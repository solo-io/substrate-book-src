"""Opt-in lifecycle verification against a deployed, empty Repo Workbench."""

import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTEXT = os.environ["KUBECTL_CONTEXT"]
NAMESPACE = "book-workbench"
REMOTE_REQUEST = """
import json, sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
path, method, body = sys.argv[1:]
request = Request('http://127.0.0.1:80' + path,
                  data=body.encode() if body else None, method=method,
                  headers={'Content-Type': 'application/json'})
try:
    response = urlopen(request, timeout=600)
except HTTPError as error:
    response = error
with response:
    raw = response.read().decode()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = raw
    print(json.dumps({'status': response.code, 'body': body}))
"""


def run_command(*args, input_text=None, success=True):
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=660)
    if success is True and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    if success is False and not result.returncode:
        raise RuntimeError(f"command unexpectedly succeeded: {args}")
    return result


def command(*args):
    return json.loads(run_command(*args).stdout)


def kube(*args):
    return command("kubectl", "--context", CONTEXT, *args)


def ate(*args):
    return command(str(ROOT / ".bin/kubectl-ate"), "--context", CONTEXT, *args, "-o", "json")


def request(path, method="GET", body=None, status=200):
    response = kube("exec", "-n", NAMESPACE, "deployment/repo-workbench-webapp", "--",
                    "python3", "-c", REMOTE_REQUEST, path, method,
                    json.dumps(body) if body is not None else "")
    check(response["status"] == status, f"{method} {path}: {response}")
    return response["body"]


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def worker_pod():
    pods = kube("get", "pods", "-n", NAMESPACE, "-o", "json")["items"]
    workers = [pod for pod in pods if pod["metadata"]["name"].startswith("generic-worker-")]
    check(len(workers) == 1, f"expected one Worker Pod, found {len(workers)}")
    return workers[0]["metadata"]["uid"]


def actor(name):
    return ate("get", "actor", name, "-a", NAMESPACE)


def suspend(name):
    request(f"/api/actors/{name}/suspend", "POST")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        value = actor(name)
        if value["status"]["state"] == "ACTOR_STATE_SUSPENDED":
            check(not value["status"].get("workerAssignment"), "suspended Actor still assigned")
            return
        time.sleep(2)
    raise RuntimeError(f"{name} did not suspend")


def main():
    created = []
    check(not request("/api/actors")["actors"], "verification requires an empty book-workbench Atespace")
    deployment = kube("get", "deployment/ate-api-server", "-n", "ate-system", "-o", "json")
    lock = json.loads((ROOT / "scripts/runtime-lock.json").read_text())
    expected_api = lock["images"]["ghcr.io/kagent-dev/substrate/ateapi:" + os.environ["SUBSTRATE_VERSION"]]
    check(deployment["spec"]["template"]["spec"]["containers"][0]["image"] == expected_api,
          "installed runtime does not match the locked release digest; rerun setup on a clean lab")
    original_worker = worker_pod()
    try:
        for _ in range(2):
            value = request("/api/actors", "POST", status=201)["actor"]
            created.append(value["name"])
            check(value["lifecycle"] == "suspended", "creation resumed an Actor")
        first, second = created
        request("/api/actors")
        check(all(actor(name)["status"]["state"] == "ACTOR_STATE_SUSPENDED" for name in created),
              "listing resumed an Actor")
        print("PASS: creating and listing two Actors leaves them suspended", flush=True)

        path = f"/api/actors/{first}"
        first_state = request(path + "/state")
        check(first_state["currentStep"] == 0, "first Actor did not start fresh")
        check(first_state["actor"]["name"] == first and first_state["actor"]["atespace"] == NAMESPACE,
              "first Actor's projected identity is incorrect")
        first_assignment = actor(first)["status"]["workerAssignment"]
        check(first_assignment.get("workerPodUid") == original_worker, "first Actor assigned to an unexpected Worker")
        for step in range(5):
            first_state = request(path + "/advance", "POST", {"expectedStep": step})
            check(first_state["currentStep"] == step + 1, "advance skipped a step")
            if step == 1:
                check("FAILED" in first_state["tests"], "initial repository tests did not reproduce the bug")
        files = {item["path"]: item for item in first_state["files"]}
        check(files["src/slugify.py"]["status"] == "modified", "repair did not modify the source")
        check(files["AGENT_REPORT.md"]["status"] == "added", "report was not added to the durable repository")
        request(path + "/advance", "POST", {"expectedStep": 4}, status=409)
        check(request(path + "/state")["currentStep"] == 5, "stale request advanced the workflow")
        suspend(first)
        print("PASS: repair and report persist at step five; stale requests return 409; suspend releases assignment", flush=True)

        second_state = request(f"/api/actors/{second}/state")
        check(second_state["currentStep"] == 0, "second Actor inherited first Actor state")
        check(second_state["actor"]["name"] == second and
              second_state["actor"]["uid"] != first_state["actor"]["uid"], "Actor identities are not independent")
        second_assignment = actor(second)["status"]["workerAssignment"]
        check(first_assignment.get("workerPodUid") == second_assignment.get("workerPodUid"),
              "Actors did not reuse the same Worker")
        second_state = request(f"/api/actors/{second}/advance", "POST", {"expectedStep": 0})
        check(second_state["currentStep"] == 1, "second Actor did not advance independently")
        suspend(second)
        check(worker_pod() == original_worker, "Worker Pod was replaced")
        print("PASS: the second Actor has independent state and reuses the same Worker Pod", flush=True)

        restored = request(path + "/state")
        for key in ("actor", "currentStep", "stateRevision", "transcript", "tests", "files", "diff"):
            check(restored[key] == first_state[key], f"{key} changed across suspend/resume")
        completed = request(path + "/fast-forward", "POST", {"expectedStep": 5})
        check(completed["phase"] == "done" and completed["currentStep"] == 7, "workflow did not complete")
        check("OK" in completed["tests"], "repaired tests did not pass")
        check(any(item["path"] == "AGENT_REPORT.md" for item in completed["files"]), "report missing")
        request(path + "/advance", "POST", {"expectedStep": 7}, status=409)
        suspend(first)
        print("PASS: memory, transcript, durable files, and identity restore; fast-forward finishes all seven steps", flush=True)
    finally:
        for name in reversed(created):
            request(f"/api/actors/{name}", "DELETE")
    check(not request("/api/actors")["actors"], "verification Actors were not deleted")
    print("PASS: verification Actors deleted; Atespace is empty", flush=True)


if __name__ == "__main__":
    main()
