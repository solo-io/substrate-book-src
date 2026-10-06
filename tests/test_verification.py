"""Check failure expectations and preservation of reader gateway configuration."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
with patch.dict(os.environ, {"KUBECTL_CONTEXT": "kind-unused-test"}):
    import verify_chapters
    import verify_deployed


class VerificationTest(unittest.TestCase):
    def test_customized_router_is_rejected_before_test_changes(self):
        with self.assertRaisesRegex(RuntimeError, "default parking"):
            verify_chapters.configured_parking("substrateIngress:\n  requestParking:\n    budget: 15s\n", "30s")

    def test_temporary_budget_preserves_other_routes_and_indentation(self):
        original = "policies:\n  substrateIngress:\n    host: api\nother:\n  keep: true\n"
        changed = verify_chapters.configured_parking(original, "30s")
        self.assertIn("  substrateIngress:\n    requestParking:\n      budget: 30s\n    host: api", changed)
        self.assertTrue(changed.endswith("other:\n  keep: true\n"))
        self.assertNotIn("requestParking", original)

    def test_negative_command_assertion_rejects_unexpected_success(self):
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(["cli"], 0, "", "")):
            with self.assertRaisesRegex(RuntimeError, "unexpectedly succeeded"):
                verify_deployed.run_command("cli", success=False)


if __name__ == "__main__":
    unittest.main()
