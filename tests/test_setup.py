"""Check release downloads and lab isolation without contacting a cluster."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / "scripts/runtime-lock.json").read_text())["version"]
CLI = f'#!/bin/sh\necho "kubectl-ate version {VERSION} commit=test linux/amd64"\n'


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

    def test_release_commit_identifies_a_cli_with_a_pseudo_version(self):
        cli = self.bin / "kubectl-ate"
        release_commit = re.search(r'^SUBSTRATE_COMMIT="([0-9a-f]{40})"$',
                                   (self.root / "scripts/lib.sh").read_text(), re.MULTILINE)[1]
        cli.write_text(f'#!/bin/sh\necho "kubectl-ate version v0.0.0-test commit={release_commit}-dirty"\n')
        cli.chmod(0o755)
        result = subprocess.run(["bash", "-c", 'source "$1"; ensure_cli', "bash",
                                str(self.root / "scripts/lib.sh")], env=self.env,
                                capture_output=True, text=True, timeout=15)
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
        cli.write_text(f'#!/bin/sh\ncase "$*" in\n  --version) echo "kubectl-ate version {VERSION} commit=test linux/amd64";;\n  *"get actors"*) echo \'{{"actors": []}}\';;\n  *) exit 1;;\nesac\n')
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
        self.write_command("kubectl", "#!/bin/sh\nexit 98\n")
        result = self.run_script("deploy-example.sh", env=dict(self.env, BOOK_AGENT_IMAGE="agent:tag"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set BOOK_AGENT_IMAGE, BOOK_WEBAPP_IMAGE, and BOOK_SOURCE_CONTROL_IMAGE together",
                      result.stderr)
        result = self.run_script("deploy-example.sh", env=dict(self.env, BOOK_AGENT_IMAGE="agent:tag",
                                                              BOOK_WEBAPP_IMAGE="webapp:tag",
                                                              BOOK_SOURCE_CONTROL_IMAGE="sc:tag"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("demo image must be pinned by digest", result.stderr)

    def test_platform_bootstrap_order_matches_release_dependencies(self):
        setup = (ROOT / "scripts/setup-kind.sh").read_text()
        platform = (ROOT / "manifests/platform.yaml.tmpl").read_text()
        markers = [
            "helm upgrade --install substrate-crds",
            "for name in service-dns-ca-pool pod-identity-ca-pool postgres-ca-pool",
            "helm upgrade --install substrate-podcert",
            'wait_for_trust_bundle "${signer}"',
            '"${BOOK_EXAMPLES_ROOT}/scripts/setup-postgres.sh"',
            "make-jwt-pool",
            "helm upgrade --install substrate ",
        ]
        positions = [setup.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("meta.helm.sh/release-name=substrate", setup)
        self.assertIn('--post-renderer "${BOOK_EXAMPLES_ROOT}/scripts/pin-images.sh"', setup)
        self.assertIn('ate.dev/api-access: "true"', platform)
        self.assertNotIn("actorIdentityJWTProvider", setup)
        self.assertIn("jwtProviders:", setup)
        template = (ROOT / "manifests/actor-template.yaml.tmpl").read_text()
        self.assertNotIn("sandboxConfig:", template)
        self.assertIn("workerSelector:", template)

    def test_credential_brokering_lab_pieces_are_wired_together(self):
        values = (ROOT / "manifests/substrate-values.yaml").read_text()
        self.assertIn("atespace: book-workbench", values)
        self.assertIn("allowedNamespaces: [workbench-secrets]", values)
        platform = (ROOT / "manifests/platform.yaml.tmpl").read_text()
        self.assertIn("name: workbench-secrets", platform)
        self.assertIn("secretName: source-control-token", platform)
        self.assertIn("signerName: servicedns.podcert.ate.dev/identity", platform)
        template = (ROOT / "manifests/actor-template.yaml.tmpl").read_text()
        self.assertIn("http://source-control.book-workbench.svc.cluster.local/repos/repo-workbench/pulls", template)
        deployment = (ROOT / "scripts/deploy-example.sh").read_text()
        self.assertIn("git-token -n workbench-secrets", deployment)
        self.assertIn("source-control-token", deployment)
        teardown = (ROOT / "scripts/teardown-example.sh").read_text()
        self.assertIn("delete namespace workbench-secrets", teardown)

    def test_demo_prep_refuses_a_cluster_without_the_locked_api_server(self):
        cli = self.bin / "kubectl-ate"
        cli.write_text(CLI)
        cli.chmod(0o755)
        self.env["MOCK_KUBE_LOG"] = str(self.root / "kube.log")
        self.write_command("kubectl", """#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_KUBE_LOG"
case "$*" in
  *"get --raw=/readyz"*) exit 0;;
  *"get deployment/ate-api-server"*) printf 'ghcr.io/kagent-dev/substrate/ateapi:v0.5.0-alpha3@sha256:abc';;
  *) exit 1;;
esac
""")
        result = self.run_script("prep-credential-demo.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not run the locked", result.stderr)
        calls = (self.root / "kube.log").read_text().splitlines()
        self.assertTrue(calls and all(call.startswith("--context kind-isolated-lab ") for call in calls))
        self.assertFalse(any("delete" in call or "apply" in call or "restart" in call for call in calls))

    def test_live_demo_policies_exist_and_default_to_the_demo_lab(self):
        script = (ROOT / "scripts/live-demo.sh").read_text()
        self.assertIn('KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-book-v060-cred}"', script)
        policies = set(re.findall(r"demo/policies/[\w-]+\.yaml", script))
        self.assertEqual(len(policies), 3)
        for policy in policies:
            self.assertTrue((ROOT / policy).is_file(), policy)

    def test_live_demo_code_excerpts_match_the_source(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/live-demo.sh"), "--check-snippets"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("code excerpts match the source", result.stdout)

    def test_demo_env_must_be_sourced_and_names_the_lab_context(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/demo-env.sh")], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        result = subprocess.run(["bash", "-c", 'source "$1"; echo "$CTX"; type wb publish >/dev/null', "bash",
                                 str(ROOT / "scripts/demo-env.sh")],
                                env=dict(os.environ, KIND_CLUSTER_NAME="demo-lab"), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kind-demo-lab", result.stdout)

    def test_golden_snapshot_wait_allows_one_cold_asset_retry(self):
        deployment = (ROOT / "scripts/deploy-example.sh").read_text()
        self.assertIn("deadline=$((SECONDS + 600))", deployment)

    def test_openbao_setup_refuses_a_provider_checkout_at_another_commit(self):
        cli = self.bin / "kubectl-ate"
        cli.write_text(CLI)
        cli.chmod(0o755)
        log = self.root / "calls.log"
        for command in ("kubectl", "helm", "docker", "go"):
            self.write_command(command, f'#!/bin/sh\necho "{command} $*" >> "{log}"\nexit 1\n')
        checkout = self.root / "credprovider-openbao"
        checkout.mkdir()
        git = ["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "other"], check=True)
        self.env["OPENBAO_PROVIDER_SOURCE_DIR"] = str(checkout)
        result = self.run_script("setup-openbao.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("want ", result.stderr)
        self.assertFalse(log.exists(), "setup touched kubectl, helm, docker or go before checking the pin")


class OpenBaoDemoTest(unittest.TestCase):
    # Two credentialProviders lists, as the v1.0.0-rc1 chart renders them.
    GATEWAY_CONFIG = """binds:
- listeners:
  - protocol: HTTPS
    routes:
    - policies:
        substrateEgress:
          credentialProviders:
          - uriAuthority: k8s.io
            target:
              host: k8s-credential-provider.ate-system.svc:50051
              policies:
                backendTLS:
                  cert: /run/podidentity.podcert.ate.dev/credential-bundle.pem
                  key: /run/podidentity.podcert.ate.dev/credential-bundle.pem
                  root: /run/servicedns.podcert.ate.dev/trust-bundle.pem
  - protocol: HTTP
    routes:
    - policies:
        substrateEgress:
          credentialProviders:
          - uriAuthority: k8s.io
            target:
              host: k8s-credential-provider.ate-system.svc:50051
              policies:
                backendTLS:
                  cert: /run/podidentity.podcert.ate.dev/credential-bundle.pem
                  key: /run/podidentity.podcert.ate.dev/credential-bundle.pem
                  root: /run/servicedns.podcert.ate.dev/trust-bundle.pem
  - protocol: TLS
"""

    def gateway_config(self, text, **env):
        return subprocess.run(["bash", str(ROOT / "scripts/openbao-gateway-config.sh")], input=text,
                              capture_output=True, text=True, timeout=15, env={**os.environ, **env})

    def test_gateway_config_adds_the_provider_after_k8s_in_both_lists(self):
        result = self.gateway_config(self.GATEWAY_CONFIG)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertEqual(out.count("- uriAuthority: openbao\n"), 2)
        self.assertEqual(out.count("host: openbao-credential-provider.credprovider-openbao.svc:50051\n"), 2)
        self.assertEqual(out.count("- uriAuthority: k8s.io\n"), 2)
        # The new entry keeps the gateway's mTLS client identity and trust root.
        self.assertEqual(out.count("root: /run/servicedns.podcert.ate.dev/trust-bundle.pem\n"), 4)
        self.assertLess(out.index("uriAuthority: openbao"), out.index("protocol: HTTP\n"))
        self.assertEqual(self.gateway_config(out).stdout, out, "patching twice must change nothing")

    def test_gateway_config_adds_a_second_provider_next_to_the_first(self):
        first = self.gateway_config(self.GATEWAY_CONFIG).stdout
        result = self.gateway_config(
            first, OPENBAO_PROVIDER_NAME="openbao-actor",
            OPENBAO_PROVIDER_ADDRESS="openbao-credential-provider.credprovider-openbao-actor.svc:50051")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertEqual(out.count("- uriAuthority: openbao\n"), 2)
        self.assertEqual(out.count("- uriAuthority: openbao-actor\n"), 2)
        self.assertEqual(out.count("host: openbao-credential-provider.credprovider-openbao-actor.svc:50051\n"), 2)

    def test_gateway_config_refuses_an_unexpected_shape_without_output(self):
        one_list = self.GATEWAY_CONFIG.split("  - protocol: HTTP\n")[0]
        for text in (one_list, "binds: []\n"):
            result = self.gateway_config(text)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_live_demo_openbao_policies_exist_and_name_the_configured_uris(self):
        script = (ROOT / "scripts/live-demo.sh").read_text()
        lib = (ROOT / "scripts/lib.sh").read_text()
        self.assertIn('OPENBAO_CREDENTIAL_URI="ate-secret://${OPENBAO_PROVIDER_NAME}/kv/${OPENBAO_SECRET_PATH}/token"', lib)
        self.assertIn('OPENBAO_ACTOR_CREDENTIAL_URI="ate-secret://${OPENBAO_ACTOR_PROVIDER_NAME}/kv/${OPENBAO_SECRET_PATH}/token"', lib)
        for provider in ("openbao", "openbao-actor"):
            policies = set(re.findall(rf"demo/policies/{provider}/[\w-]+\.yaml", script))
            self.assertEqual(len(policies), 2, provider)
            for policy in policies:
                self.assertTrue((ROOT / policy).is_file(), policy)
            for policy, atespace in (("2-inject", "book-workbench"), ("3-other-atespace", "team-a")):
                uri = f"ate-secret://{provider}/kv/secret/atespaces/{atespace}/source-control/token"
                self.assertIn(f"credentialUri: {uri}\n", (ROOT / f"demo/policies/{provider}/{policy}.yaml").read_text())

    def test_openbao_actor_values_grant_the_identity_named_in_lib(self):
        lib = (ROOT / "scripts/lib.sh").read_text()
        values = (ROOT / "manifests/substrate-values-openbao-actor.yaml").read_text()
        self.assertIn('OPENBAO_ACTOR_PROVIDER_NAMESPACE="credprovider-openbao-actor"', lib)
        self.assertIn('OPENBAO_ACTOR_PROVIDER_SPIFFE_ID="spiffe://cluster.local/ns/${OPENBAO_ACTOR_PROVIDER_NAMESPACE}/sa/openbao-credential-provider"', lib)
        self.assertIn("- --egress-gateway-spiffe-id=spiffe://cluster.local/ns/credprovider-openbao-actor/sa/openbao-credential-provider\n",
                      values)
        # The default provider needs no grant.
        self.assertNotIn("ns/credprovider-openbao/", values)

if __name__ == "__main__":
    unittest.main()
