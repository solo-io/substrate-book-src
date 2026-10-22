#!/usr/bin/env python3
"""A stand-in internal source-control service for the credential brokering demo.

It accepts pull requests only with a bearer token that matches the account token
mounted from a Kubernetes Secret. It optionally verifies a Substrate-minted
Actor JWT presented in a separate header so the reader can see what an external
service can and cannot learn about the caller.

By default it logs and records only token fingerprints. With DEMO_SHOW_SECRETS=1
(the live demo sets it) it logs and records the credentials it receives as is,
and the Actor JWT decoded, so an audience can see exactly what arrived. A real
service never would.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import urlopen

try:
    import jwt
    from jwt import PyJWKSet
except ImportError:  # pragma: no cover - the image installs PyJWT; tests may skip.
    jwt = None
    PyJWKSet = None


ASSERTION_HEADER = "Actor-Assertion"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(secret: bytes) -> str:
    return hashlib.sha256(secret).hexdigest()[:16]


class AssertionError_(Exception):
    """An Actor assertion header was present but could not be verified."""


class ActorAssertionVerifier:
    """Verifies Actor JWTs against the Substrate IdP's published signing keys."""

    def __init__(self, issuer: str, audience: str, ca_file: str | None, jwks_ttl: float = 300.0):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.ca_file = ca_file
        self.jwks_ttl = jwks_ttl
        self.lock = threading.Lock()
        self._jwks: dict[str, object] | None = None
        self._fetched_at = 0.0

    def _ssl_context(self) -> ssl.SSLContext:
        if self.ca_file:
            return ssl.create_default_context(cafile=self.ca_file)
        return ssl.create_default_context()

    def _fetch_jwks(self) -> dict[str, object]:
        context = self._ssl_context()
        with urlopen(self.issuer + "/.well-known/openid-configuration", context=context, timeout=10) as response:
            discovery = json.load(response)
        with urlopen(discovery["jwks_uri"], context=context, timeout=10) as response:
            return json.load(response)

    def _keys(self, refresh: bool = False) -> dict[str, object]:
        with self.lock:
            stale = time.monotonic() - self._fetched_at > self.jwks_ttl
            if refresh or self._jwks is None or stale:
                self._jwks = self._fetch_jwks()
                self._fetched_at = time.monotonic()
            return self._jwks

    def verify(self, token: str) -> dict[str, object]:
        if jwt is None:
            raise AssertionError_("PyJWT is not installed, so Actor assertions cannot be verified")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AssertionError_(f"malformed token: {exc}") from exc
        kid = header.get("kid")
        try:
            key = self._select_key(kid)
        except (OSError, ValueError, KeyError) as exc:
            raise AssertionError_(f"signing key unavailable: {exc}") from exc
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["ES256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AssertionError_(f"token rejected: {exc}") from exc
        substrate = claims.get("actor.ate.dev")
        if not isinstance(substrate, dict):
            raise AssertionError_("token carries no actor.ate.dev claim")
        return {
            "subject": claims.get("sub"),
            "atespace": substrate.get("atespace"),
            "name": substrate.get("actorName"),
            "uid": substrate.get("actorUID"),
            "audience": claims.get("aud"),
            "expiresAt": claims.get("exp"),
            "claims": substrate,
        }

    def _select_key(self, kid: str | None):
        for refresh in (False, True):
            key_set = PyJWKSet.from_dict(self._keys(refresh=refresh))
            for candidate in key_set.keys:
                if kid is None or candidate.key_id == kid:
                    return candidate.key
        raise KeyError(f"no signing key with kid {kid!r}")


def describe_presented(value: str | None) -> str | None:
    """Describe a presented credential header for the log without revealing it.

    Only the demo's literal placeholder is shown as is; a bearer token appears as
    its fingerprint, and anything else only as its length.
    """
    if value is None:
        return None
    stripped = value.strip()
    if stripped.lower() == "placeholder":
        return "placeholder"
    scheme, _, presented = stripped.partition(" ")
    if scheme.lower() == "bearer" and presented.strip():
        return f"Bearer sha256:{fingerprint(presented.strip().encode('utf-8'))}"
    return f"{len(stripped)} bytes, not shown"


def decode_jwt(value: str | None) -> dict[str, object] | None:
    """Decode a JWT's header and payload for display, without verifying it."""
    if value is None:
        return None
    parts = value.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        header, payload = (json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                           for part in parts[:2])
    except ValueError:
        return None
    return {"header": header, "payload": payload, "signature": parts[2]}


def print_log(line: str) -> None:
    print(line, flush=True)


class SourceControlState:
    def __init__(self, token_file: Path, account: str, verifier: ActorAssertionVerifier | None,
                 log: Callable[[str], None] = print_log, show_secrets: bool = False):
        self.token_file = token_file
        self.show_secrets = show_secrets
        self.account = account
        self.verifier = verifier
        self.log = log
        self.lock = threading.Lock()
        self.pulls: list[dict[str, object]] = []

    def _expected_token(self) -> bytes:
        return self.token_file.read_bytes().strip()

    def authenticate(self, authorization: str | None) -> tuple[bool, str, str | None]:
        """Return (accepted, reason, token fingerprint)."""
        if not authorization:
            return False, "missing Authorization header", None
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            return False, "Authorization header is not a bearer token", None
        presented_bytes = presented.strip().encode("utf-8")
        try:
            expected = self._expected_token()
        except OSError:
            return False, "account token is not configured", None
        if not expected or not hmac.compare_digest(presented_bytes, expected):
            return False, "bearer token is not valid for this service", fingerprint(presented_bytes)
        return True, "ok", fingerprint(presented_bytes)

    def open_pull(self, repo: str, body: dict[str, object], headers: dict[str, str]) -> tuple[HTTPStatus, dict[str, object]]:
        status, response = self._open_pull(repo, body, headers)
        present = (lambda value: value) if self.show_secrets else describe_presented
        assertion = headers.get(ASSERTION_HEADER.lower())
        # One JSON line per request: what arrived and what the service decided.
        entry = {
            "time": utc_now(),
            "event": "pull_request",
            "repo": repo,
            "status": status.value,
            "authorization": present(headers.get("authorization")),
            "actorAssertion": present(assertion),
            "error": response.get("error"),
            "authenticatedAs": response.get("authenticatedAs"),
            "number": response.get("number"),
            "actor": response.get("actor"),
        }
        if self.show_secrets:
            entry["actorAssertionJwt"] = decode_jwt(assertion)
        self.log(json.dumps(entry))
        return status, response

    def _open_pull(self, repo: str, body: dict[str, object], headers: dict[str, str]) -> tuple[HTTPStatus, dict[str, object]]:
        accepted, reason, token_fingerprint = self.authenticate(headers.get("authorization"))
        if not accepted:
            return HTTPStatus.UNAUTHORIZED, {"error": reason, "tokenFingerprint": token_fingerprint}
        actor: dict[str, object] | None = None
        assertion = headers.get(ASSERTION_HEADER.lower())
        if assertion is not None:
            if self.verifier is None:
                return HTTPStatus.UNAUTHORIZED, {"error": "Actor assertions are not configured on this service"}
            try:
                actor = self.verifier.verify(assertion.strip())
            except AssertionError_ as exc:
                return HTTPStatus.UNAUTHORIZED, {"error": f"{ASSERTION_HEADER} rejected: {exc}"}
        title = str(body.get("title") or "Untitled change")
        description = str(body.get("body") or "")
        with self.lock:
            number = len(self.pulls) + 1
            record = {
                "number": number,
                "repo": repo,
                "title": title,
                "body": description,
                "receivedAt": utc_now(),
                "authenticatedAs": self.account,
                "tokenFingerprint": token_fingerprint,
                "actor": {
                    "subject": actor["subject"],
                    "atespace": actor["atespace"],
                    "name": actor["name"],
                    "uid": actor["uid"],
                }
                if actor
                else None,
            }
            if self.show_secrets:
                record["token"] = headers["authorization"].partition(" ")[2].strip()
                record["actorAssertion"] = assertion.strip() if assertion is not None else None
            self.pulls.append(record)
        return HTTPStatus.CREATED, {
            "number": number,
            "repo": repo,
            "authenticatedAs": self.account,
            "actor": record["actor"],
        }

    def list_pulls(self) -> list[dict[str, object]]:
        with self.lock:
            return list(self.pulls)


class SourceControlHandler(BaseHTTPRequestHandler):
    state: SourceControlState

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 65_536:
            raise ValueError("request body is too large")
        if not content_length:
            return {}
        value = json.loads(self.rfile.read(content_length))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/readyz", "/healthz"}:
            self._json(HTTPStatus.OK, {"status": "ready"})
        elif path == "/pulls":
            self._json(HTTPStatus.OK, {"pulls": self.state.list_pulls()})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if len(parts) != 3 or parts[0] != "repos" or parts[2] != "pulls":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            body = self._json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        headers = {name.lower(): value for name, value in self.headers.items()}
        status, response = self.state.open_pull(parts[1], body, headers)
        self._json(status, response)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_state() -> SourceControlState:
    token_file = Path(os.environ.get("TOKEN_FILE", "/run/source-control/token"))
    account = os.environ.get("ACCOUNT_NAME", "repo-workbench-bot")
    issuer = os.environ.get("ACTOR_JWT_ISSUER", "")
    audience = os.environ.get("ACTOR_JWT_AUDIENCE", "")
    verifier = None
    if issuer and audience:
        verifier = ActorAssertionVerifier(issuer, audience, os.environ.get("ACTOR_JWT_CA_FILE") or None)
    show_secrets = os.environ.get("DEMO_SHOW_SECRETS", "").lower() in {"1", "true", "yes"}
    return SourceControlState(token_file, account, verifier, show_secrets=show_secrets)


def run_server() -> None:
    port = int(os.environ.get("PORT", "80"))
    SourceControlHandler.state = build_state()
    server = ThreadingHTTPServer(("0.0.0.0", port), SourceControlHandler)
    print(f"Source-control service listening on :{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
