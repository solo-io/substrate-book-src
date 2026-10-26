"""Opt-in Chapter 3–6 regression checks on an empty, disposable book lab."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import time

import verify_deployed as lab

MEMORY = "SNAPSHOT_FIDELITY_MEMORY"
VOLUMES = "SNAPSHOT_FIDELITY_VOLUMES"
PENDING = "SNAPSHOT_STORAGE_STATUS_PENDING"
COMPLETED = "SNAPSHOT_STORAGE_STATUS_COMPLETED"
ROUTER_ASSIGNMENT_CACHE_SECONDS = 6


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


def lifecycle(operation, name, mode=None):
    args = [operation, "actor", name, "-a", lab.NAMESPACE]
    if operation == "suspend":
        args += ["--mode", mode or "sync"]
    return lab.ate(*args)


def snapshots(value):
    status = value.get("status", value)
    return status.get("snapshots", [])


def durable_snapshot(value):
    completed = [snapshot for snapshot in snapshots(value)
                 if snapshot.get("durableSnapshot", {}).get("status") == COMPLETED]
    lab.check(completed, "Actor has no completed durable snapshot")
    return max(completed, key=lambda snapshot: snapshot["generation"])


def local_snapshot(value):
    local = [snapshot for snapshot in snapshots(value) if snapshot.get("locality")]
    lab.check(local, "Actor has no local snapshot")
    return max(local, key=lambda snapshot: snapshot["generation"])


def snapshot_uri(value):
    return durable_snapshot(value)["durableSnapshot"]["object"]["snapshotUri"]


def snapshot_object_names(path, output):
    objects = set()
    for name in output.splitlines():
        parts = name.removeprefix(path + "/").split("/")
        for index, part in enumerate(parts):
            if part == "manifest.json" or part.endswith(".zstd"):
                objects.add("/".join(parts[:index + 1]))
                break
    return objects


def snapshot_files(value):
    uri = snapshot_uri(value)
    lab.check(uri.startswith("gs://ate-snapshots/atespaces/book-workbench/"),
              "unexpected snapshot location")
    path = uri.replace("gs://", "/data/", 1)
    result = lab.run_command("kubectl", "--context", lab.CONTEXT, "exec", "-n", "ate-system",
                             "deploy/rustfs", "--", "find", path, "-type", "f")
    return snapshot_object_names(path, result.stdout)


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


def restart_atelet():
    pods = lab.kube("get", "pods", "-n", "ate-system", "-l", "app=atelet", "-o", "json")["items"]
    lab.check(len(pods) == 1, f"expected one atelet Pod, found {len(pods)}")
    old_uid = pods[0]["metadata"]["uid"]
    lab.run_command("kubectl", "--context", lab.CONTEXT, "delete", "pod",
                    pods[0]["metadata"]["name"], "-n", "ate-system", "--timeout=180s")
    lab.run_command("kubectl", "--context", lab.CONTEXT, "rollout", "status", "daemonset/atelet",
                    "-n", "ate-system", "--timeout=180s")
    replacement = wait_for(
        "replacement atelet Pod",
        lambda: lab.kube("get", "pods", "-n", "ate-system", "-l", "app=atelet", "-o", "json")["items"],
        lambda items: len(items) == 1 and items[0]["metadata"]["uid"] != old_uid and
        all(status["ready"] for status in items[0]["status"].get("containerStatuses", [])),
    )[0]
    lab.check(replacement["status"]["phase"] == "Running", "replacement atelet Pod is not running")


def verify_chapter3(first, second):
    deployment = lab.kube("get", "deployment/ate-api-server", "-n", "ate-system", "-o", "json")
    lock = json.loads((lab.ROOT / "scripts/runtime-lock.json").read_text())
    expected_api = lock["images"]["ghcr.io/kagent-dev/substrate/ateapi:" + os.environ["SUBSTRATE_VERSION"]]
    lab.check(deployment["spec"]["template"]["spec"]["containers"][0]["image"] == expected_api,
              "installed runtime does not match the locked release digest")

    pool = lab.kube("get", "workerpool", "generic-worker", "-n", lab.NAMESPACE, "-o", "json")
    resolved = pool.get("status", {}).get("sandboxClasses", [])
    lab.check(pool["spec"]["replicas"] == 1 and pool["status"]["replicas"] == 1 and
              pool["status"]["readyReplicas"] == 1, "Chapter 3 requires one ready Worker")
    lab.check(pool["spec"]["sandboxClasses"] == [{"name": "gvisor", "configRef": {"name": "gvisor-default"}}],
              "WorkerPool does not select the release gVisor SandboxConfig")
    lab.check(len(resolved) == 1 and resolved[0]["configRef"]["name"] == "gvisor-default" and
              any(c["type"] == "Resolved" and c["status"] == "True" for c in resolved[0]["conditions"]),
              "WorkerPool sandbox class is not resolved")

    pods = lab.kube("get", "pods", "-n", lab.NAMESPACE,
                    "-l", "ate.dev/worker-pool=generic-worker", "-o", "json")["items"]
    lab.check(len(pods) == 1 and pods[0]["status"]["phase"] == "Running" and
              all(status["ready"] for status in pods[0]["status"]["containerStatuses"]),
              "Chapter 3 requires one ready Worker Pod")
    workers = lab.ate("get", "workers", "-n", lab.NAMESPACE).get("workers", [])
    lab.check(len(workers) == 1 and workers[0]["status"]["state"] == "WORKER_STATE_ACTIVE",
              "Substrate does not report one active Worker")
    capacity = workers[0]["status"]["capacity"]
    lab.check(capacity == {"cpuMillicores": "1000", "memoryBytes": "1073741824", "actors": 1000},
              "Worker capacity differs from the Chapter 3 lab")

    template = lab.ate("get", "actor-template", "repo-workbench", "-a", lab.NAMESPACE)
    lab.check("sandboxConfig" not in template and
              template["workerSelector"]["matchLabels"] == {"workload": "generic-worker"} and
              template["resources"]["limits"] == {"cpuMillicores": "1000", "memoryBytes": "536870912"},
              "ActorTemplate placement, sandbox ownership, or resource requirements changed")
    lab.check(template["snapshotConfig"]["preferredFidelity"] == MEMORY and
              bool(template.get("status", {}).get("goldenSnapshotStatus", {}).get("goldenTag", {}).get("name")),
              "ActorTemplate MEMORY golden snapshot is not ready")
    print("PASS: Chapter 3 starts with the locked runtime, one ready gVisor Worker, and a ready MEMORY template",
          flush=True)

    create(first)
    create(second)
    records = {actor["metadata"]["name"]: actor for actor in
               lab.ate("get", "actors", "-a", lab.NAMESPACE).get("actors", [])}
    lab.check(set(records) == {first, second} and
              all(actor["status"]["state"] == "ACTOR_STATE_SUSPENDED" and
                  not actor["status"].get("workerAssignment") for actor in records.values()),
              "new Chapter 3 Actors are not suspended and unassigned")
    original_worker = lab.worker_pod()

    lifecycle("resume", first)
    first_initial = state(first)
    lab.check(first_initial["currentStep"] == 0 and
              first_initial["actor"]["name"] == first and
              first_initial["actor"]["atespace"] == lab.NAMESPACE,
              "first Actor did not start at step 0 with projected identity")
    first_assignment = lab.actor(first)["status"]["workerAssignment"]
    lab.check(first_assignment["workerPodUid"] == original_worker,
              "first Actor was not assigned to the prepared Worker")
    first_at_two = advance(first, 0, 2)
    lab.check("FAILED" in first_at_two["tests"], "step 2 did not reproduce the repository failure")
    lifecycle("suspend", first)

    lifecycle("resume", second)
    second_initial = state(second)
    lab.check(second_initial["currentStep"] == 0 and
              second_initial["actor"]["uid"] != first_at_two["actor"]["uid"],
              "second Actor inherited the first Actor's state or identity")
    second_assignment = lab.actor(second)["status"]["workerAssignment"]
    lab.check(second_assignment["workerPodUid"] == original_worker,
              "second Actor did not reuse the same Worker")
    second_at_one = advance(second, 0, 1)
    lifecycle("suspend", second)

    lifecycle("resume", first)
    restored = state(first)
    for key in ("actor", "currentStep", "stateRevision", "transcript", "tests", "files", "diff"):
        lab.check(restored[key] == first_at_two[key], f"{key} changed across the Chapter 3 handoff")
    first_at_three = advance(first, 2, 3)
    lab.check(first_at_three["currentStep"] == 3, "first Actor did not continue from restored step 2")
    print("PASS: Chapter 3 CLI walkthrough keeps independent state while two Actors reuse one Worker",
          flush=True)

    # Exercise the section 3.7 state transitions through the backend while deliberately skipping the browser UI.
    first_at_four = advance(first, 3, 4)
    source = lambda value: next(item for item in value["files"] if item["path"] == "src/slugify.py")
    lab.check(source(first_at_four)["status"] == "modified", "step 4 did not repair src/slugify.py")
    lab.request(f"/api/actors/{first}/advance", "POST", {"expectedStep": 3}, status=409)
    lifecycle("suspend", first)
    lifecycle("resume", second)
    second_restored = state(second)
    lab.check(second_restored["currentStep"] == second_at_one["currentStep"] and
              source(second_restored)["status"] == "unchanged",
              "second Actor inherited the first Actor's repaired workspace")
    lifecycle("suspend", second)
    lifecycle("resume", first)
    first_restored = state(first)
    lab.check(first_restored["currentStep"] == 4 and source(first_restored)["status"] == "modified",
              "first Actor lost its repaired workspace")
    first_at_six = advance(first, 4, 6)
    files = {item["path"]: item for item in first_at_six["files"]}
    lab.check(files["AGENT_REPORT.md"]["status"] == "added" and "OK" in first_at_six["tests"],
              "Chapter 3 did not reach step 6 with a report and passing tests")
    final_records = {actor["metadata"]["name"]: actor for actor in
                     lab.ate("get", "actors", "-a", lab.NAMESPACE).get("actors", [])}
    lab.check(final_records[first]["status"]["state"] == "ACTOR_STATE_RUNNING" and
              final_records[second]["status"]["state"] == "ACTOR_STATE_SUSPENDED" and
              lab.worker_pod() == original_worker,
              "Chapter 3 did not leave the state required by Chapter 4")
    print("PASS: Chapter 3 backend path reaches step 6/step 1 and preserves the Chapter 4 handoff (UI skipped)",
          flush=True)


def verify_capacity(first, second):
    lab.check(state(first)["currentStep"] == 6 and
              lab.actor(second)["status"]["state"] == "ACTOR_STATE_SUSPENDED",
              "Chapter 4 did not receive the Chapter 3 step 6/step 1 handoff")
    original_worker = lab.worker_pod()
    failure = ate_command("resume", "actor", second, "-a", lab.NAMESPACE, success=False)
    lab.check("ResourceExhausted" in failure.stderr, f"unexpected direct-resume error: {failure.stderr}")
    lab.request(f"/api/actors/{second}/state", status=503)
    lab.check(lab.actor(second)["status"]["state"] == "ACTOR_STATE_SUSPENDED",
              "capacity timeout assigned the second Actor")
    print("PASS: Chapter 4 direct resume exhausts capacity; default parked request returns 503", flush=True)

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
            lifecycle("suspend", first)
            resumed = parked.result(timeout=60)
        lab.check(resumed["currentStep"] == 1, "parked request lost second Actor progress")
        lab.check(time.monotonic() - started >= 1, "request did not wait for capacity")
        lab.check(lab.worker_pod() == original_worker, "parking replaced the Worker Pod")
        print("PASS: Chapter 4 original parked request succeeds after capacity release (test budget 30s)", flush=True)
    finally:
        set_router_config(config)


def verify_recovery(second):
    before = lab.actor(second)
    uid = before["metadata"]["uid"]
    lab.check(before["status"]["state"] == "ACTOR_STATE_RUNNING" and state(second)["currentStep"] == 1,
              "Chapter 4 recovery requires the second Actor running at saved step 1")
    advance(second, 1, 2)
    assignment = lab.actor(second)["status"]["workerAssignment"]
    old_pod_uid = lab.worker_pod()
    lab.run_command("kubectl", "--context", lab.CONTEXT, "delete", "pod", assignment["workerPod"],
                    "-n", lab.NAMESPACE, "--timeout=180s")
    crashed = wait_for("Actor crash after Worker loss", lambda: lab.actor(second),
                       lambda actor: actor["status"]["state"] == "ACTOR_STATE_CRASHED")
    lab.check(bool(crashed["status"].get("crash", {}).get("message")), "crash has no recorded cause")
    lab.run_command("kubectl", "--context", lab.CONTEXT, "rollout", "status", "deployment/generic-worker",
                    "-n", lab.NAMESPACE, "--timeout=180s")
    replacement_pod_uid = lab.worker_pod()
    wait_for(
        "replacement Worker registration",
        lambda: lab.ate("get", "workers", "-n", lab.NAMESPACE).get("workers", []),
        lambda workers: len(workers) == 1 and
        workers[0]["metadata"]["name"] == replacement_pod_uid and
        workers[0]["status"]["state"] == "WORKER_STATE_ACTIVE",
    )
    lifecycle("revert", second)
    lifecycle("resume", second)
    # AgentGateway caches an Actor's Worker address for 5s and evicts it early only
    # when a live Worker rejects the request. A deleted Worker can't, so a request
    # sent faster than a reader would type still dials the old Pod and returns 503.
    time.sleep(ROUTER_ASSIGNMENT_CACHE_SECONDS)
    recovered = state(second)
    lab.check(recovered["currentStep"] == 1, "recovery retained unsaved step 2 or lost saved step 1")
    lab.check(lab.actor(second)["metadata"]["uid"] == uid, "recovery changed Actor UID")
    lab.check(replacement_pod_uid != old_pod_uid, "recovery did not replace the Worker Pod")
    print("PASS: Chapter 4 Worker loss crashes the running Actor; revert/resume restores saved step 1",
          flush=True)


def verify_snapshots(first, second, third, fourth, template, tag):
    files = snapshot_files(lab.actor(first))
    lab.check(files == {"checkpoint.img.zstd", "fs/fscheckpoint.pb.zstd", "fs/multitar.img.zstd",
                        "fs/pages.img.zstd", "fs/pages_meta.img.zstd", "manifest.json",
                        "pages.img.zstd", "pages_meta.img.zstd",
                        "rootfs/fscheckpoint.pb.zstd", "rootfs/multitar.img.zstd",
                        "rootfs/pages.img.zstd", "rootfs/pages_meta.img.zstd"},
              f"MEMORY snapshot contents: {files}")
    memory_snapshot = durable_snapshot(lab.actor(first))
    lab.check(memory_snapshot["fidelity"] == MEMORY and memory_snapshot["generation"] >= 1 and
              bool(memory_snapshot.get("uuid")), "MEMORY snapshot status is incomplete")
    lifecycle("suspend", second)
    lifecycle("resume", first)
    before_memory_async = state(first)
    lifecycle("suspend", first, mode="async")
    memory_suspended = lab.actor(first)["status"]
    local = local_snapshot(memory_suspended)
    lab.check(memory_suspended["state"] == "ACTOR_STATE_SUSPENDED" and
              local["fidelity"] == MEMORY and
              local.get("durableSnapshot", {}).get("status") == PENDING and
              bool(local.get("durableSnapshot", {}).get("object", {}).get("snapshotUri")) and
              memory_suspended.get("assignedNode") == local["locality"] and
              not memory_suspended.get("workerAssignment"),
              "asynchronous MEMORY suspend did not leave a pending local snapshot")
    restart_atelet()
    recovered_local = local_snapshot(lab.actor(first))
    lab.check(recovered_local["generation"] == local["generation"] and
              recovered_local["locality"] == local["locality"] and
              recovered_local.get("durableSnapshot", {}).get("status") == PENDING,
              "atelet restart lost or replaced the reported local snapshot")
    lifecycle("resume", first)
    after_memory_async = state(first)
    for key in ("currentStep", "stateRevision", "transcript", "tests", "files", "diff"):
        lab.check(after_memory_async[key] == before_memory_async[key],
                  f"{key} changed across asynchronous MEMORY suspend/resume")
    lifecycle("suspend", first)
    lab.check(durable_snapshot(lab.actor(first))["fidelity"] == MEMORY,
              "synchronous MEMORY suspend did not complete durable storage")

    definition = lab.ate("get", "actor-template", "repo-workbench", "-a", lab.NAMESPACE)
    definition.pop("status", None)
    definition["metadata"] = {"atespace": lab.NAMESPACE, "name": template}
    definition["snapshotConfig"]["preferredFidelity"] = VOLUMES
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
    lab.check(snapshot_files(lab.actor(third)) == {
        "fs/fscheckpoint.pb.zstd", "fs/multitar.img.zstd", "fs/pages.img.zstd",
        "fs/pages_meta.img.zstd", "manifest.json"},
        "VOLUMES snapshot captured memory or omitted durable data")
    lab.check(durable_snapshot(lab.actor(third))["fidelity"] == VOLUMES,
              "VOLUMES suspend recorded the wrong fidelity")
    lifecycle("resume", third)
    restored = state(third)
    source = lambda s: next(f for f in s["files"] if f["path"] == "src/slugify.py")
    lab.check(restored["currentStep"] == 0, "VOLUMES restore retained process progress")
    lab.check(source(restored) == source(repaired), "VOLUMES restore lost the repair")
    lab.check(len(restored["transcript"]) < len(repaired["transcript"]),
              "VOLUMES restore retained the old transcript")
    advance(third, 0, 1)
    lifecycle("suspend", third, mode="async")
    async_status = lab.actor(third)["status"]
    async_local = local_snapshot(async_status)
    lab.check(async_status["state"] == "ACTOR_STATE_SUSPENDED" and
              async_status.get("assignedNode") == async_local["locality"] and
              not async_status.get("workerAssignment") and
              async_local.get("durableSnapshot", {}).get("status") == PENDING,
              "asynchronous suspend did not release the Worker with a pending local snapshot")
    lab.check(async_local["fidelity"] == VOLUMES,
              "asynchronous VOLUMES suspend captured process memory")
    lifecycle("resume", third)
    restored_after_async = state(third)
    lab.check(restored_after_async["currentStep"] == 0, "VOLUMES async suspend retained process progress")
    lab.check(source(restored_after_async) == source(repaired), "VOLUMES async suspend lost the durable repair")
    lab.check(len(restored_after_async["transcript"]) < len(repaired["transcript"]),
              "VOLUMES async suspend retained the old transcript")
    lifecycle("suspend", third)
    sync_status = lab.actor(third)["status"]
    sync_local = local_snapshot(sync_status)
    lab.check(sync_status.get("assignedNode") == sync_local["locality"] and
              not sync_status.get("workerAssignment") and
              sync_local.get("durableSnapshot", {}).get("status") == COMPLETED and
              durable_snapshot(sync_status)["fidelity"] == VOLUMES,
              "synchronous VOLUMES suspend did not retain a completed local snapshot")
    print("PASS: Chapter 5 async MEMORY state survives atelet restart; async/sync VOLUMES suspends preserve files and reset memory", flush=True)

    lab.ate("create", "tag", tag, "--actor", first, "-a", lab.NAMESPACE)
    create(fourth, tag=tag)
    record = lab.actor(fourth)
    lab.check(record["sourceTag"]["name"] == tag, "fork did not reference the saved tag")
    lifecycle("resume", fourth)
    lab.check(state(fourth)["currentStep"] == 6, "tag fork did not restore saved step 6")
    lab.suspend(fourth)
    fork_snapshot = snapshot_uri(lab.actor(fourth))
    ate_command("delete", "actor", fourth, "-a", lab.NAMESPACE)
    object_root = "/data/ate-snapshots"
    listing = lab.run_command("kubectl", "--context", lab.CONTEXT, "exec", "-n", "ate-system",
                              "deploy/rustfs", "--", "find", object_root, "-type", "f").stdout
    objects = snapshot_object_names(object_root, listing)
    fork_prefix = fork_snapshot.replace("gs://", "/data/", 1).removeprefix(object_root + "/")
    fork_manifest = fork_prefix + "/manifest.json"
    lab.check(fork_manifest not in objects,
              "deleted Actor still owns its snapshot")
    print("PASS: Chapter 5 tag forks saved progress and Actor deletion removes its snapshot", flush=True)

    ate_command("delete", "actor", third, "-a", lab.NAMESPACE)
    ate_command("delete", "tag", tag, "-a", lab.NAMESPACE)
    ate_command("delete", "actor-template", template, "-a", lab.NAMESPACE)
    lifecycle("resume", second)
    lab.check(state(second)["currentStep"] == 1,
              "Chapter 5 reset did not restore the second Actor at step 1")
    print("PASS: Chapter 5 reset leaves actor 1 suspended and actor 2 running for Chapter 6", flush=True)


def publish(name, assert_actor=False):
    return lab.request(f"/api/actors/{name}/publish", "POST", {"assertActor": assert_actor})


def publish_until(name, description, predicate, assert_actor=False, timeout=90):
    # The DNS relay and gateway pick up a new policy asynchronously; retry on
    # the Actor's own outcome, never on transport errors from the lab bridge.
    return wait_for(description, lambda: publish(name, assert_actor)["publish"], predicate, timeout=timeout)


def set_policy(name, rules):
    # Chapter 6 replaces a policy by deleting it and creating it again.
    existing = lab.run_command(str(lab.ROOT / ".bin/kubectl-ate"), "--context", lab.CONTEXT, "get",
                               "egress-policy", name, "-a", lab.NAMESPACE, "-o", "json", success=None)
    if not existing.returncode and existing.stdout.lstrip().startswith("{"):
        ate_command("delete", "egress-policy", name, "-a", lab.NAMESPACE)
    ate_command("create", "egress-policy", name, "-a", lab.NAMESPACE, "-f", "-", manifest={"rules": rules})
    return lab.ate("get", "egress-policy", name, "-a", lab.NAMESPACE)


SOURCE_CONTROL_HOST = "source-control.book-workbench.svc.cluster.local"
GRANTED_URI = "ate-secret://k8s.io/default/workbench-secrets/git-token/token"
UNGRANTED_URI = "ate-secret://k8s.io/default/book-workbench/source-control-token/token"
NAMESPACE_POLICY = 'policies: [{"allowedNamespaces":["workbench-secrets"],"atespace":"book-workbench"}]'
DNS_DENIED = "Name or service not known"
NOT_PERMITTED = 'atespace "book-workbench" is not permitted to resolve secrets in namespace "book-workbench"'


def account_token_fingerprint():
    import base64
    import hashlib
    encoded = lab.run_command("kubectl", "--context", lab.CONTEXT, "get", "secret", "git-token",
                              "-n", "workbench-secrets", "-o", "jsonpath={.data.token}").stdout
    return hashlib.sha256(base64.b64decode(encoded)).hexdigest()[:16]


def dns_denied(outcome):
    return outcome["status"] is None and DNS_DENIED in (outcome["error"] or "")


def verify_identity_and_policy(first, second):
    before = lab.actor(first)
    uid = before["metadata"]["uid"]
    lab.check(lab.actor(second)["status"]["state"] == "ACTOR_STATE_RUNNING",
              "Chapter 6 requires the second Actor to start running")
    lifecycle("suspend", second)
    lifecycle("resume", first)
    restored = state(first)
    running = lab.actor(first)
    lab.check(restored["currentStep"] == 6 and restored["actor"]["uid"] == uid and
              running["status"]["state"] == "ACTOR_STATE_RUNNING" and
              bool(running["status"]["workerAssignment"]["workerPod"]),
              "Chapter 6 identity did not survive activation")
    lifecycle("suspend", first)
    suspended = lab.actor(first)
    lab.check(suspended["metadata"]["uid"] == uid and not suspended["status"].get("workerAssignment"),
              "suspend changed identity or retained placement")
    print("PASS: Chapter 6.2 Actor UID survives placement changes", flush=True)

    # Section 6.3: the Actor only ever sends placeholder headers. A fresh lab
    # starts with no pull requests; count from the existing ones on a reused lab.
    existing_pulls = len(lab.request("/api/source-control/pulls")["pulls"])
    lifecycle("resume", first)
    lab.check(state(first)["canPublish"], "Actor at step 6 cannot publish its report")
    denied = publish(first)["publish"]
    lab.check(dns_denied(denied), f"publish without a policy was not denied at DNS: {denied}")
    lab.check(denied["sentHeaders"] == {"Authorization": "placeholder"}, "Actor sent a real credential")
    print("PASS: Chapter 6.3 publish without a policy fails name resolution", flush=True)

    actual = set_policy(first, [{"http": {"hostnames": [SOURCE_CONTROL_HOST]}}])
    lab.check(actual["rules"][0]["http"]["ports"]["numbers"] == [80], "HTTP rule default port changed")
    allowed = publish_until(first, "hostname rule to allow the request",
                            lambda outcome: outcome["status"] is not None)
    lab.check(allowed["status"] == 401 and allowed["response"] == {
        "error": "Authorization header is not a bearer token", "tokenFingerprint": None},
        f"allowed request without a credential was not rejected as in the chapter: {allowed}")
    print("PASS: Chapter 6.3 hostname rule reaches the service, which rejects the placeholder with 401", flush=True)

    # Section 6.4: the provider grant rendered at install time.
    grant = lab.run_command("kubectl", "--context", lab.CONTEXT, "get", "configmap",
                            "k8s-credential-provider-namespace-policy", "-n", "ate-system",
                            "-o", r"jsonpath={.data.namespace-policy\.yaml}").stdout.strip()
    lab.check(grant == NAMESPACE_POLICY, f"provider grant differs from the chapter: {grant}")

    effect = {"replaceHeaders": [{"header": "Authorization", "prefix": "Bearer ", "credentialUri": GRANTED_URI}]}
    actual = set_policy(first, [{"http": {"hostnames": [SOURCE_CONTROL_HOST], "effects": effect}}])
    lab.check(actual["rules"][0]["http"]["effects"] == effect, "credential replacement effect changed")
    injected = publish_until(first, "credential injection", lambda outcome: outcome["status"] == 201)
    lab.check(injected["sentHeaders"] == {"Authorization": "placeholder"}, "Actor saw the injected credential")
    lab.check(injected["response"] == {"number": existing_pulls + 1, "repo": "repo-workbench",
                                       "authenticatedAs": "repo-workbench-bot", "actor": None},
              f"service response differs from the chapter: {injected}")
    pulls = lab.request("/api/source-control/pulls")["pulls"]
    lab.check(len(pulls) == existing_pulls + 1 and pulls[-1]["authenticatedAs"] == "repo-workbench-bot" and
              pulls[-1]["tokenFingerprint"] == account_token_fingerprint() and
              pulls[-1]["actor"] is None and "# Agent report" in pulls[-1]["body"],
              f"service record does not match the Secret: {pulls[-1:]}")
    lab.check("placeholder" not in json.dumps(injected["response"]), "placeholder reached the service")
    print("PASS: Chapter 6.4 gateway injects the granted Secret as the next pull request; fingerprints match",
          flush=True)

    ungranted = {"replaceHeaders": [{"header": "Authorization", "prefix": "Bearer ", "credentialUri": UNGRANTED_URI}]}
    set_policy(first, [{"http": {"hostnames": [SOURCE_CONTROL_HOST], "effects": ungranted}}])
    refused = publish_until(first, "provider refusal", lambda outcome: outcome["status"] == 403)
    # The gateway returns the provider message as text with escaped quotes.
    lab.check(NOT_PERMITTED in json.dumps(refused["response"]).replace("\\", ""),
              f"403 does not carry the chapter's provider message: {refused}")
    lab.check(len(lab.request("/api/source-control/pulls")["pulls"]) == len(pulls),
              "a refused credential request still reached the service")
    print("PASS: Chapter 6.4 provider refuses a Secret outside the Atespace grant and the gateway fails closed",
          flush=True)

    # Section 6.5: an Actor JWT alongside the account token.
    asserted = {"replaceHeaders": [
        {"header": "Authorization", "prefix": "Bearer ", "credentialUri": GRANTED_URI},
        {"header": "Actor-Assertion", "actorJwt": {"audiences": [SOURCE_CONTROL_HOST], "expirationSeconds": 600}},
    ]}
    actual = set_policy(first, [{"http": {"hostnames": [SOURCE_CONTROL_HOST], "effects": asserted}}])
    # The readback renders the int64 lifetime as a JSON string.
    admitted = json.loads(json.dumps(actual["rules"][0]["http"]["effects"]).replace('"600"', "600"))
    lab.check(admitted == asserted, f"actorJwt effect changed at admission: {actual['rules'][0]}")
    with_actor = publish_until(first, "Actor JWT injection", lambda outcome: outcome["status"] == 201,
                               assert_actor=True)
    lab.check(with_actor["sentHeaders"] == {"Authorization": "placeholder", "Actor-Assertion": "placeholder"},
              "Actor saw its own assertion")
    lab.check(with_actor["response"] == {
        "number": existing_pulls + 2, "repo": "repo-workbench", "authenticatedAs": "repo-workbench-bot",
        "actor": {"subject": f"actor/{lab.NAMESPACE}/{first}", "atespace": lab.NAMESPACE,
                  "name": first, "uid": uid}},
        f"service did not verify the Actor JWT as the following pull request: {with_actor}")
    unverified = publish_until(first, "placeholder assertion without the effect",
                               lambda outcome: outcome["status"] == 201, assert_actor=False)
    lab.check(unverified["response"]["actor"] is None and
              unverified["response"]["authenticatedAs"] == "repo-workbench-bot",
              "assertion was injected without the header present")
    print("PASS: Chapter 6.5 gateway mints and injects an Actor JWT the service verifies against the IdP",
          flush=True)

    # "The policy follows the Actor": the second Actor has no policy.
    lifecycle("suspend", first)
    lifecycle("resume", second)
    completed = lab.request(f"/api/actors/{second}/fast-forward", "POST", {"expectedStep": 1})
    lab.check(completed["actor"]["name"] == second and completed["currentStep"] == 7 and
              completed["phase"] == "done", f"second Actor did not finish from step 1: {completed['currentStep']}")
    second_denied = publish(second)["publish"]
    lab.check(dns_denied(second_denied) and second_denied["sentHeaders"] == {"Authorization": "placeholder"},
              f"second Actor reached the service without a policy: {second_denied}")
    lab.check(lab.actor(second)["status"]["workerAssignment"]["workerPodUid"] ==
              running["status"]["workerAssignment"]["workerPodUid"],
              "second Actor did not share the first Actor's Worker Pod")
    lifecycle("suspend", second)
    lifecycle("resume", first)
    after_move = publish_until(first, "injection after resume", lambda outcome: outcome["status"] == 201,
                               assert_actor=True)
    lab.check(after_move["response"]["actor"]["uid"] == uid and
              state(first)["publications"][0]["status"] is None and len(state(first)["publications"]) >= 6,
              "policy or publish history did not follow the Actor across activations")
    lab.check(lab.actor(first)["status"]["state"] == "ACTOR_STATE_RUNNING" and
              lab.actor(second)["status"]["state"] == "ACTOR_STATE_SUSPENDED",
              "Chapter 6 did not end with actor 1 running and actor 2 suspended")
    print("PASS: Chapter 6 policy follows actor 1 across suspend/resume; actor 2 on the same Worker is denied",
          flush=True)


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
    templates = lab.ate("get", "actor-templates", "-a", lab.NAMESPACE)
    lab.check(all(t["metadata"]["name"] == "repo-workbench" for t in templates.get("actorTemplates", [])),
              "chapter verification requires only the original template")
    original = lab.ate("get", "actor-template", "repo-workbench", "-a", lab.NAMESPACE)
    golden_tag = original.get("status", {}).get("goldenSnapshotStatus", {}).get("goldenTag", {}).get("name")
    existing_tags = lab.ate("get", "tags", "-a", lab.NAMESPACE).get("tags", [])
    lab.check(golden_tag and
              {item["metadata"]["name"] for item in existing_tags} == {golden_tag},
              "chapter verification requires only the original template's golden tag")
    try:
        verify_chapter3(first, second)
        verify_capacity(first, second)
        verify_recovery(second)
        verify_snapshots(first, second, third, fourth, template, tag)
        verify_identity_and_policy(first, second)
    finally:
        # Delete only the names this suite reserves in an initially empty lab.
        failures = []
        for name in (fourth, third, second, first):
            result = ate_command("delete", "actor", name, "-a", lab.NAMESPACE, success=None)
            if result.returncode and "NotFound" not in result.stderr:
                failures.append(result.stderr)
        for resource, name in (("tag", tag), ("actor-template", template)):
            result = ate_command("delete", resource, name, "-a", lab.NAMESPACE, success=None)
            if result.returncode and "NotFound" not in result.stderr:
                failures.append(result.stderr)
        lab.check(not failures, f"chapter cleanup failed: {failures}")
    lab.check(not lab.ate("get", "actors", "-a", lab.NAMESPACE).get("actors"), "chapter test Actors remain")
    print("PASS: Chapters 3–6 verification leaves the Atespace empty", flush=True)


if __name__ == "__main__":
    main()
