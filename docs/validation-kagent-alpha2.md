# Validation: kagent-dev Substrate v0.5.0-alpha2

Validated on October 12, 2026, using macOS 15.7.7 on AMD64, Docker Desktop 29.8.0, kind 0.31.0, and a single-node Linux AMD64 Kubernetes 1.35.0 cluster. The existing `book-workbench-alpha4` cluster was not changed. ARM64, Linux/WSL2 hosts, browsers, and GitHub-hosted CI were not exercised in this validation.

## Runtime

- Release: [kagent-dev/substrate v0.5.0-alpha2](https://github.com/kagent-dev/substrate/releases/tag/v0.5.0-alpha2), commit `9c4b1fb52212028100fa02c8a7bc4526575ea37e`.
- CLI: released macOS AMD64 and Linux AMD64/ARM64 binaries, verified against `scripts/cli-checksums.json`. The published binary reports the release commit with a `-dirty` suffix.
- Kubernetes client: `kubectl` 1.36.1; kind node image: Kubernetes 1.35.0, pinned by digest in `scripts/lib.sh`.
- Helm: 3.16.2.
- Images: all six released Substrate image indexes were resolved to their registry digests and confirmed to publish Linux AMD64 and ARM64 manifests. The checked-in PostgreSQL and third-party images remain digest-pinned.
- Only the companion agent and web application images were built locally. No Substrate source checkout or local platform build was used.

The three chart packages matched both their published OCI digests and downloaded archive checksums:

| Chart | OCI digest | Archive SHA-256 |
| --- | --- | --- |
| `substrate-crds` | `sha256:328bd7a125d4e3f223309851cee74c8cb7a53c5613523f7d2bb7cd5b56aebc49` | `e54d159e6b253ec2d12f0946f543896262167dee865534e0ca7a259763e141fe` |
| `substrate-podcert` | `sha256:440e0052e5a8b8f96e4dd7baef90aaabafa7ab1e581364bb8035cc6a1cf17b13` | `89180dc1a1073d86f9de105756dd4e69217cd188df661d8c6bdaf9c1c985c436` |
| `substrate` | `sha256:361504a126e02473b78a0e9e10b1c613b62a2ec7429419cf6a83e808b9808a34` | `b303bffd003655f3e5623773aecb04e1efe4f65e2192545ebd540043ab076b0c` |

## Automated validation

The final disposable run used a new cluster named `book-alpha2-clean`:

```shell
KIND_CLUSTER_NAME=book-alpha2-clean ./scripts/test-lab.sh
```

All 36 unit tests passed. Setup then installed three Helm releases, created the three CA pools, prepared the checked-in TLS-enabled PostgreSQL StatefulSet, ran the idempotent role/schema SQL, created the separate read/write and owner connection Secrets, and installed the main chart with PostgreSQL client-certificate projection enabled. The API server used the exact locked alpha2 image, and the router/egress Pods each contained only AgentGateway.

The deployed demo verifier passed Actor creation/listing, independent identity/state, stale-step rejection, Worker reuse, Full restoration, completion, and deletion. The Chapter 4–6 suite passed capacity exhaustion, default parking timeout, parked-request handoff, Full/Data snapshot contents, Full and Data pause behavior, tag forks and snapshot deletion, Worker loss and recovery, UID preservation, HTTP policy readback, and credential-effect admission. The runner collected bounded diagnostics, deleted its Actors and namespace, deleted `book-alpha2-clean`, and restored `kind-book-workbench-alpha4`.

The diagnostics were not warning-free. The two API replicas raced once while creating `schema_migrations`, PostgreSQL rejected one duplicate Atespace insert during the lifecycle suite, and the chart's OpenTelemetry exporters logged connection failures because this lab does not install a collector. The induced Worker-crash test also produced the expected gVisor control-server errors during recovery. These messages did not restart a platform Pod or fail an assertion, but they are worth retaining as alpha2 observations rather than treating this as a clean-log validation.

## Corrections discovered during validation

- Alpha2 installs the pod-certificate controller from the separate `substrate-podcert` chart. Its three CA-pool Secrets and live trust bundles must exist before PostgreSQL starts.
- The main chart no longer creates PostgreSQL. The lab now applies the release's kind-sized, digest-pinned manifest and creates the two connection Secrets before installing the chart.
- A cold gVisor ranged download can consume the first five-minute golden-snapshot attempt. The control plane retries after the asset is cached, so the lab now allows ten minutes for the golden tag and collects atelet/Worker logs when that path fails.
- Restarting the single AgentGateway router to change the test-only parking budget caused an avoidable Service outage. AgentGateway watches its projected config, so the suite now waits for the gateway's confirmed hot reload and keeps the router in service.
- Alpha2 names durable archives per volume. The `workspace` volume is stored as `durable-dir-workspace.tar.zstd`; the regression keeps an exact file-set assertion for both Full and Data snapshots.
- `snapshotConfig.onPause` no longer exists. `onCommit` now controls both Pause and Suspend, so the suite proves that Full pause preserves process memory while Data pause preserves the durable repair and restarts process memory from the golden state.

## Remaining coverage

The browser UI was not exercised in this run. The companion still has no outbound-request endpoint, so outbound authorization, TLS interception, and credential delivery remain outside the automated suite. Linux/WSL2, ARM64, and GitHub-hosted Linux validation are intentionally deferred until the final release candidate; this alpha2 result makes no cross-host claim.
