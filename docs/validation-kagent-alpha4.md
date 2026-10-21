# Validation: kagent-dev Substrate v0.5.0-alpha4

Validated on October 14, 2026, using macOS 15.7.7 on AMD64, Docker Desktop 29.8.0, kind 0.31.0, and a fresh single-node Linux AMD64 Kubernetes 1.35.0 cluster named `book-alpha4-validation-20261014-3`. ARM64, Linux/WSL2 hosts, browsers, and GitHub-hosted CI were not exercised.

## Runtime

- Release: [kagent-dev/substrate v0.5.0-alpha4](https://github.com/kagent-dev/substrate/releases/tag/v0.5.0-alpha4), commit `a7310e83a9d30f2cb96b23e96f3541c50c62e24e`.
- Charts: published `substrate-crds`, `substrate-podcert`, and `substrate` OCI charts, version `0.5.0-alpha4`, with archive checksums and OCI digests recorded in `scripts/runtime-lock.json`.
- CLI: all four released host/Linux binaries were downloaded, checksum-verified, and recorded in `scripts/cli-checksums.json`. The published binaries report a pseudo-version and the release commit, so `ensure_cli` accepts the exact commit embedded in the checksum-verified binary.
- Images: the seven released Substrate images used by the lab, including `objectstore-plugin` and `ateom-gvisor`, are pinned to their multi-platform index digests. The chart's pinned agentgateway image and the third-party lab images remain locked as well.
- Only the companion agent and web application images were built locally. No Substrate source checkout, Go compiler, or runtime image build was required.

## Checks

The complete disposable runner passed:

```shell
KIND_CLUSTER_NAME=book-alpha4-validation-20261014-3 ./scripts/test-lab.sh
```

All 39 unit and backend HTTP tests passed. Setup verified the downloaded CLI and charts, rendered every chart image through the digest lock, installed PostgreSQL and the certificate controller, and checked both object-store plugin sidecars. The control provider ran beside the API server, and the node provider ran beside atelet. The `generic-worker` WorkerPool resolved its `gvisor` sandbox class to `gvisor-default` before the `ActorTemplate` was created.

The expanded stateful verifier passed the Chapter 3 setup and walkthrough: locked runtime identity, one ready gVisor Worker, a ready MEMORY template, two initially suspended Actors, independent identity and repository state, stale-step rejection, Worker reuse, suspend/resume restoration, and the exact step 6/step 1 handoff into Chapter 4. It exercised the section 3.7 application actions through the backend while intentionally skipping DOM/browser interaction.

From that preserved state, Chapters 4–6 passed capacity exhaustion, default parking timeout, parked-request handoff, Worker loss and revert/resume, MEMORY/VOLUMES snapshot contents, Pause/Resume behavior, Tag forks and snapshot deletion, the Chapter 5 reset into Chapter 6, UID preservation, HTTP policy readback, and credential-effect admission.

Alpha4 no longer exposes `ACTOR_STATE_PAUSED` or `ACTOR_STATE_PAUSING`. The regression verified that Pause produces `ACTOR_STATE_SUSPENDED`, a latest snapshot with `locality` and no durable copy, an `assignedNode` matching that locality, and no `workerAssignment`. Suspend produced a completed durable snapshot with the Worker released. MEMORY Pause preserved the process state; VOLUMES Pause and Suspend preserved the repaired files and restarted process state.

Each runner collected bounded diagnostics, removed its Actors and namespace, deleted its disposable cluster, and restored the previous Kubernetes context. The successful result is under `.test-results/book-alpha4-validation-20261014-3/` in the local working tree.

## Corrections discovered during validation

Alpha4 adds a default-deny ingress policy to `ate-system`. An in-cluster API client must run in a namespace labeled `ate.dev/api-access=true`. Without that label, existing pooled connections can make the failure look intermittent while a new connection to another API server Pod times out. The companion now labels `book-workbench`, and a unit test keeps that requirement in the platform manifest.

Kubernetes Deployment readiness is not the same as Substrate Worker readiness after a Worker Pod is replaced. The recovery regression now waits until the replacement Worker is registered as `WORKER_STATE_ACTIVE` before resuming an Actor. This removes a race where the API correctly returned `ResourceExhausted` before the replacement Worker had published capacity.

## Manuscript alignment

- Chapter 5 must stop treating `PAUSED` as an Actor state. A paused Actor is now identified by the shape of its latest snapshot and its retained node assignment.
- Chapter 5 can mention alpha4's internal `UploadConfig`, `PromoteSnapshot`, snapshot reports, and public `ReportActorSnapshotState` RPC only with a caveat: background deferred upload, persistent snapshot tracking, response state population, and the reporting handler are not implemented in this release.
- Chapter 6 should add the API-access namespace label to the network boundary discussion. The label permits network traffic to the API server; authentication and OpenFGA authorization remain separate checks.
- Runtime-compatible scheduling, local-first restore fallback, the completed snapshot tracker, fail-closed authorization registry, policy-aware DNS enforcement, router-to-atunnel mTLS, and static-mTLS ingress installation remain upstream-only and must not be presented as alpha4 behavior.

## Remaining coverage

The browser workflow was intentionally skipped. The API and lifecycle paths behind it passed, but New run, Next step, suspend/resume, the file and diff panels, report display, and deletion still need a separate browser check. Linux/WSL2, ARM64, and GitHub-hosted Linux runs also remain pending.
