import importlib.util
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("source_control_server", ROOT / "source-control" / "server.py")
SERVER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SERVER)

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm
except ImportError:  # pragma: no cover - the image installs these; the host may not.
    jwt = None


class FakeVerifier:
    """Stands in for the IdP-backed verifier without network access."""

    def __init__(self, accepted):
        self.accepted = accepted
        self.tokens = []

    def verify(self, token):
        self.tokens.append(token)
        if token not in self.accepted:
            raise SERVER.AssertionError_("token rejected: not minted by Substrate")
        return self.accepted[token]


class SourceControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token_file = Path(self.temp.name) / "token"
        self.token_file.write_text("account-token-1\n")
        self.verifier = FakeVerifier({
            "minted-jwt": {"subject": "actor/book-workbench/repo-workbench-1", "atespace": "book-workbench",
                           "name": "repo-workbench-1", "uid": "uid-1", "audience": "svc", "expiresAt": 0,
                           "claims": {}},
        })
        self.log = []
        SERVER.SourceControlHandler.state = SERVER.SourceControlState(self.token_file, "repo-workbench-bot", self.verifier,
                                                                      log=self.log.append)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.SourceControlHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, headers, body=None):
        data = json.dumps(body or {"title": "Repair slugify", "body": "# Agent report"}).encode()
        request = Request(self.base + "/repos/repo-workbench/pulls", data=data, method="POST",
                          headers={"Content-Type": "application/json", **headers})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def pulls(self):
        with urlopen(self.base + "/pulls") as response:
            return json.load(response)["pulls"]

    def test_missing_or_placeholder_authorization_is_rejected_without_recording(self):
        self.assertEqual(self.post({})[0], 401)
        status, body = self.post({"Authorization": "placeholder"})
        self.assertEqual(status, 401)
        self.assertIn("bearer", body["error"])
        status, body = self.post({"Authorization": "Bearer wrong-token"})
        self.assertEqual(status, 401)
        self.assertEqual(self.pulls(), [])

    def test_valid_token_records_the_pull_with_a_fingerprint_not_the_token(self):
        status, body = self.post({"Authorization": "Bearer account-token-1"})
        self.assertEqual(status, 201)
        self.assertEqual(body["number"], 1)
        self.assertEqual(body["authenticatedAs"], "repo-workbench-bot")
        self.assertIsNone(body["actor"])
        record = self.pulls()[0]
        self.assertEqual(record["tokenFingerprint"], hashlib.sha256(b"account-token-1").hexdigest()[:16])
        self.assertNotIn("account-token-1", json.dumps(record))
        self.assertEqual(record["body"], "# Agent report")

    def test_actor_assertion_is_verified_when_present(self):
        status, body = self.post({"Authorization": "Bearer account-token-1", "Actor-Assertion": "placeholder"})
        self.assertEqual(status, 401)
        self.assertIn("Actor-Assertion rejected", body["error"])
        self.assertEqual(self.pulls(), [])

        status, body = self.post({"Authorization": "Bearer account-token-1", "Actor-Assertion": "minted-jwt"})
        self.assertEqual(status, 201)
        self.assertEqual(body["actor"], {"subject": "actor/book-workbench/repo-workbench-1",
                                         "atespace": "book-workbench", "name": "repo-workbench-1", "uid": "uid-1"})
        self.assertEqual(self.pulls()[0]["actor"]["uid"], "uid-1")

    def test_each_request_is_logged_without_the_token(self):
        self.post({"Authorization": "placeholder"})
        self.post({"Authorization": "Bearer account-token-1", "Actor-Assertion": "minted-jwt"})
        self.post({"Authorization": "secret-but-not-bearer"})
        records = [json.loads(line) for line in self.log]
        self.assertEqual([record["status"] for record in records], [401, 201, 401])
        self.assertEqual(records[0]["authorization"], "placeholder")
        self.assertIn("bearer", records[0]["error"])
        fingerprint = hashlib.sha256(b"account-token-1").hexdigest()[:16]
        self.assertEqual(records[1]["authorization"], f"Bearer sha256:{fingerprint}")
        self.assertEqual(records[1]["actor"]["uid"], "uid-1")
        self.assertEqual(records[1]["authenticatedAs"], "repo-workbench-bot")
        self.assertEqual(records[2]["authorization"], "21 bytes, not shown")
        logged = "\n".join(self.log)
        for secret in ("account-token-1", "minted-jwt", "secret-but-not-bearer"):
            self.assertNotIn(secret, logged)

    def test_token_rotation_is_read_from_the_mounted_file(self):
        self.token_file.write_text("account-token-2\n")
        self.assertEqual(self.post({"Authorization": "Bearer account-token-1"})[0], 401)
        self.assertEqual(self.post({"Authorization": "Bearer account-token-2"})[0], 201)

    def test_unknown_routes(self):
        self.assertEqual(self.post({"Authorization": "Bearer account-token-1"}, body={"title": "x"})[0], 201)
        request = Request(self.base + "/repos/other", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as raised:
            urlopen(request)
        self.assertEqual(raised.exception.code, 404)


@unittest.skipIf(jwt is None, "PyJWT and cryptography are required for signature tests")
class ActorAssertionVerifierTest(unittest.TestCase):
    ISSUER = "https://idp.ate-system.svc"
    AUDIENCE = "source-control.book-workbench.svc.cluster.local"

    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        public_jwk = json.loads(ECAlgorithm.to_jwk(self.key.public_key()))
        public_jwk.update({"kid": "1", "use": "sig", "alg": "ES256"})
        self.jwks = {"keys": [public_jwk]}
        self.verifier = SERVER.ActorAssertionVerifier(self.ISSUER, self.AUDIENCE, None)
        self.verifier._fetch_jwks = lambda: self.jwks

    def mint(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": self.ISSUER, "sub": "actor/book-workbench/repo-workbench-1", "aud": [self.AUDIENCE],
            "exp": now + 600, "nbf": now - 300, "iat": now, "jti": "abc",
            "actor.ate.dev": {"atespace": "book-workbench", "actorName": "repo-workbench-1", "actorUID": "uid-1"},
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="ES256", headers={"kid": "1"})

    def test_substrate_claims_are_returned_for_a_valid_token(self):
        actor = self.verifier.verify(self.mint())
        self.assertEqual((actor["atespace"], actor["name"], actor["uid"]), ("book-workbench", "repo-workbench-1", "uid-1"))
        self.assertEqual(actor["subject"], "actor/book-workbench/repo-workbench-1")

    def test_wrong_audience_issuer_signature_or_expiry_is_rejected(self):
        other_key = ec.generate_private_key(ec.SECP256R1())
        forged = jwt.encode({"iss": self.ISSUER, "sub": "x", "aud": [self.AUDIENCE], "exp": int(time.time()) + 60,
                             "iat": int(time.time()), "actor.ate.dev": {}}, other_key, algorithm="ES256",
                            headers={"kid": "1"})
        for token in (self.mint(aud=["someone-else"]), self.mint(iss="https://evil.example"),
                      self.mint(exp=int(time.time()) - 10), forged, "placeholder"):
            with self.assertRaises(SERVER.AssertionError_):
                self.verifier.verify(token)

    def test_unknown_key_id_refreshes_the_key_set_once(self):
        calls = []
        self.verifier._fetch_jwks = lambda: calls.append(1) or self.jwks
        with self.assertRaises(SERVER.AssertionError_):
            self.verifier.verify(self.mint() if False else jwt.encode(
                {"iss": self.ISSUER, "sub": "x", "aud": [self.AUDIENCE], "exp": int(time.time()) + 60,
                 "iat": int(time.time()), "actor.ate.dev": {}}, self.key, algorithm="ES256", headers={"kid": "2"}))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
