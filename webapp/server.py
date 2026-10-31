#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


class ControlPlaneError(RuntimeError):
    pass


class CliActorManager:
    def __init__(
        self,
        cli: str,
        endpoint: str,
        kubeconfig: str,
        atespace: str,
        template: str,
        actor_prefix: str,
    ):
        self.cli = cli
        self.endpoint = endpoint
        self.kubeconfig = kubeconfig
        self.atespace = atespace
        self.template = template
        self.actor_prefix = actor_prefix
        self.name_pattern = re.compile(rf"^{re.escape(actor_prefix)}-([1-9][0-9]*)$")
        self.lock = threading.Lock()

    def validates_name(self, name: str) -> bool:
        return self.name_pattern.fullmatch(name) is not None

    def _run(self, *args: str, output: bool = True) -> object:
        command = [
            self.cli,
            "--endpoint",
            self.endpoint,
            "--kubeconfig",
            self.kubeconfig,
            *args,
        ]
        if output:
            command.extend(["-o", "json"])
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
            raise ControlPlaneError(detail)
        if not output:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ControlPlaneError(f"invalid control-plane response: {exc}") from exc

    @staticmethod
    def _lifecycle(state: str) -> str:
        prefix = "ACTOR_STATE_"
        return state.removeprefix(prefix).lower() if state else "unknown"

    def _simplify(self, actor: dict[str, object]) -> dict[str, object]:
        metadata = actor.get("metadata", {})
        template = actor.get("actorTemplate", {})
        status = actor.get("status", {})
        assignment = status.get("workerAssignment", {}) if isinstance(status, dict) else {}
        return {
            "name": metadata.get("name"),
            "atespace": metadata.get("atespace"),
            "uid": metadata.get("uid"),
            "createdAt": metadata.get("createTime"),
            "version": metadata.get("version"),
            "template": f"{template.get('atespace')}/{template.get('name')}",
            "lifecycle": self._lifecycle(status.get("state", "")),
            "worker": assignment.get("workerPod") if isinstance(assignment, dict) else None,
        }

    def list_actors(self) -> list[dict[str, object]]:
        response = self._run("get", "actors", "--atespace", self.atespace)
        actors = []
        for actor in response.get("actors", []):
            metadata = actor.get("metadata", {})
            template = actor.get("actorTemplate", {})
            name = metadata.get("name", "")
            if (
                self.validates_name(name)
                and template.get("atespace") == self.atespace
                and template.get("name") == self.template
            ):
                actors.append(self._simplify(actor))
        actors.sort(key=lambda actor: int(self.name_pattern.fullmatch(str(actor["name"])).group(1)))
        return actors

    def create_actor(self) -> dict[str, object]:
        with self.lock:
            actors = self.list_actors()
            ordinals = [int(self.name_pattern.fullmatch(str(actor["name"])).group(1)) for actor in actors]
            name = f"{self.actor_prefix}-{max(ordinals, default=0) + 1}"
            actor = self._run(
                "create",
                "actor",
                name,
                "--atespace",
                self.atespace,
                "--template",
                self.template,
            )
            return self._simplify(actor)

    def suspend_actor(self, name: str) -> dict[str, object]:
        if not self.validates_name(name):
            raise ValueError("invalid Actor name")
        actor = self._run(
            "suspend", "actor", name, "--atespace", self.atespace, "--mode", "sync"
        )
        return self._simplify(actor)

    def delete_actor(self, name: str) -> None:
        if not self.validates_name(name):
            raise ValueError("invalid Actor name")
        self._run("delete", "actor", name, "--atespace", self.atespace, output=False)


class LocalActorManager:
    def __init__(self, target_actor: str):
        self.atespace, self.name = target_actor.split("/", 1)

    def validates_name(self, name: str) -> bool:
        return name == self.name

    def list_actors(self) -> list[dict[str, object]]:
        return [
            {
                "name": self.name,
                "atespace": self.atespace,
                "uid": "not-running-in-substrate",
                "createdAt": None,
                "version": "local",
                "template": "local/repo-workbench",
                "lifecycle": "local",
                "worker": None,
            }
        ]

    def create_actor(self) -> dict[str, object]:
        raise ControlPlaneError("Actor creation requires a Substrate control plane")

    def suspend_actor(self, name: str) -> dict[str, object]:
        raise ControlPlaneError("Actor suspension requires a Substrate control plane")

    def delete_actor(self, name: str) -> None:
        raise ControlPlaneError("Actor deletion requires a Substrate control plane")


class WebAppHandler(BaseHTTPRequestHandler):
    index_html: bytes
    agent_url: str
    # The router admits only mTLS clients whose SPIFFE ID it allows. Both files
    # are projected and rotated by the kubelet, so they are read per request.
    agent_client_cert: str = ""
    agent_ca_file: str = ""
    source_control_url: str = ""
    atespace: str
    manager: object

    def _write(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, value: object) -> None:
        self._write(status.value, "application/json; charset=utf-8", json.dumps(value).encode("utf-8"))

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def _actor_route(self) -> tuple[str, str] | None:
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) != 4 or parts[:2] != ["api", "actors"]:
            return None
        name, action = parts[2], parts[3]
        if not self.manager.validates_name(name):
            return None
        return name, action

    def _call_actor(self, actor_name: str, actor_path: str) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None
        upstream_url = urljoin(self.agent_url.rstrip("/") + "/", actor_path.lstrip("/"))
        headers = {
            "Accept": self.headers.get("Accept", "application/json"),
            "ate-target-actor": f"{self.atespace}/{actor_name}",
        }
        if self.headers.get("Content-Type"):
            headers["Content-Type"] = self.headers["Content-Type"]
        request = Request(upstream_url, data=body, headers=headers, method=self.command)
        context = None
        if self.agent_client_cert:
            context = ssl.create_default_context(cafile=self.agent_ca_file or None)
            context.load_cert_chain(self.agent_client_cert)
        try:
            with urlopen(request, timeout=600, context=context) as response:
                self._write(response.status, response.headers.get_content_type(), response.read())
        except HTTPError as exc:
            self._write(exc.code, exc.headers.get_content_type(), exc.read())
        except (TimeoutError, URLError) as exc:
            self._json_error(HTTPStatus.BAD_GATEWAY, f"Actor request failed: {exc}")

    def _call_source_control(self, path: str) -> None:
        # Read-only inspection of what the service received. The web app holds no
        # account token; the demo service records the tokens it received as is.
        if not self.source_control_url:
            self._json_error(HTTPStatus.NOT_FOUND, "no source-control service is configured")
            return
        url = urljoin(self.source_control_url.rstrip("/") + "/", path.lstrip("/"))
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=30) as response:
                self._write(response.status, response.headers.get_content_type(), response.read())
        except HTTPError as exc:
            self._write(exc.code, exc.headers.get_content_type(), exc.read())
        except (TimeoutError, URLError) as exc:
            self._json_error(HTTPStatus.BAD_GATEWAY, f"source-control request failed: {exc}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._write(HTTPStatus.OK.value, "text/html; charset=utf-8", self.index_html)
            return
        if path == "/readyz":
            self._write(HTTPStatus.OK.value, "text/plain; charset=utf-8", b"ready\n")
            return
        if path == "/api/actors":
            try:
                self._json(HTTPStatus.OK, {"actors": self.manager.list_actors()})
            except ControlPlaneError as exc:
                self._json_error(HTTPStatus.BAD_GATEWAY, f"control-plane request failed: {exc}")
            return
        if path == "/api/source-control/pulls":
            self._call_source_control("/pulls")
            return
        route = self._actor_route()
        if route and route[1] == "state":
            self._call_actor(route[0], "/api/state")
            return
        self._json_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/actors":
                self._json(HTTPStatus.CREATED, {"actor": self.manager.create_actor()})
                return
            route = self._actor_route()
            if route and route[1] == "suspend":
                self._json(HTTPStatus.OK, {"actor": self.manager.suspend_actor(route[0])})
                return
            if route and route[1] in {"advance", "fast-forward", "publish"}:
                self._call_actor(route[0], f"/api/{route[1]}")
                return
        except ValueError as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except ControlPlaneError as exc:
            self._json_error(HTTPStatus.BAD_GATEWAY, f"control-plane request failed: {exc}")
            return
        self._json_error(HTTPStatus.NOT_FOUND, "not found")

    def do_DELETE(self) -> None:
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) != 3 or parts[:2] != ["api", "actors"] or not self.manager.validates_name(parts[2]):
            self._json_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            self.manager.delete_actor(parts[2])
            self._json(HTTPStatus.OK, {"deleted": True, "name": parts[2]})
        except ValueError as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
        except ControlPlaneError as exc:
            self._json_error(HTTPStatus.BAD_GATEWAY, f"control-plane request failed: {exc}")

    def log_message(self, format: str, *args: object) -> None:
        return


def build_manager():
    cli = os.environ.get("ATE_CLI", "")
    if not cli:
        return LocalActorManager(os.environ.get("TARGET_ACTOR", "local/repo-workbench-1"))
    return CliActorManager(
        cli=cli,
        endpoint=os.environ.get("ATE_ENDPOINT", "api.ate-system.svc:443"),
        kubeconfig=os.environ.get("ATE_KUBECONFIG", "/app/kubeconfig.yaml"),
        atespace=os.environ.get("BOOK_ATESPACE", "book-workbench"),
        template=os.environ.get("BOOK_TEMPLATE", "repo-workbench"),
        actor_prefix=os.environ.get("ACTOR_PREFIX", "repo-workbench"),
    )


def run_server() -> None:
    app_dir = Path(__file__).resolve().parent
    port = int(os.environ.get("PORT", "80"))
    WebAppHandler.index_html = (app_dir / "index.html").read_bytes()
    WebAppHandler.agent_url = os.environ.get("AGENT_URL", "https://atenet-router.ate-system.svc")
    WebAppHandler.agent_client_cert = os.environ.get("AGENT_CLIENT_CERT", "")
    WebAppHandler.agent_ca_file = os.environ.get("AGENT_CA_FILE", "")
    WebAppHandler.source_control_url = os.environ.get("SOURCE_CONTROL_URL", "")
    WebAppHandler.manager = build_manager()
    WebAppHandler.atespace = WebAppHandler.manager.atespace
    server = ThreadingHTTPServer(("0.0.0.0", port), WebAppHandler)
    print(f"Repo Workbench web app listening on :{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
