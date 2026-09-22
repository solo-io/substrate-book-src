import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("webapp_server", ROOT / "webapp" / "server.py")
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SERVER)


class FakeActorHandler(BaseHTTPRequestHandler):
    target_actor = None
    requested_path = None

    def reply(self):
        type(self).target_actor = self.headers.get("ate-target-actor")
        type(self).requested_path = self.path
        body = json.dumps({"phase": "ready", "currentStep": 0}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = reply
    do_POST = reply

    def log_message(self, format, *args):
        return


class FakeActorManager:
    def __init__(self):
        self.actors = [self.actor("repo-workbench-1", "suspended")]
        self.suspended = None
        self.deleted = None

    @staticmethod
    def actor(name, lifecycle):
        return {
            "name": name,
            "atespace": "book-workbench",
            "uid": f"uid-{name}",
            "createdAt": "2026-09-22T00:00:00Z",
            "version": "1",
            "template": "book-workbench/repo-workbench",
            "lifecycle": lifecycle,
            "worker": None,
        }

    @staticmethod
    def validates_name(name):
        return name.startswith("repo-workbench-")

    def list_actors(self):
        return list(self.actors)

    def create_actor(self):
        actor = self.actor("repo-workbench-2", "suspended")
        self.actors.append(actor)
        return actor

    def suspend_actor(self, name):
        self.suspended = name
        return self.actor(name, "suspended")

    def delete_actor(self, name):
        self.deleted = name
        self.actors = [actor for actor in self.actors if actor["name"] != name]


class WebAppTest(unittest.TestCase):
    def setUp(self):
        FakeActorHandler.target_actor = None
        FakeActorHandler.requested_path = None
        self.actor_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeActorHandler)
        self.actor_thread = threading.Thread(target=self.actor_server.serve_forever, daemon=True)
        self.actor_thread.start()

        self.manager = FakeActorManager()
        SERVER.WebAppHandler.index_html = b"<html>Repo Workbench</html>"
        SERVER.WebAppHandler.agent_url = f"http://127.0.0.1:{self.actor_server.server_port}"
        SERVER.WebAppHandler.atespace = "book-workbench"
        SERVER.WebAppHandler.manager = self.manager
        self.web_server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.WebAppHandler)
        self.web_thread = threading.Thread(target=self.web_server.serve_forever, daemon=True)
        self.web_thread.start()
        self.base = f"http://127.0.0.1:{self.web_server.server_port}"

    def tearDown(self):
        self.web_server.shutdown()
        self.web_server.server_close()
        self.web_thread.join()
        self.actor_server.shutdown()
        self.actor_server.server_close()
        self.actor_thread.join()

    def json_request(self, path, method="GET", body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urlopen(request) as response:
            return response.status, json.load(response)

    def test_root_and_actor_list_do_not_call_an_actor(self):
        with urlopen(self.base + "/") as response:
            self.assertIn(b"Repo Workbench", response.read())
        status, body = self.json_request("/api/actors")

        self.assertEqual(status, 200)
        self.assertEqual(body["actors"][0]["name"], "repo-workbench-1")
        self.assertIsNone(FakeActorHandler.target_actor)

    def test_selected_actor_requests_are_routed_by_name(self):
        _, state = self.json_request("/api/actors/repo-workbench-1/state")
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(FakeActorHandler.target_actor, "book-workbench/repo-workbench-1")
        self.assertEqual(FakeActorHandler.requested_path, "/api/state")

        self.json_request(
            "/api/actors/repo-workbench-1/advance",
            method="POST",
            body={"expectedStep": 0},
        )
        self.assertEqual(FakeActorHandler.requested_path, "/api/advance")

    def test_create_suspend_and_delete_use_the_manager(self):
        status, created = self.json_request("/api/actors", method="POST")
        self.assertEqual(status, 201)
        self.assertEqual(created["actor"]["name"], "repo-workbench-2")

        self.json_request("/api/actors/repo-workbench-1/suspend", method="POST")
        self.assertEqual(self.manager.suspended, "repo-workbench-1")

        self.json_request("/api/actors/repo-workbench-1", method="DELETE")
        self.assertEqual(self.manager.deleted, "repo-workbench-1")


if __name__ == "__main__":
    unittest.main()
