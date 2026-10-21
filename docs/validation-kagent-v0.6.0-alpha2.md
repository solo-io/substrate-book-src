# Validation: kagent-dev Substrate v0.6.0-alpha2

Validated on October 17, 2026, using macOS 15.7.7 on AMD64, Docker Desktop 29.8.0, kind 0.31.0, kubectl 1.36.1, Helm 3.16.2, and a fresh single-node Linux AMD64 Kubernetes 1.35.0 cluster named `book-v060-alpha2-validation-20261017-4`. The run used the published kagent-dev release rather than a local Substrate build. ARM64, Linux/WSL2 hosts, browsers, and GitHub-hosted CI were not exercised.

## Runtime

- Release: [kagent-dev/substrate v0.6.0-alpha2](https://github.com/kagent-dev/substrate/releases/tag/v0.6.0-alpha2), commit `4df1cd30f54e7efd277252e6372ccbd68fdad9c2`.
- CLI: all four released macOS/Linux binaries matched the SHA-256 values in `scripts/cli-checksums.json`. The binaries report the exact release commit in a pseudo-version with a `-dirty` suffix.
- Charts: the published `substrate-crds`, `substrate-podcert`, and `substrate` OCI charts matched both the archive checksums and registry digests in `scripts/runtime-lock.json`. All three charts passed `helm lint`, and the main and pod-certificate charts rendered with the companion values.
- Images: every release image required by the rendered installation publishes Linux AMD64 and ARM64 manifests. The companion now pins the renamed `ateworker-gvisor` image and the chart's updated agentgateway image by immutable multi-platform digest.

## Release changes exercised by this companion

This release completes the coordinated `ateom` to `ateworker` rename across the Worker images, internal Worker service, certificates, and metrics. `atelet` keeps its name and remains the node-level manager. The companion therefore changes its Worker image lock from `ateom-gvisor` to `ateworker-gvisor`; it does not introduce an `AteHerder` component.

The ActorTemplate resource limits remain explicit, and its sandbox class is now represented by the string `gvisor`. Authorization is enabled by default, with the chart's shared `ate-client` service account as the bootstrap owner. The web application already requests that identity, so the regression must prove that Actor, template, Tag, and policy operations still pass authorization without widening the bootstrap policy.

Snapshot state is now tracked persistently by atelet and reported to the control plane. Checkpoint and promotion responses carry state, but this release does not perform the later asynchronous durable upload work found on upstream `main`. The existing Chapter 5 tests should continue to treat Pause as a local snapshot and Suspend as the completed durable snapshot boundary.

## Artifact evidence

| Chart | OCI digest | Archive SHA-256 |
| --- | --- | --- |
| `substrate-crds` | `sha256:b9898f8590d14d8806e4251c5ebd87a1af4137507ae32bf313334f35b0545d75` | `ade85207c795775963ffe094a2151fc2f9df111304218d0911ce8a4c61643a1b` |
| `substrate-podcert` | `sha256:ee25b529622816fd53ab4e5b2e653849060a016b0035b3cdba75e607f2204a7c` | `47a8a0c2914c7008b4759266e69cde5f8ebc835c3e5d0c06707a0fee9c7f1097` |
| `substrate` | `sha256:49febbe3820ffce16156ebf78b6e01ff716e797c7206396eb403d2c29ed270d6` | `528d7608716c17d6ea22578bd1e7fc3dbd05f12d027031c8d9b9291d22294a53` |

The release workflow and its Helm end-to-end job passed. The exact image index digests used by the companion are recorded in `scripts/runtime-lock.json`; setup rejects a rendered image that is not in that lock.

## Companion regression

The repository unit suite and complete disposable-cluster run passed:

```shell
KIND_CLUSTER_NAME=book-v060-alpha2-validation-20261017-4 ./scripts/test-lab.sh
```

All 39 unit and backend HTTP tests passed. Setup created an empty cluster, verified the published CLI and chart archives, installed only digest-locked release images, and reached a ready ActorTemplate using the renamed `ateworker-gvisor` image and string-valued `gvisor` sandbox class. The chart's default authorization also worked through the shared `ate-client` bootstrap owner used by the CLI and web application.

The stateful Chapter 3 through Chapter 6 regression passed Worker readiness and reuse, independent Actor state, direct capacity exhaustion, request parking, Worker-loss recovery, MEMORY/VOLUMES snapshots, local Pause, durable Suspend, Tag forks and deletion, UID preservation, and HTTP policy/credential-effect admission. The MEMORY Pause check restarted the atelet Pod, confirmed that the same local snapshot generation and locality were reported afterward, and resumed the Actor with its process state intact. This exercises the release's persistent atelet snapshot tracker rather than inferring persistence from the API shape.

The first run also exposed a stale alpha4 assertion: pool saturation now returns HTTP 503 rather than 504. The v0.6.0-alpha2 router source and tests explicitly map `ResourceExhausted` to 503, so the companion now checks the released behavior instead of weakening the capacity assertion.

The runner preserved diagnostics under `.test-results/book-v060-alpha2-validation-20261017-4/`, removed its Actors and namespace, deleted the disposable cluster, and restored the prior `kind-book-workbench` context. Expected warnings included transient certificate mounts during startup, OpenTelemetry export failures because the lab has no collector, the deliberately killed Worker sandbox, and the deliberately restarted atelet Pod; none failed an assertion.

## Upstream-only watch items

The following behavior exists after the release commit on canonical `main` and is not part of this runtime baseline: required snapshot object-storage URIs at every checkpoint, `SuspendActor` durability modes, `kubectl-ate suspend` replacing the Pause implementation, removal of `ActorTemplate.sandboxConfig`, the `ate-idp-server`, and the completed asynchronous snapshot upload loop. These changes stay in the book update watchlist until a later kagent release publishes the matching CLI, charts, and images and the companion regression passes against them.

## Remaining coverage

The browser workflow, Linux/WSL2 hosts, ARM64 hosts, and GitHub-hosted Linux remain untested for this release.
