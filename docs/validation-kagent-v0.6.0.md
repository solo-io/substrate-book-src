# Validation: kagent-dev Substrate v0.6.0

Release validation completed on October 22, 2026. The audit and clean-cluster run used the published kagent-dev artifacts, not a local Substrate build.

## Runtime

- Release: [kagent-dev/substrate v0.6.0](https://github.com/kagent-dev/substrate/releases/tag/v0.6.0), commit `91ddf40e437a693434cf9b883531bbe96d67c61c`.
- Upstream baseline: the release imports `agent-substrate/substrate` commit `e88ffcdb54d65eac54c3a0e064364029bd84f0af`.
- CLI: all four Darwin/Linux and AMD64/ARM64 binaries were downloaded and hashed locally. Their SHA-256 values are recorded in `scripts/cli-checksums.json`; the host binary reported the release commit.
- Charts: the published `substrate-crds`, `substrate-podcert`, and `substrate` OCI charts were pulled and checked against both their archive checksums and OCI digests in `scripts/runtime-lock.json`. The exact packages rendered successfully through the image-locking post-renderer.
- Images: every platform image rendered by the charts, plus `ateworker-gvisor`, resolved to the immutable multi-platform index digest in `scripts/runtime-lock.json`. Each required image publishes `linux/amd64` and `linux/arm64/v8` variants.
- Companion images: the agent, web application, and source-control service were built locally. No Substrate source checkout, Go compiler, or platform image build was used.

## API and behavior changes from v0.6.0-alpha3

The companion now uses integer `cpuMillicores` and `memoryBytes` resource fields. It inherits the installation's cluster-wide snapshot location instead of setting the preview-gated per-template `storageLocation`, and its expected durable URIs begin with `gs://ate-snapshots/atespaces/book-workbench/`. Actor deletion no longer passes the removed `--any-state` flag. Setup also omits the removed `actorIdentityJWTProvider` authentication field.

The lifecycle regression reflects the released suspend model. Asynchronous suspend returns with a local checkpoint and a pending durable copy. Synchronous suspend waits for the durable upload but retains the completed local copy and node affinity for fast reuse. Resume can detach that checkpoint and use eligible capacity on another node. The released gateway also injects a scoped Secret or Substrate-minted Actor JWT and validates the UID-bearing Actor identity against the live Actor.

## Validation status

The release passed on October 22, 2026, on macOS x86_64 (Darwin 24.6.0), Docker Engine 29.8.0, kind 0.31.0, kubectl 1.36.1, and host Python 3.9.6. The final clean-cluster command was:

```shell
KIND_CLUSTER_NAME=book-v060-final-20261022-r4 ./scripts/test-lab.sh
```

All 59 local unit tests passed. The runner then created a fresh Kubernetes 1.35.0 kind cluster, installed only the locked v0.6.0 charts and platform images, and passed the complete Chapter 3–6 regression. The live checks covered Worker reuse and isolated Actor state, capacity exhaustion and parked requests, Worker-loss recovery, exact MEMORY and VOLUMES snapshot contents, asynchronous and synchronous suspend, atelet restart, durable upload, tag forks and deletion ownership, Actor UID continuity, scoped Secret injection, denial of an ungranted Secret, Actor JWT issuance and verification, and policy continuity across suspend and resume. Teardown left the Atespace empty, and the runner deleted the disposable cluster successfully.

The retained results are under `.test-results/book-v060-final-20261022-r4`. The browser workflow, HTTPS interception, Linux/WSL2 hosts, ARM64 hosts, and GitHub-hosted CI remain separate coverage surfaces.

## Upstream after the release

At validation time, `agent-substrate/substrate` `upstream/main` was `4d9898169`, five commits ahead of the release's imported upstream commit. Those commits harden malformed durable-snapshot URI validation, adjust development guidance and security documentation, and change an internal timing test. They introduce no additional public API or component rename that requires a book or companion migration. They remain directional until a later kagent release publishes a matching CLI, charts, CRDs, and images.
