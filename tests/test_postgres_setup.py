"""Verify the alpha2 kind PostgreSQL bootstrap without contacting a cluster."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PostgresSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        shutil.copytree(ROOT / "manifests", self.root / "manifests")
        self.mock = self.root / "mock"
        self.mock.mkdir()
        self.log = self.root / "kubectl.log"
        self.sql = self.root / "setup.sql"
        self.env = dict(
            os.environ,
            PATH=str(self.mock) + os.pathsep + os.environ["PATH"],
            KIND_CLUSTER_NAME="postgres-test",
            MOCK_KUBE_LOG=str(self.log),
            MOCK_SQL=str(self.sql),
            MOCK_FAIL_ON="",
        )
        kubectl = self.mock / "kubectl"
        kubectl.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
safe = []
for arg in args:
    if arg.startswith("--from-literal="):
        option, literal = arg.split("=", 1)
        key = literal.split("=", 1)[0]
        safe.append(f"{option}={key}=<redacted>")
    else:
        safe.append(arg)
with Path(os.environ["MOCK_KUBE_LOG"]).open("a") as stream:
    stream.write(json.dumps(safe) + "\\n")
if os.environ.get("MOCK_FAIL_ON") in " ".join(args) and os.environ.get("MOCK_FAIL_ON"):
    sys.exit(42)
if "exec" in args:
    Path(os.environ["MOCK_SQL"]).write_text(sys.stdin.read())
if "create" in args and "secret" in args:
    print("apiVersion: v1\\nkind: Secret\\nmetadata:\\n  name: fixture")
elif args[-2:] == ["-f", "-"]:
    sys.stdin.read()
''')
        kubectl.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_setup(self, env=None):
        return subprocess.run(
            ["bash", str(self.root / "scripts/setup-postgres.sh")],
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_context_order_secret_names_and_keys(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(all(call[:2] == ["--context", "kind-postgres-test"] for call in calls))

        def position(*terms):
            return next(i for i, call in enumerate(calls) if all(term in call for term in terms))

        manifest = str(self.root / "manifests/postgres-kind.yaml")
        ordered = [
            position("apply", manifest),
            position("rollout", "statefulset/postgres"),
            position("exec", "postgres-0", "psql"),
            position("create", "substrate-postgres-readwrite"),
            position("create", "substrate-postgres-owner"),
        ]
        self.assertEqual(ordered, sorted(ordered))

        readwrite = calls[ordered[3]]
        owner = calls[ordered[4]]
        self.assertIn("--from-literal=readWriteConnectionString=<redacted>", readwrite)
        self.assertIn("--from-literal=ownerConnectionString=<redacted>", owner)
        self.assertNotIn("postgresql://", self.log.read_text())
        self.assertEqual(self.sql.read_text(), (self.root / "manifests/postgres-setup.sql").read_text())

    def test_rerun_converges(self):
        for _ in range(2):
            result = self.run_setup()
            self.assertEqual(result.returncode, 0, result.stderr)
        commands = [" ".join(call) for call in self.calls()]
        self.assertEqual(sum("create secret generic substrate-postgres-readwrite" in call
                             for call in commands), 2)
        self.assertEqual(sum("create secret generic substrate-postgres-owner" in call
                             for call in commands), 2)

    def test_failures_stop_before_creating_secrets(self):
        for failure in ("rollout status", "exec -i"):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                env = dict(self.env, MOCK_FAIL_ON=failure)
                result = self.run_setup(env)
                self.assertEqual(result.returncode, 42)
                commands = [" ".join(call) for call in self.calls()]
                self.assertFalse(any("create secret" in call for call in commands))


if __name__ == "__main__":
    unittest.main()
