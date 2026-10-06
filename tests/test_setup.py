"""Check release downloads and lab isolation without contacting a cluster."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = '#!/bin/sh\necho "kubectl-ate version v0.4.0-alpha1 commit=test linux/amd64"\n'


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        self.bin = self.root / ".bin"
        self.bin.mkdir()
        self.mock = self.root / "mock"
        self.mock.mkdir()
        self.env = dict(os.environ, PATH=str(self.mock) + os.pathsep + os.environ["PATH"],
                        KIND_CLUSTER_NAME="isolated-lab", KUBECTL_CONTEXT="kind-wrong-cluster")
        self.write_command("uname", '#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        self.payload = self.root / "payload"
        self.payload.write_text(CLI)
        self.env["MOCK_PAYLOAD"] = str(self.payload)
        self.write_command("curl", '''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then cp "$MOCK_PAYLOAD" "$2"; exit; fi
  shift
done
exit 1
''')
        digest = hashlib.sha256(CLI.encode()).hexdigest()
        (self.root / "scripts/cli-checksums.json").write_text(json.dumps({
            "kubectl-ate-linux-amd64": digest,
            "kubectl-ate-linux-arm64": digest,
        }))

    def tearDown(self):
        self.temp.cleanup()

    def write_command(self, name, text):
        path = self.mock / name
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, name, *args, env=None):
        return subprocess.run(["bash", str(self.root / "scripts" / name), *args],
                              env=env or self.env, capture_output=True, text=True, timeout=15)

    def test_download_checks_hash_and_reuses_valid_files(self):
        result = self.run_script("download-cli.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.bin / "kubectl-ate").read_text(), CLI)
        self.write_command("curl", "#!/bin/sh\nexit 98\n")
        result = self.run_script("download-cli.sh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_corrupt_download_does_not_replace_existing_cli(self):
        (self.bin / "kubectl-ate").write_text("old CLI")
        self.payload.write_text("corrupt download")
        result = self.run_script("download-cli.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertEqual((self.bin / "kubectl-ate").read_text(), "old CLI")
        self.assertEqual(list(self.bin.glob("kubectl-ate.*")), [])

    def test_invalid_arguments_do_not_download(self):
        self.write_command("curl", "#!/bin/sh\nexit 98\n")
        result = self.run_script("download-cli.sh", "--unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_context_is_derived_from_the_named_cluster(self):
        self.write_command("kubectl", '#!/bin/sh\nprintf "%s\\n" "$*"\n')
        cli = self.bin / "kubectl-ate"
        cli.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
        cli.chmod(0o755)
        result = subprocess.run(["bash", "-c",
                                 'source "$1"; kube get pods; ate get actors', "bash",
                                 str(self.root / "scripts/lib.sh")],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "--context kind-isolated-lab get pods", "--context kind-isolated-lab get actors"])

    def test_wsl_dns_override_preserves_other_go_options(self):
        for kernel, expected in (("6.18-microsoft-standard-WSL2", "http2client=0,netedns0=0"),
                                 ("7.0.0-generic", "http2client=0")):
            with self.subTest(kernel=kernel):
                self.write_command("uname", f"#!/bin/sh\necho {kernel}\n")
                result = subprocess.run(["bash", "-c", 'source "$1"; printf "%s" "$GODEBUG"',
                                         "bash", str(self.root / "scripts/lib.sh")],
                                        env=dict(self.env, GODEBUG="http2client=0"),
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_api_dns_patch_uses_lab_context_and_waits_for_rollout(self):
        self.write_command("kubectl", "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
        result = subprocess.run(["bash", "-c", 'source "$1"; configure_api_dns', "bash",
                                 str(self.root / "scripts/lib.sh")],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[:2] == ["--context", "kind-isolated-lab"] for call in calls))
        patch = json.loads(calls[0][calls[0].index("-p") + 1])
        self.assertEqual(patch["spec"]["template"]["spec"]["dnsConfig"]["options"],
                         [{"name": "ndots", "value": "4"}])
        self.assertEqual(calls[1][2:4], ["rollout", "status"])

    def test_cleanup_removes_workloads_before_certificate_requests_and_namespace(self):
        cli = self.bin / "kubectl-ate"
        cli.write_text('#!/bin/sh\ncase "$*" in\n  --version) echo "kubectl-ate version v0.4.0-alpha1 commit=test linux/amd64";;\n  *"get actors"*) echo \'{"actors": []}\';;\n  *) exit 1;;\nesac\n')
        cli.chmod(0o755)
        self.env["MOCK_KUBE_LOG"] = str(self.root / "kube.log")
        self.write_command("kubectl", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$MOCK_KUBE_LOG"\n')
        result = self.run_script("teardown-example.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "kube.log").read_text().splitlines()
        self.assertTrue(all(call.startswith("--context kind-isolated-lab ") for call in calls))
        operations = ("delete workerpool", "delete deployment", "wait --for=delete pod",
                      "delete podcertificaterequests", "delete namespace")
        positions = [next(i for i, call in enumerate(calls) if operation in call)
                     for operation in operations]
        self.assertEqual(positions, sorted(positions))

    def test_supplied_demo_images_must_be_a_pair_and_use_digests(self):
        cli = self.bin / "kubectl-ate"
        cli.write_text(CLI)
        cli.chmod(0o755)
        self.write_command("docker", "#!/bin/sh\nexit 98\n")
        result = self.run_script("deploy-example.sh", env=dict(self.env, BOOK_AGENT_IMAGE="agent:tag"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set BOOK_AGENT_IMAGE and BOOK_WEBAPP_IMAGE together", result.stderr)
        result = self.run_script("deploy-example.sh", env=dict(self.env, BOOK_AGENT_IMAGE="agent:tag",
                                                              BOOK_WEBAPP_IMAGE="webapp:tag"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("demo image must be pinned by digest", result.stderr)


if __name__ == "__main__":
    unittest.main()
