# Validation: kagent-dev Substrate v1.0.0

Release validation completed on November 6, 2026. The audit and clean-cluster run used the published kagent-dev artifacts, not a local Substrate build. The lab moved from v1.0.0-rc1 to v1.0.0 through rc2 and rc4, which were each validated on November 5, 2026; this record replaces those interim notes.

## Runtime

- Release: [kagent-dev/substrate v1.0.0](https://github.com/kagent-dev/substrate/releases/tag/v1.0.0), commit `563df06f6be751c101539986896c17c55aa22a9d`. It contains upstream [`agent-substrate/substrate` v1.0.0](https://github.com/agent-substrate/substrate/releases/tag/v1.0.0) (`b34fbee742906494b5eb4f1f36987176bf8770a4`) plus the kagent-dev release changes, including the Helm charts.
- CLI: the SHA-256 values of all four Darwin/Linux and AMD64/ARM64 binaries are recorded in `scripts/cli-checksums.json` and match the release's `.sha256` files; the host binary reported the release commit.
- Charts: the published `substrate-crds`, `substrate-podcert`, `substrate-atelet`, and `substrate` OCI charts at `1.0.0` were pulled and checked against both their archive checksums and OCI digests in `scripts/runtime-lock.json`. The release notes list only three charts, but `substrate-atelet` is published at `1.0.0` alongside them. The exact packages rendered successfully through the image-locking post-renderer with the lab values.
- Images: every kagent-dev platform image, plus `ateworker-gvisor`, resolved to a new immutable multi-platform index digest with `linux/amd64` and `linux/arm64/v8` variants. The router and egress gateway still use `cr.agentgateway.dev/agentgateway:v1.7.0` at the same digest as rc1. The third-party images are unchanged.
- Companion images: the agent, web application, and source-control service were built locally. No Substrate source checkout, Go compiler, or platform image build was used for Substrate itself. The OpenBao providers are still built from `../credprovider-openbao` at the pinned commit.

## Changes from v1.0.0-rc4

None that affect the lab. The six commits after rc4 change copyright headers, contributor and governance documentation, the project website, and a boilerplate checker. Apart from `version` and `appVersion`, the four chart packages are identical to rc4, including every default value. The images were rebuilt from the new commit, so their digests changed.

## Changes from v1.0.0-rc1 that affect the lab

- **atelet is its own release.** rc4 adds a `substrate-atelet` chart and deprecates the `substrate` chart's bundled atelet (`atelet.enabled`, still `true` by default), so atelet can be upgraded before the API server as the version skew policy requires. Setup now creates a `rustfs-credentials` Secret from the bundled RustFS's default lab credentials, installs `substrate-atelet`, and then installs `substrate` with `atelet.enabled=false`. `manifests/substrate-values.yaml` configures both releases: its new `objectStore` block points atelet's node object-store plugin at the bundled RustFS. Setup verifies that the `atelet` DaemonSet belongs to the `substrate-atelet` release and uses the locked atelet and object-store plugin images. Upgrades through `setup-openbao.sh` and `teardown-openbao.sh` keep the bundled atelet disabled. atelet now runs at `system-cluster-critical` instead of the chart's own PriorityClass.
- **Router parking budget.** The chart now renders `requestParking.budget` from `atenetRouter.requestParking.budget`, whose default rose from 5 seconds (rc2) to 60 seconds (rc3). The lab sets it to `5s` so Chapter 4's capacity example returns its `503` quickly; Chapters 3 and 4 now say the release default is 60 seconds and the lab shortens it. The Chapter 4 regression replaces the lab's rendered `"5s"` with its test-only `"30s"` and refuses any other starting value. rc3 also adds `atenetRouter.requestTimeout` (60 seconds) for each Substrate ingress API call; the lab keeps that default.
- **gVisor.** The chart's default `SandboxConfig` moved to version `v7` (gVisor release `20261102.0`). The lab's Worker, snapshot, and restore checks pass on it unchanged.
- **Actor JWT verification.** The API server no longer mounts the JWKS file; the credential exchange verifies Actor JWTs through OIDC discovery. Chapter 6's injected Actor JWT and the `openbao-actor` provider's per-Actor OpenBao login both still work.
- **Stricter ActorTemplate validation.** `capabilities.add` is limited to a fixed set, mounts are `nodev`, and `/dev`, `/proc`, and `/sys` are rejected as mount paths. `/tmp` and `/dev/shm` are tmpfs, and the gVisor durable-directory root is mode `0777`. The `repo-workbench` template uses none of the restricted settings and is unchanged.
- **Unchanged.** The API group (`ate.dev/v1`), the router's mTLS-only ingress and SPIFFE allowlist, `durableSnapshot.state`, the Worker's reported 960 Mi of a 1 GiB Pod, the generic `actor egress policy denied` refusal body, and the chart's single bundled `k8s.io` credential provider (so `openbao-gateway-config.sh` remains a temporary bridge).

The kagent-dev diff also changes the separate Go `atenet-router` implementation so a `FailedPrecondition` from `ResumeActor` fails fast instead of parking. The lab's router is AgentGateway, so that change does not apply to it.

## Validation status

The release was validated on macOS 15.7.7 x86_64 (Darwin 24.6.0), Docker Engine 29.8.0, kind 0.31.0, kubectl 1.36.1, Helm 3.16.2, and host Python 3.9.6.

The clean-cluster command was:

```shell
KIND_CLUSTER_NAME=book-v100-clean ./scripts/test-lab.sh
```

All 65 local unit tests passed. The runner then created a fresh Kubernetes 1.35.0 kind cluster, installed only the locked v1.0.0 charts and platform images, and passed the complete Chapter 3–6 regression: Worker reuse and isolated Actor state through the web application's mTLS router path, capacity exhaustion with the lab's 5-second parked request returning 503, the original parked request succeeding under the 30-second test budget, Worker-loss recovery, exact MEMORY and VOLUMES snapshot contents, asynchronous and synchronous suspend, restart of the separately released atelet, tag forks and deletion ownership, Actor UID continuity, scoped Secret injection, denial of an ungranted Secret, Actor JWT issuance and verification, and policy continuity across suspend and resume. Teardown left the Atespace empty, and the runner deleted the disposable cluster successfully. The retained results are under `.test-results/book-v100-clean`.

On a separate lab created with `KIND_CLUSTER_NAME=book-v100 ./scripts/setup-kind.sh`, these demos ran hands-free with `--auto 0 --logs off`:

| Demo | Result |
| --- | --- |
| `./scripts/live-demo.sh --check-snippets` | Code excerpts match the source |
| `./scripts/live-demo.sh` (Kubernetes Secrets) | 401 with the placeholder, 201 with the injected Secret, 201 with an Actor JWT the service verified |
| `./scripts/live-demo.sh --broker openbao` (after `setup-openbao.sh`) | 401; 201 with OpenBao's audit log naming the provider and the Actor; 403 refused by the provider before OpenBao; 201 with a verified Actor JWT |
| `./scripts/live-demo.sh --broker openbao-actor` | 401; 201 with OpenBao's audit log naming the Actor's own login and read; 403 refused by OpenBao's policy; 201 with a verified Actor JWT |

`teardown-openbao.sh` then restored the `substrate` release to `manifests/substrate-values.yaml`, and `prep-credential-demo.sh` returned the lab to the reader starting state: no Actors, the `repo-workbench` golden tag ready, one idle Worker, atelet still owned by `substrate-atelet`, and a source-control service without `DEMO_SHOW_SECRETS`.

`kubectl ate proxy actor` was not retested; the rc1 limitation on the Kubernetes 1.35.0 node is assumed to remain, so the lab still does not list `atenet-client` in the router allowlist.

The browser workflow, HTTPS interception, Linux/WSL2 hosts, ARM64 hosts, and GitHub-hosted CI remain separate coverage surfaces.
