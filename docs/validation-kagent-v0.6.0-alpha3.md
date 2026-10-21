# Validation: kagent-dev Substrate v0.6.0-alpha3

Release audit started on October 19, 2026. The artifact checks used the published kagent-dev release rather than a local Substrate build.

## Runtime

- Release: [kagent-dev/substrate v0.6.0-alpha3](https://github.com/kagent-dev/substrate/releases/tag/v0.6.0-alpha3), commit `e2fc0fa9f77ab5fc09eb01b43ccd74bd84c67c71`.
- CLI: all four Darwin/Linux and AMD64/ARM64 binaries were downloaded, hashed locally, and matched the recorded release checksums. The Darwin AMD64 binary reported the release commit.
- Charts: the published `substrate-crds`, `substrate-podcert`, and `substrate` OCI charts were pulled, hashed locally, linted, and rendered with the companion values.
- Images: every rendered Substrate image, including the new `ate-idp-server`, and the companion's gVisor Worker image resolved to a published AMD64/ARM64 index. Their immutable index digests are recorded in `scripts/runtime-lock.json`.

## Companion changes

The companion now removes `ActorTemplate.sandboxConfig`, keeps sandbox selection on the `WorkerPool` and placement on `workerSelector`, and pins the complete alpha3 artifact set. Its normal Suspend path uses `--mode sync` because the demo promises a durable recovery point. The Chapter 5 regression separately exercises `--mode async`, verifies the pending local snapshot and node pin across an atelet restart, resumes from that local snapshot, and then proves synchronous durability.

The clean-cluster run also confirmed two observable alpha3 details. The ActorTemplate's generated golden Tag appears in `kubectl ate get tags`, so the regression preserves it as setup state. A gVisor MEMORY snapshot contains the four expected `rootfs/` checkpoint objects in addition to the top-level process and `fs/` objects; the regression checks the exact set.

## Validation status

The release passed on October 19, 2026, on macOS x86_64 (Darwin 24.6.0), Docker Engine 29.8.0, kind 0.31.0, kubectl 1.36.1, and host Python 3.9.6. The final clean-cluster command was:

```shell
KIND_CLUSTER_NAME=book-v060-alpha3-validation-20261019-r3 ./scripts/test-lab.sh
```

All 40 local unit tests passed. The script then created a fresh cluster, installed only the locked published Substrate artifacts, built the two companion images, and passed the Chapter 3–6 regression. That run covered placement, capacity exhaustion and parked requests, Worker-loss recovery, exact snapshot contents, asynchronous and synchronous suspend, atelet restart with a local snapshot, VOLUMES restore semantics, tag forks and ownership, Actor UID continuity, EgressPolicy admission, credential-effect admission, and final Actor cleanup. The runner deleted its cluster successfully. Results were retained locally under `.test-results/book-v060-alpha3-validation-20261019-r3`.

The browser workflow, Linux/WSL2 hosts, ARM64 hosts, and GitHub-hosted CI remain separate coverage surfaces. Egress-policy and credential-effect admission do not establish enforcement or end-to-end credential delivery; the published agentgateway still lacks the `actorJwt` replacement-header source.
