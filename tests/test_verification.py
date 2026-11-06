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
    def test_rustfs_internal_files_are_normalized_to_snapshot_objects(self):
        root = "/data/ate-snapshots/atespaces/book-workbench/actor/snapshot"
        files = "\n".join([
            root + "/manifest.json/xl.meta",
            root + "/checkpoint.img.zstd/xl.meta",
            root + "/pages.img.zstd/upload-id/part.1",
            root + "/fs/fscheckpoint.pb.zstd/xl.meta",
        ])
        self.assertEqual(verify_chapters.snapshot_object_names(root, files), {
            "manifest.json", "checkpoint.img.zstd", "pages.img.zstd",
            "fs/fscheckpoint.pb.zstd",
        })

    def test_customized_router_is_rejected_before_test_changes(self):
        with self.assertRaisesRegex(RuntimeError, "lab's 5s parking"):
            verify_chapters.configured_parking("substrateIngress:\n  requestParking:\n    budget: \"15s\"\n", "30s")
        with self.assertRaisesRegex(RuntimeError, "lab's 5s parking"):
            verify_chapters.configured_parking("substrateIngress:\n  host: api\n", "30s")

    def test_temporary_budget_preserves_other_routes_and_indentation(self):
        original = ('policies:\n  substrateIngress:\n    host: api\n    requestParking:\n'
                    '      budget: "5s"\n    connectTargetPort: 8443\nother:\n  keep: true\n')
        changed = verify_chapters.configured_parking(original, "30s")
        self.assertEqual(changed, original.replace('budget: "5s"', 'budget: "30s"'))
        self.assertIn('budget: "5s"', original)

    def test_explicit_resume_uses_the_lab_cli_context(self):
        with patch.object(verify_deployed, "run_command") as command:
            verify_deployed.resume("repo-workbench-1")
        command.assert_called_once_with(str(verify_deployed.ROOT / ".bin/kubectl-ate"),
                                        "--context", verify_deployed.CONTEXT,
                                        "resume", "actor", "repo-workbench-1", "-a", "book-workbench")

    def test_lifecycle_suspend_defaults_to_sync_and_allows_async(self):
        with patch.object(verify_chapters.lab, "ate", return_value={}) as command:
            verify_chapters.lifecycle("suspend", "repo-workbench-1")
            verify_chapters.lifecycle("suspend", "repo-workbench-1", mode="async")
        self.assertEqual(command.call_args_list[0].args,
                         ("suspend", "actor", "repo-workbench-1", "-a", "book-workbench",
                          "--mode", "sync"))
        self.assertEqual(command.call_args_list[1].args,
                         ("suspend", "actor", "repo-workbench-1", "-a", "book-workbench",
                          "--mode", "async"))

    def test_router_config_waits_for_hot_reload_without_restarting_the_pod(self):
        config = "routes: []\n"
        responses = [
            subprocess.CompletedProcess([], 0, "loaded config from File\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "loaded config from File\nloaded config from File\n", ""),
        ]
        with patch.object(verify_chapters.lab, "run_command", side_effect=responses) as command:
            verify_chapters.set_router_config(config)
        calls = [call.args for call in command.call_args_list]
        self.assertTrue(any("patch" in call for call in calls))
        self.assertTrue(any("logs" in call for call in calls))
        self.assertFalse(any("rollout" in call for call in calls))

    def test_negative_command_assertion_rejects_unexpected_success(self):
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(["cli"], 0, "", "")):
            with self.assertRaisesRegex(RuntimeError, "unexpectedly succeeded"):
                verify_deployed.run_command("cli", success=False)


if __name__ == "__main__":
    unittest.main()
