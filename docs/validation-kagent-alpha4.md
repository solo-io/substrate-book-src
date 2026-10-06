# Validation: kagent-dev Substrate v0.4.0-alpha1

Validated on October 6, 2026, using macOS AMD64, Docker Desktop, and a single-node Linux AMD64 kind cluster named `book-workbench-alpha4`. Existing `substrate` and `book-workbench` clusters were retained. ARM64 and WSL2 were not exercised end to end.

## Runtime

- Release: [kagent-dev/substrate v0.4.0-alpha1](https://github.com/kagent-dev/substrate/releases/tag/v0.4.0-alpha1), commit `c2e2e130606db615a553421b6352e47f49860870`.
- Charts: published `substrate-crds` and `substrate` OCI charts, version `0.4.0-alpha1`.
- CLI: released host and Linux binaries, verified against `scripts/cli-checksums.json`.
- Worker: published gVisor image pinned by digest in `scripts/lib.sh`.
- Kubernetes: kind node image `v1.35.0`, pinned by digest in `scripts/lib.sh`.
- Only the companion agent and web application images were built locally. No Substrate sources or binaries were built, and no images were published externally.

## Checks

The final setup completed on a freshly recreated test cluster. The deployed verifier passed on that fresh installation:

```shell
KIND_CLUSTER_NAME=book-workbench-alpha4 ./scripts/setup-kind.sh --recreate
KIND_CLUSTER_NAME=book-workbench-alpha4 ./scripts/verify-example.sh
python3 -m unittest discover -s tests -v
```

All 14 companion tests passed, including checksum failure/cache handling, context isolation, image-reference validation, and cleanup ordering. The corrected teardown completed without manual intervention; the demo was then redeployed with an empty Atespace.

The current book walkthrough was also executed against this release:

| Chapter | Verified behavior |
| --- | --- |
| 3 | WorkerPool and template inspection; two independent Actors; unchanged Worker Pod across suspend/resume; restored workflow position; browser file, diff, report, and passing-test views |
| 4 | Direct resume fails when capacity is occupied; routed request times out with 504; parked request succeeds after capacity is released; Worker Pod loss marks an Actor crashed; revert/resume on a replacement Pod preserves UID and restores the saved step, losing unsaved progress |
| 5 | Full snapshot files; Data restore preserves the repair and resets process progress; Full pause/resume; paused-to-suspended Data conversion; tagged snapshot starts another Actor at step 6; tag and Actor deletion |
| 6 | Stable Actor UID across suspend/resume; placement inspection; HTTP policy creation/readback; admission and readback of the illustrative credential-header replacement manifest |

The companion has no outbound-request endpoint. Outbound enforcement, TLS interception, and credential delivery were not exercised by the demo. Their chapter descriptions were checked against the tagged API schema, published chart configuration, and its pinned AgentGateway source revision `50999825cb55904801f7fd6b18b865179d0d50c4`.

## Automated regression run

A fresh disposable cluster, `book-ci-local-20261006`, passed `scripts/test-lab.sh` on the same macOS AMD64/Docker Desktop host. All 22 unit tests passed. The deployed demo and Chapter 4–6 regression suites passed, including capacity exhaustion and parking, Full/Data restoration, pause/resume, tag forks and snapshot deletion, Worker loss and recovery, UID preservation, and HTTP policy admission/readback. Credential-header replacement admission/readback also passed in a separate focused check after it was added to the suite.

The runner collected diagnostics, completed teardown, deleted its cluster, and restored the original `kind-substrate` context. The preexisting clusters remained available.

The Linux GitHub Actions workflow passes `actionlint` validation. It has not run on a GitHub-hosted runner; the companion checkout has no configured Git remote. Linux-host and Windows/WSL2 validation remain pending. The Linux tool installer has not yet been exercised on either target host. See [Testing the book lab](testing.md) for the maintained commands and coverage limits.

## Corrections discovered during validation

- Bootstrap must give the precreated Pod certificate controller namespace the Helm ownership metadata expected by the platform chart.
- Worker placement no longer needs a source-version node label.
- The released CLI prints `WORKER IPS` and takes an egress-policy manifest instead of `--hostnames`.
- The released egress API uses protocol-specific hostname/port rules and credential `replaceHeaders` effects. It chooses the most specific rule rather than the first rule.
- The chart configures HTTPS interception; the repository task does not install its CA or make outbound calls.
- Kubernetes 1.35.0 left a PodCertificateRequest with an invalid `core/v1` Pod owner reference and namespace cleanup stalled. This matches [Kubernetes issue 136890](https://github.com/kubernetes/kubernetes/issues/136890). Teardown now deletes the demo workloads, waits for their Pods, and removes their certificate requests through the normal API before deleting the namespace.

## Follow-up

Publish the two companion demo images for Linux AMD64 and ARM64 to the author's chosen registry, pin their digests, and switch the default setup to pull them. Image publication is intentionally deferred; `BOOK_AGENT_IMAGE` and `BOOK_WEBAPP_IMAGE` already permit testing that path together.
