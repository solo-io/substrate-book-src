"""Opt-in Chapter 4–6 regression checks on an empty, disposable book lab."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import time

import verify_deployed as lab

FULL = "SNAPSHOT_CONTENT_SCOPE_FULL"
DATA = "SNAPSHOT_CONTENT_SCOPE_DATA"


def wait_for(description, read, predicate, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read()
        if predicate(last):
            return last
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for {description}: {last}")


def ate_command(*args, manifest=None, success=True):
    result = lab.run_command(str(lab.ROOT / ".bin/kubectl-ate"), "--context", lab.CONTEXT,
                             *args, input_text=json.dumps(manifest) if manifest else None,
                             success=success)
    return result


def create(name, template="repo-workbench", tag=None):
    args = ["create", "actor", name, "--template", template, "-a", lab.NAMESPACE]
    if tag:
        args += ["--tag", tag]
    lab.ate(*args)


def state(name):
    return lab.request(f"/api/actors/{name}/state")


def advance(name, start, end):
    value = None
    for step in range(start, end):
        value = lab.request(f"/api/actors/{name}/advance", "POST", {"expectedStep": step})
        lab.check(value["currentStep"] == step + 1, "workflow step did not advance")
    return value


def lifecycle(operation, name):
    return lab.ate(operation, "actor", name, "-a", lab.NAMESPACE)


def snapshot_files(value):
    uri = value["status"]["externalSnapshot"]["snapshotUri"]
    lab.check(uri.startswith("gs://ate-snapshots/book-workbench/"), "unexpected snapshot location")
    result = lab.run_command("kubectl", "--context", lab.CONTEXT, "exec", "-n", "ate-system",
                             "deploy/rustfs", "--", "ls", uri.replace("gs://", "/data/", 1))
    return set(result.stdout.splitlines())


def configured_parking(config, budget):
    lab.check("requestParking:" not in config, "test requires the chart's default parking configuration")
    modified, count = re.subn(r"^(\s*)substrateIngress:\n",
                             lambda m: f"{m[1]}substrateIngress:\n{m[1]}  requestParking:\n{m[1]}    budget: {budget}\n",
                             config, flags=re.MULTILINE)
    lab.check(count > 0, "no Substrate ingress policies in router config")
    return modified


def set_router_config(config):
    log_args = ("kubectl", "--context", lab.CONTEXT, "logs", "deployment/atenet-router",
                "-n", "ate-system", "--tail=200")
    initial_loads = lab.run_command(*log_args).stdout.count("loaded config from")
    patch = json.dumps({"data": {"config.yaml": config}})
    lab.run_command("kubectl", "--context", lab.CONTEXT, "patch", "configmap",
                    "atenet-router-agentgateway-config", "-n", "ate-system", "--type=merge", "-p", patch)
    deadline = time.monotonic() + 180
    last_loads = initial_loads
    while time.monotonic() < deadline:
        last_loads = lab.run_command(*log_args).stdout.count("loaded config from")
        if last_loads > initial_loads:
            return
        time.sleep(2)
    raise RuntimeError(f"AgentGateway did not reload the projected config: loads={initial_loads}->{last_loads}")


def verify_capacity(first, second):
    create(first)
    create(second)
    lifecycle("resume", first)
    state(first)
    advance(first, 0, 6)
    lab.suspend(first)
    lifecycle("resume", second)
    state(second)
    advance(second, 0, 1)
    lab.suspend(second)
    lifecycle("resume", first)
    state(first)
    original_worker = lab.worker_pod()
    failure = ate_command("resume", "actor", second, "-a", lab.NAMESPACE, success=False)
    lab.check("ResourceExhausted" in failure.stderr, f"unexpected direct-resume error: {failure.stderr}")
    lab.request(f"/api/actors/{second}/state", status=504)
    lab.check(lab.actor(second)["status"]["state"] == "ACTOR_STATE_SUSPENDED",
              "capacity timeout assigned the second Actor")
    print("PASS: Chapter 4 direct resume exhausts capacity; default parked request returns 504", flush=True)

    # A longer test-only budget separates parking behavior from host restore speed.
    config = lab.kube("get", "configmap/atenet-router-agentgateway-config", "-n", "ate-system", "-o", "json")["data"]["config.yaml"]
    try:
        set_router_config(configured_parking(config, "30s"))
        def route_ready():
            try:
                return {"state": state(first)}
            except RuntimeError as error:
                return {"error": str(error)}
        wait_for("router endpoint after rollout", route_ready, lambda value: "state" in value, timeout=60)
        with ThreadPoolExecutor(max_workers=1) as executor:
            started = time.monotonic()
            parked = executor.submit(state, second)
            time.sleep(1)
            lab.suspend(first)
            resumed = parked.result(timeout=60)
        lab.check(resumed["currentStep"] == 1, "parked request lost second Actor progress")
        lab.check(time.monotonic() - started >= 1, "request did not wait for capacity")
        lab.check(lab.worker_pod() == original_worker, "parking replaced the Worker Pod")
        print("PASS: Chapter 4 original parked request succeeds after capacity release (test budget 30s)", flush=True)
    finally:
        set_router_config(config)
    lab.suspend(second)


def verify_snapshots(first, third, fourth, template, tag):
    files = snapshot_files(lab.actor(first))
    lab.check(files == {"checkpoint.img.zstd", "durable-dir-workspace.tar.zstd", "manifest.json",
                        "pages.img.zstd", "pages_meta.img.zstd"}, f"Full snapshot contents: {files}")
    lifecycle("resume", first)
    before_full_pause = state(first)
    lifecycle("pause", first)
    full_paused = lab.actor(first)["status"]
    lab.check(full_paused["state"] == "ACTOR_STATE_PAUSED" and
              full_paused["localSnapshot"]["contentScope"] == FULL and
              not full_paused.get("workerAssignment"),
              "Full pause did not preserve memory or release the Worker")
    lifecycle("resume", first)
    after_full_pause = state(first)
    for key in ("currentStep", "stateRevision", "transcript", "tests", "files", "diff"):
        lab.check(after_full_pause[key] == before_full_pause[key],
                  f"{key} changed across Full pause/resume")
    lab.suspend(first)

    definition = lab.ate("get", "actor-template", "repo-workbench", "-a", lab.NAMESPACE)
    definition.pop("status", None)
    definition["metadata"] = {"atespace": lab.NAMESPACE, "name": template}
    definition["snapshotConfig"]["onCommit"] = DATA
    ate_command("create", "actor-template", "-f", "-", manifest=definition)
    wait_for("Data template golden snapshot",
             lambda: lab.ate("get", "actor-template", template, "-a", lab.NAMESPACE),
             lambda t: bool(t.get("status", {}).get("goldenSnapshotStatus", {}).get("goldenTag", {}).get("name")),
             timeout=300)
    create(third, template=template)
    lifecycle("resume", third)
    state(third)
    repaired = advance(third, 0, 4)
    lab.suspend(third)
    lab.check(snapshot_files(lab.actor(third)) == {"durable-dir-workspace.tar.zstd", "manifest.json"},
              "Data snapshot captured memory or omitted durable data")
    lifecycle("resume", third)
    restored = state(third)
    source = lambda s: next(f for f in s["files"] if f["path"] == "src/slugify.py")
    lab.check(restored["currentStep"] == 0, "Data restore retained process progress")
    lab.check(source(restored) == source(repaired), "Data restore lost the repair")
    lab.check(len(restored["transcript"]) < len(repaired["transcript"]), "Data restore retained the old transcript")
    advance(third, 0, 1)
    lifecycle("pause", third)
    paused = lab.actor(third)["status"]
    lab.check(paused["state"] == "ACTOR_STATE_PAUSED" and not paused.get("workerAssignment"),
              "pause did not release the Worker")
    lab.check(paused["localSnapshot"]["contentScope"] == DATA, "Data pause captured process memory")
    lifecycle("resume", third)
    restored_after_pause = state(third)
    lab.check(restored_after_pause["currentStep"] == 0, "Data pause retained process progress")
    lab.check(source(restored_after_pause) == source(repaired), "Data pause lost the durable repair")
    lab.check(len(restored_after_pause["transcript"]) < len(repaired["transcript"]),
              "Data pause retained the old transcript")
    lifecycle("pause", third)
    lifecycle("suspend", third)
    suspended = lab.actor(third)["status"]
    lab.check(not suspended.get("localSnapshot") and suspended["externalSnapshot"]["contentScope"] == DATA,
              "paused-to-suspended conversion did not commit only Data")
    print("PASS: Chapter 5 Full pause preserves memory; Data pause/suspend preserves files and resets memory", flush=True)

    lab.ate("create", "tag", tag, "--actor", first, "-a", lab.NAMESPACE)
    create(fourth, tag=tag)
    record = lab.actor(fourth)
    lab.check(record["sourceTag"]["name"] == tag, "fork did not reference the saved tag")
    lifecycle("resume", fourth)
    lab.check(state(fourth)["currentStep"] == 6, "tag fork did not restore saved step 6")
    lab.suspend(fourth)
    fork_snapshot = lab.actor(fourth)["status"]["externalSnapshot"]["snapshotUri"]
    ate_command("delete", "actor", fourth, "-a", lab.NAMESPACE)
    listing = lab.run_command("kubectl", "--context", lab.CONTEXT, "exec", "-n", "ate-system",
                              "deploy/rustfs", "--", "find", "/data/ate-snapshots/book-workbench", "-name", "manifest.json").stdout
    lab.check(fork_snapshot.replace("gs://", "/data/", 1) + "/manifest.json" not in listing,
              "deleted Actor still owns its snapshot")
    print("PASS: Chapter 5 tag forks saved progress and Actor deletion removes its snapshot", flush=True)


def verify_identity_and_recovery(first):
    before = lab.actor(first)
    uid = before["metadata"]["uid"]
    restored = state(first)
    lab.check(restored["currentStep"] == 6, "recovery test needs saved step 6")
    advance(first, 6, 7)
    assignment = lab.actor(first)["status"]["workerAssignment"]
    old_pod_uid = lab.worker_pod()
    lab.run_command("kubectl", "--context", lab.CONTEXT, "delete", "pod", assignment["workerPod"],
                    "-n", lab.NAMESPACE, "--timeout=180s")
    crashed = wait_for("Actor crash after Worker loss", lambda: lab.actor(first),
                       lambda a: a["status"]["state"] == "ACTOR_STATE_CRASHED")
    lab.check(bool(crashed["status"].get("crash", {}).get("message")), "crash has no recorded cause")
    lab.run_command("kubectl", "--context", lab.CONTEXT, "rollout", "status", "deployment/generic-worker",
                    "-n", lab.NAMESPACE, "--timeout=180s")
    lifecycle("revert", first)
    lifecycle("resume", first)
    recovered = state(first)
    lab.check(recovered["currentStep"] == 6, "recovery retained unsaved step 7 or lost saved step 6")
    lab.check(lab.actor(first)["metadata"]["uid"] == uid, "recovery changed Actor UID")
    lab.check(lab.worker_pod() != old_pod_uid, "recovery did not replace the Worker Pod")
    lab.suspend(first)
    suspended = lab.actor(first)
    lab.check(suspended["metadata"]["uid"] == uid and not suspended["status"].get("workerAssignment"),
              "suspend changed identity or retained placement")
    policy = {"rules": [{"http": {"hostnames": ["git.internal.example.com"]}}]}
    ate_command("create", "egress-policy", first, "-a", lab.NAMESPACE, "-f", "-", manifest=policy)
    actual = lab.ate("get", "egress-policy", first, "-a", lab.NAMESPACE)
    lab.check(actual["rules"][0]["http"]["ports"]["numbers"] == [80], "HTTP rule default port changed")
    effect = {"replaceHeaders": [{"header": "Authorization", "prefix": "Bearer ",
                                  "credentialUri": "ate-secret://k8s.io/default/workbench-secrets/git-token/token"}]}
    actual["rules"][0]["http"]["effects"] = effect
    ate_command("update", "egress-policy", first, "-a", lab.NAMESPACE, "-f", "-", manifest=actual)
    updated = lab.ate("get", "egress-policy", first, "-a", lab.NAMESPACE)
    lab.check(updated["rules"][0]["http"]["effects"] == effect, "credential replacement effect changed")
    print("PASS: Chapters 4/6 crash recovery restores durable progress on a replacement Worker; UID, HTTP policy, and credential effect admission", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-worker-replacement", action="store_true",
                        help="acknowledge that this test deletes the lab Worker Pod")
    args = parser.parse_args()
    if not args.allow_worker_replacement:
        parser.error("pass --allow-worker-replacement only for a disposable lab")
    lab.check(lab.CONTEXT.startswith("kind-"), "chapter verification requires a kind test cluster")
    lab.check(not lab.ate("get", "actors", "-a", lab.NAMESPACE).get("actors"), "chapter verification requires an empty Atespace")
    first, second, third, fourth = [f"repo-workbench-{i}" for i in range(1, 5)]
    template, tag = "repo-workbench-data", "step-6-review"
    lab.check(not lab.ate("get", "tags", "-a", lab.NAMESPACE).get("tags"), "chapter verification requires no existing tags")
    templates = lab.ate("get", "actor-templates", "-a", lab.NAMESPACE)
    lab.check(all(t["metadata"]["name"] == "repo-workbench" for t in templates.get("actorTemplates", [])),
              "chapter verification requires only the original template")
    try:
        verify_capacity(first, second)
        verify_snapshots(first, third, fourth, template, tag)
        verify_identity_and_recovery(first)
    finally:
        # Delete only the names this suite reserves in an initially empty lab.
        failures = []
        for name in (fourth, third, second, first):
            result = ate_command("delete", "actor", name, "-a", lab.NAMESPACE, "--any-state", success=None)
            if result.returncode and "NotFound" not in result.stderr:
                failures.append(result.stderr)
        for resource, name in (("tag", tag), ("actor-template", template)):
            result = ate_command("delete", resource, name, "-a", lab.NAMESPACE, success=None)
            if result.returncode and "NotFound" not in result.stderr:
                failures.append(result.stderr)
        lab.check(not failures, f"chapter cleanup failed: {failures}")
    lab.check(not lab.ate("get", "actors", "-a", lab.NAMESPACE).get("actors"), "chapter test Actors remain")
    print("PASS: chapter verification leaves the Atespace empty", flush=True)


if __name__ == "__main__":
    main()
