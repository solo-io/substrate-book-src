#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


FIXED_SLUGIFY = '''import re


def slugify(value: str) -> str:
    """Turn a title into a URL slug."""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return normalized.strip("-")
'''

AGENT_REPORT = '''# Agent report

The repository task repaired `src/slugify.py` so punctuation and repeated
whitespace collapse into a single hyphen. The complete test suite passes.
'''

TASK = "Fix the intentionally broken slugify implementation and make every test pass."
STEP_TITLES = (
    "Inspect repository",
    "Reproduce the failure",
    "Diagnose the bug",
    "Repair the implementation",
    "Write the agent report",
    "Verify the test suite",
    "Review the changes",
)


class StepConflict(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkbenchState:
    def __init__(self, workspace: Path, seed: Path, actor_info: Path):
        self.workspace = workspace
        self.seed = seed
        self.actor_info = actor_info
        self.lock = threading.RLock()
        self.phase = "ready"
        self.current_step = 0
        self.state_revision = 0
        self.last_test_output = "Tests have not run yet."
        self.transcript: list[dict[str, object]] = []
        self._initialize_workspace()
        self._record(
            "system",
            "Repository task ready",
            "The Actor owns a fresh repository workspace. Advance one step at a time or fast-forward through the same steps.",
        )

    def _initialize_workspace(self) -> None:
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        if not self.workspace.exists():
            shutil.copytree(self.seed, self.workspace)
        if (self.workspace / ".git").exists():
            return
        self._command(["git", "init", "-q"])
        self._command(["git", "config", "user.name", "Repo Workbench"])
        self._command(["git", "config", "user.email", "workbench@example.invalid"])
        self._command(["git", "add", "."])
        self._command(["git", "commit", "-q", "-m", "Initial broken project"])

    def _read_actor_field(self, name: str, fallback: str) -> str:
        path = self.actor_info / name
        try:
            value = path.read_text(encoding="utf-8").strip()
            return value or fallback
        except OSError:
            return fallback

    def identity(self) -> dict[str, str]:
        return {
            "name": self._read_actor_field("actor-name", "local-workbench"),
            "atespace": self._read_actor_field("atespace", "local"),
            "uid": self._read_actor_field("actor-uid", "not-running-in-substrate"),
        }

    def _record(self, kind: str, title: str, body: str) -> None:
        self.state_revision += 1
        self.transcript.append(
            {
                "sequence": len(self.transcript) + 1,
                "time": utc_now(),
                "kind": kind,
                "title": title,
                "body": body.strip(),
            }
        )

    def _command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=self.workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )

    def _run_and_record(self, title: str, command: list[str]) -> subprocess.CompletedProcess[str]:
        result = self._command(command)
        rendered = "$ " + " ".join(command) + "\n" + result.stdout
        self._record("tool", title, rendered)
        return result

    def _git_status(self) -> dict[str, str]:
        result = self._command(["git", "status", "--short", "--untracked-files=all"])
        statuses: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            code = line[:2]
            path = line[3:]
            if code == "??" or "A" in code:
                statuses[path] = "added"
            elif "D" in code:
                statuses[path] = "deleted"
            else:
                statuses[path] = "modified"
        return statuses

    def _files(self) -> list[dict[str, str]]:
        statuses = self._git_status()
        files = []
        for path in sorted(self.workspace.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            relative = path.relative_to(self.workspace).as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = "Binary file"
            files.append({"path": relative, "status": statuses.get(relative, "unchanged"), "content": content})
        return files

    def _diff(self) -> str:
        parts = [self._command(["git", "diff", "--", "."]).stdout.strip()]
        for path, status in self._git_status().items():
            if status != "added":
                continue
            result = self._command(["git", "diff", "--no-index", "--", "/dev/null", path])
            parts.append(result.stdout.strip())
        return "\n\n".join(part for part in parts if part)

    def _step_inspect(self) -> None:
        listing = self._command(["find", ".", "-maxdepth", "3", "-type", "f", "-not", "-path", "./.git/*"])
        readme = (self.workspace / "README.md").read_text(encoding="utf-8")
        self._record("tool", "Inspect repository", "$ find . -maxdepth 3 -type f\n" + listing.stdout)
        self._record("agent", "Read the task requirements", readme)

    def _step_reproduce(self) -> None:
        result = self._run_and_record(
            "Run the failing tests", ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
        )
        self.last_test_output = result.stdout
        if result.returncode == 0:
            raise RuntimeError("the starting test suite unexpectedly passed")

    def _step_diagnose(self) -> None:
        source = (self.workspace / "src" / "slugify.py").read_text(encoding="utf-8")
        tests = (self.workspace / "tests" / "test_slugify.py").read_text(encoding="utf-8")
        self._record(
            "agent",
            "Diagnose the slugification bug",
            "The implementation replaces literal spaces only, so punctuation and repeated separators are not normalized.\n\n"
            f"src/slugify.py\n\n{source}\n\ntests/test_slugify.py\n\n{tests}",
        )

    def _step_repair(self) -> None:
        (self.workspace / "src" / "slugify.py").write_text(FIXED_SLUGIFY, encoding="utf-8")
        self._record(
            "agent",
            "Repair src/slugify.py",
            "Normalize runs of non-alphanumeric characters into one hyphen and trim boundary hyphens.",
        )

    def _step_report(self) -> None:
        (self.workspace / "AGENT_REPORT.md").write_text(AGENT_REPORT, encoding="utf-8")
        self._record(
            "agent",
            "Create AGENT_REPORT.md",
            "Added a durable task artifact that records what changed and why.",
        )

    def _step_verify(self) -> None:
        result = self._run_and_record(
            "Run the complete test suite", ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
        )
        self.last_test_output = result.stdout
        if result.returncode != 0:
            raise RuntimeError("the repaired test suite failed")

    def _step_review(self) -> None:
        diff = self._diff()
        self._record("tool", "Review the final changes", "$ git diff -- .\n" + (diff or "No changes."))
        self._record(
            "agent",
            "Repository task complete",
            "All tests pass. The repaired source and the new agent report remain in this Actor's durable workspace.",
        )

    def _advance_locked(self) -> None:
        if self.phase == "done":
            raise StepConflict("this repository task is already complete")
        if self.phase == "failed":
            raise StepConflict("this repository task has failed")

        actions = (
            self._step_inspect,
            self._step_reproduce,
            self._step_diagnose,
            self._step_repair,
            self._step_report,
            self._step_verify,
            self._step_review,
        )
        self.phase = "running"
        try:
            actions[self.current_step]()
            self.current_step += 1
            self.phase = "done" if self.current_step == len(actions) else "waiting"
        except Exception as exc:
            self.phase = "failed"
            self._record("error", "Step failed", f"{type(exc).__name__}: {exc}")
            raise

    def advance(self, expected_step: int) -> dict[str, object]:
        with self.lock:
            if expected_step != self.current_step:
                raise StepConflict(f"expected step {expected_step}, but the Actor is at step {self.current_step}")
            self._advance_locked()
            return self.snapshot()

    def fast_forward(self, expected_step: int) -> dict[str, object]:
        with self.lock:
            if expected_step != self.current_step:
                raise StepConflict(f"expected step {expected_step}, but the Actor is at step {self.current_step}")
            while self.phase != "done":
                self._advance_locked()
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            steps = []
            for index, title in enumerate(STEP_TITLES):
                if index < self.current_step:
                    status = "complete"
                elif index == self.current_step and self.phase not in {"done", "failed"}:
                    status = "next"
                elif index == self.current_step and self.phase == "failed":
                    status = "failed"
                else:
                    status = "pending"
                steps.append({"number": index + 1, "title": title, "status": status})
            return {
                "actor": self.identity(),
                "task": TASK,
                "phase": self.phase,
                "currentStep": self.current_step,
                "totalSteps": len(STEP_TITLES),
                "nextStep": STEP_TITLES[self.current_step] if self.current_step < len(STEP_TITLES) else None,
                "stateRevision": self.state_revision,
                "steps": steps,
                "transcript": list(self.transcript),
                "files": self._files(),
                "diff": self._diff(),
                "tests": self.last_test_output,
            }


class WorkbenchHandler(BaseHTTPRequestHandler):
    state: WorkbenchState

    def _write(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, value: object) -> None:
        self._write(status, "application/json; charset=utf-8", json.dumps(value).encode("utf-8"))

    def _json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 16_384:
            raise ValueError("request body is too large")
        if not content_length:
            return {}
        value = json.loads(self.rfile.read(content_length))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(HTTPStatus.OK, self.state.snapshot())
        elif path == "/readyz":
            self._write(HTTPStatus.OK, "text/plain; charset=utf-8", b"ready\n")
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/advance", "/api/fast-forward"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            body = self._json_body()
            expected_step = body.get("expectedStep")
            if not isinstance(expected_step, int) or isinstance(expected_step, bool):
                raise ValueError("expectedStep must be an integer")
            if path == "/api/advance":
                snapshot = self.state.advance(expected_step)
            else:
                snapshot = self.state.fast_forward(expected_step)
            self._json(HTTPStatus.OK, snapshot)
        except StepConflict as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc), "state": self.state.snapshot()})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"step failed: {exc}", "state": self.state.snapshot()})

    def log_message(self, format: str, *args: object) -> None:
        return


def run_server() -> None:
    workspace = Path(os.environ.get("WORKSPACE_DIR", "/workspace/repository"))
    seed = Path(os.environ.get("SEED_DIR", "/opt/repo-workbench-seed"))
    actor_info = Path(os.environ.get("ACTOR_INFO_DIR", "/run/ate"))
    port = int(os.environ.get("PORT", "80"))
    WorkbenchHandler.state = WorkbenchState(workspace, seed, actor_info)
    server = ThreadingHTTPServer(("0.0.0.0", port), WorkbenchHandler)
    print(f"Repo Workbench agent API listening on :{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
