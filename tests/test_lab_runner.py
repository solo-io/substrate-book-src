"""Verify destructive-test isolation and cleanup on failures."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LabRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        (self.root / "tests").mkdir()
        # Python 3.12 rejects discovery with zero tests; the fixture must collect one.
        (self.root / "tests/test_fixture.py").write_text(
            "import unittest\nclass FixtureTest(unittest.TestCase):\n"
            "    def test_fixture(self): pass\n")
        self.mock = self.root / "mock"
        self.mock.mkdir()
        self.trace = self.root / "trace"
        self.env = dict(os.environ, PATH=str(self.mock) + os.pathsep + os.environ["PATH"],
                        KIND_CLUSTER_NAME="isolated-regression", LAB_RESULTS_DIR=str(self.root / "results"),
                        MOCK_TRACE=str(self.trace), MOCK_EXISTING="", MOCK_FAIL="")
        self.write(self.mock / "kind", '''#!/bin/bash
if [[ "$1 $2" == "get clusters" ]]; then printf '%s\\n' "$MOCK_EXISTING"; fi
''')
        self.write(self.mock / "kubectl", '#!/bin/bash\nif [[ "$1 $2" == "config current-context" ]]; then echo kind-original; fi\n')
        for script, marker in (("setup-kind.sh", "setup"), ("verify-example.sh", "demo"),
                               ("verify-chapters.sh", "chapters"), ("collect-diagnostics.sh", "diagnostics"),
                               ("teardown-example.sh", "teardown"), ("delete-kind.sh", "delete-kind")):
            self.write(self.root / "scripts" / script,
                       f'#!/bin/bash\necho {marker} >> "$MOCK_TRACE"\n'
                       f'if [[ "$MOCK_FAIL" == {marker} ]]; then exit 7; fi\n')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def run_lab(self, *args):
        return subprocess.run(["bash", str(self.root / "scripts/test-lab.sh"), *args],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def calls(self):
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def test_existing_cluster_is_never_changed(self):
        self.env["MOCK_EXISTING"] = "another-cluster\nisolated-regression"
        result = self.run_lab()
        self.assertEqual(result.returncode, 2)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_partial_setup_failure_still_deletes_owned_cluster(self):
        self.env["MOCK_FAIL"] = "setup"
        result = self.run_lab()
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.calls(), ["setup", "diagnostics", "teardown", "delete-kind"])

    def test_regression_failure_collects_logs_then_cleans_up(self):
        self.env["MOCK_FAIL"] = "chapters"
        result = self.run_lab()
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.calls(), ["setup", "demo", "chapters", "diagnostics", "teardown", "delete-kind"])

    def test_cleanup_failure_fails_an_otherwise_successful_run(self):
        self.env["MOCK_FAIL"] = "teardown"
        result = self.run_lab()
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.calls()[-1], "delete-kind")

    def test_keep_cluster_defers_deletion(self):
        result = self.run_lab("--keep-cluster")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["setup", "demo", "chapters", "diagnostics"])
        self.assertIn("kind-isolated-regression", result.stdout)


if __name__ == "__main__":
    unittest.main()
