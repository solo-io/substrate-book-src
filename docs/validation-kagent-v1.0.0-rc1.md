# Validation: kagent-dev Substrate v1.0.0-rc1

Release-candidate validation completed on October 30, 2026. The audit and clean-cluster run used the published kagent-dev artifacts, not a local Substrate build.

## Runtime

- Release: [kagent-dev/substrate v1.0.0-rc1](https://github.com/kagent-dev/substrate/releases/tag/v1.0.0-rc1), commit `71b1f9ec80848f8fd46f5d5d7e6b84ba36a80966`.
- Upstream baseline: the release contains `agent-substrate/substrate` `release-1.0` at `f86fde9dfb5424c8d16f613de80469bdbef06541`, the upstream `v1.0.0-rc.1` tag.
- CLI: the SHA-256 values of all four Darwin/Linux and AMD64/ARM64 binaries are recorded in `scripts/cli-checksums.json`; the host binary reported the release commit.
- Charts: the published `substrate-crds`, `substrate-podcert`, and `substrate` OCI charts at `1.0.0-rc1` were pulled and checked against both their archive checksums and OCI digests in `scripts/runtime-lock.json`. The exact packages rendered successfully through the image-locking post-renderer.
- Images: every platform image rendered by the charts, plus `ateworker-gvisor`, resolved to the immutable multi-platform index digest in `scripts/runtime-lock.json`. Each kagent-dev image publishes `linux/amd64` and `linux/arm64/v8` variants. The router and egress gateway now use the released AgentGateway `cr.agentgateway.dev/agentgateway:v1.7.0` instead of a kagent-built image.
- Companion images: the agent, web application, and source-control service were built locally. No Substrate source checkout, Go compiler, or platform image build was used.

## API and behavior changes from v0.6.0

- **API group.** `WorkerPool`, `SandboxConfig`, and `CSIDriverConfig` are served only as `ate.dev/v1`. The companion WorkerPool moved from `ate.dev/v1alpha1`; its fields are unchanged.
- **Actor ingress requires mTLS.** The router's plaintext listeners (ports 8080 and 8081) are gone. Clients connect over HTTPS on port 443 (or CONNECT on 8444), present a `podidentity.podcert.ate.dev` certificate, and must be listed in `atenetRouter.ingressAllowedSPIFFEIDs`, whose empty default denies all ingress. The web application now mounts its own Pod certificate and the service-DNS trust bundle and calls `https://atenet-router.ate-system.svc`. The lab allows only `spiffe://cluster.local/ns/book-workbench/sa/repo-workbench-webapp`.
- **Snapshot storage state.** `durableSnapshot.status` (`SNAPSHOT_STORAGE_STATUS_*`) is now `durableSnapshot.state` (`SNAPSHOT_STORAGE_STATE_*`). `scripts/lib.sh` and the chapter regression read the new field.
- **Worker capacity.** A Worker keeps 64 MiB of its Pod memory limit for itself and for at most two concurrent checkpoint writes, so the 1 GiB `generic-worker` Pod now reports `memoryBytes: 1006632960` (960 Mi) rather than 1 GiB. One 512 MiB Actor still fits; the Chapter 4 capacity exhaustion behaves as before.
- **Credential denial body.** When the provider refuses a credential, the Actor receives `403` with the body `actor egress policy denied`. The provider's reason (`atespace "book-workbench" is not permitted to resolve secrets in namespace ...`) now appears only in the egress gateway's access log, as the `error=` field with `reason=Authorization`.
- **CLI.** `kubectl ate proxy actor` is new. `kubectl ate admin make-ca-pool` now defaults to `ECDSAP256`; setup already passed that key type for the Actor identity and egress CA pools. The remaining commands and flags used by the lab are unchanged.

The Actor state enum, ActorTemplate fields, PostgreSQL manifests and bootstrap SQL, and the egress-policy shapes used by the lab are unchanged from v0.6.0.

## Validation status

The release candidate was validated on macOS 15.7.7 x86_64 (Darwin 24.6.0), Docker Engine 29.8.0, kind 0.31.0, kubectl 1.36.1, Helm 3.16.2, and host Python 3.9.6.

The final clean-cluster command was:

```shell
KIND_CLUSTER_NAME=book-v100-rc1-clean ./scripts/test-lab.sh
```

All 59 local unit tests passed. The runner then created a fresh Kubernetes 1.35.0 kind cluster, installed only the locked v1.0.0-rc1 charts and platform images, and passed the complete Chapter 3–6 regression: Worker reuse and isolated Actor state through the web application's mTLS router path, capacity exhaustion and parked requests, Worker-loss recovery, exact MEMORY and VOLUMES snapshot contents, asynchronous and synchronous suspend, atelet restart, durable upload, tag forks and deletion ownership, Actor UID continuity, scoped Secret injection, denial of an ungranted Secret, Actor JWT issuance and verification, and policy continuity across suspend and resume. Teardown left the Atespace empty, and the runner deleted the disposable cluster successfully. The retained results are under `.test-results/book-v100-rc1-clean`.

`kubectl ate proxy actor` does not work on the lab's Kubernetes 1.35.0 node. The CLI requests its router client certificate (as `ate-system/atenet-client`) with a `PodCertificateRequest` that sets `stubPKCS10Request`, and the 1.35 API rejects it: `spec.pkixPublicKey: Invalid value: null: must be a valid PKIX-serialized public key`. The web application is unaffected because the kubelet issues its projected Pod certificate. The lab therefore does not list `atenet-client` in the router allowlist, and Actor access still goes through the companion backend.

The browser workflow, HTTPS interception, Linux/WSL2 hosts, ARM64 hosts, and GitHub-hosted CI remain separate coverage surfaces.
