"""Verify chart integrity and image pinning without changing a cluster."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeLockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        self.lock_path = self.root / "scripts/runtime-lock.json"
        self.lock = json.loads(self.lock_path.read_text())
        self.version = self.lock["version"]
        self.chart_version = self.version.removeprefix("v")
        self.payload = self.root / "chart-payload"
        self.payload.write_bytes(b"verified chart fixture")
        self.digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        for chart in self.lock["charts"].values():
            chart["sha256"] = self.digest
        self.lock_path.write_text(json.dumps(self.lock))
        self.mock = self.root / "mock"
        self.mock.mkdir()
        self.env = dict(os.environ, PATH=str(self.mock) + os.pathsep + os.environ["PATH"],
                        MOCK_CHART_PAYLOAD=str(self.payload))
        self.write_helm('''#!/bin/bash
chart="${2##*/}"
shift 2
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) version="$2"; shift 2;;
    --destination) destination="$2"; shift 2;;
    *) exit 99;;
  esac
done
cp "$MOCK_CHART_PAYLOAD" "$destination/$chart-$version.tgz"
''')

    def tearDown(self):
        self.temp.cleanup()

    def write_helm(self, text):
        path = self.mock / "helm"
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, script, input_text=None):
        return subprocess.run(["bash", str(self.root / "scripts" / script)],
                              env=self.env, input=input_text, capture_output=True, text=True, timeout=15)

    def archives(self):
        return sorted((self.root / ".cache/charts").rglob("*.tgz"))

    def test_chart_download_checks_hash_and_reuses_valid_cache(self):
        result = self.run_script("download-charts.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {archive.name for archive in self.archives()},
            {f"{chart}-{self.chart_version}.tgz" for chart in self.lock["charts"]},
        )
        self.write_helm("#!/bin/bash\nexit 98\n")
        result = self.run_script("download-charts.sh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_chart_is_rejected_without_installable_or_temporary_files(self):
        self.payload.write_bytes(b"retagged chart")
        result = self.run_script("download-charts.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertEqual(self.archives(), [])
        self.assertEqual(list((self.root / ".cache/charts").rglob("download.*")), [])

    def test_corrupt_chart_cache_is_replaced_only_by_verified_content(self):
        self.assertEqual(self.run_script("download-charts.sh").returncode, 0)
        archive = self.archives()[0]
        archive.write_bytes(b"damaged cache")
        self.payload.write_bytes(b"retagged chart")
        result = self.run_script("download-charts.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(archive.read_bytes(), b"damaged cache")
        self.payload.write_bytes(b"verified chart fixture")
        result = self.run_script("download-charts.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(archive.read_bytes(), self.payload.read_bytes())

    def test_renderer_pins_images_preserves_other_fields_and_is_idempotent(self):
        image = f"ghcr.io/kagent-dev/substrate/ateapi:{self.version}"
        original = f"kind: Deployment\nspec:\n  image: '{image}'\n  image: \"{image}\"\n  command: [ateapi]\n"
        result = self.run_script("pin-images.sh", original)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(self.lock["images"][image]), 2)
        self.assertTrue(result.stdout.startswith("kind: Deployment\nspec:\n"))
        self.assertTrue(result.stdout.endswith("  command: [ateapi]\n"))
        repeated = self.run_script("pin-images.sh", result.stdout)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(repeated.stdout, result.stdout)

    def test_unknown_images_reject_the_entire_render_even_if_already_digest_pinned(self):
        for unknown in ("unexpected/image:latest", "unexpected/image@sha256:" + "a" * 64):
            with self.subTest(image=unknown):
                manifests = f"  image: ghcr.io/kagent-dev/substrate/ateapi:{self.version}\n"
                result = self.run_script("pin-images.sh", manifests + f"  image: {unknown}\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("absent from runtime-lock.json", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_lock_for_another_release_is_rejected(self):
        self.lock["version"] = "v0.5.0-alpha1"
        self.lock_path.write_text(json.dumps(self.lock))
        result = self.run_script("pin-images.sh", "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"must match {self.version}", result.stderr)


if __name__ == "__main__":
    unittest.main()
