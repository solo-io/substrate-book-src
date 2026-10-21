# Testing the book lab

The full-lab target environments are macOS with Docker, Linux with Docker, and Windows through WSL2 with Docker Desktop integration. Use Bash for the scripts. The version under test is pinned in `scripts/lib.sh`.

`scripts/runtime-lock.json` records the chart package checksums, their OCI manifest digests for reference, and the platform image index digests. Setup verifies chart archives before installation and uses `scripts/pin-images.sh` as the Helm post-renderer to enforce the image lock. These index digests retain AMD64 and ARM64 selection. The deployed verifier checks the API server's exact locked image reference.

When upgrading the runtime, update the version, all four CLI checksums, all three chart package checksums and OCI digests, and the release image digests together. Resolve image tags to their multi-platform index digests, download the charts to compute the archive checksums, then render the charts with the post-renderer and run the disposable lab suite. Chart checksum failures and unknown rendered images must be resolved explicitly rather than bypassed.

The v0.6.0-alpha3 setup has a strict dependency order: CRDs, three CA pools, the separate certificate-controller release, its live trust bundles, PostgreSQL, the owner/read-write roles and connection Secrets, application authentication, and finally the main chart. A later failure can hide the first bootstrap problem, so collect diagnostics before rerunning over a partial installation. The lab targets fresh clusters; it does not migrate Actors, snapshots, or PostgreSQL data from an earlier alpha.

## One command for a disposable lab

With the README prerequisites installed:

```shell
./scripts/test-lab.sh
```

The runner creates a uniquely named kind cluster and refuses existing cluster names. It runs all unit tests, installs the published Substrate release, follows the Chapter 3–6 lab state from one chapter into the next, and tears down the resources and cluster. It restores the previous Kubernetes context and preserves logs in `.test-results/<cluster-name>/`. A failing assertion, setup error, or cleanup error makes the run fail.

To use an explicit new name:

```shell
KIND_CLUSTER_NAME=book-regression ./scripts/test-lab.sh
```

To keep it for a browser check:

```shell
export KIND_CLUSTER_NAME=book-browser-check
./scripts/test-lab.sh --keep-cluster
kubectl --context kind-book-browser-check port-forward \
  -n book-workbench service/repo-workbench-webapp 8080:80
```

After checking the browser, stop the port-forward and clean up in that same shell:

```shell
./scripts/teardown-example.sh
./scripts/delete-kind.sh
```

`--keep-cluster` also preserves a failed installation for diagnosis. It transfers cleanup responsibility to the caller.

## Individual checks

| Command | Coverage |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | Application state and routing; verified downloads; PostgreSQL bootstrap ordering/Secret keys/failure propagation; context isolation; image validation; failure cleanup and refusal of existing test clusters |
| `./scripts/verify-example.sh` | Standalone deployed-backend smoke test: Actor creation/listing, independent identity/state, stale-step conflicts, Worker reuse, MEMORY restoration, seven-step completion |
| `./scripts/verify-chapters.sh --allow-worker-replacement` | Stateful Chapter 3–6 lab: initial Worker/template checks; two-Actor CLI and backend walkthrough through the step 6/step 1 handoff; HTTP 503 capacity response and parked handoff; Worker loss and revert/resume; snapshot contents/fidelities, async/sync suspend, tag forks/deletion and Chapter 5 reset; UID preservation; credential brokering through the egress gateway (no-policy denial, hostname-only 401, injected Secret 201, ungranted-namespace 403, verified Actor JWT, and injection after resume) |

Both deployed verifiers require an empty `book-workbench` Atespace. The full runner invokes only the stateful Chapter 3–6 suite; `verify-example.sh` remains available as a shorter standalone smoke test. The chapter suite allows only the original `repo-workbench` template and its generated golden Tag; it rejects any additional templates or Tags. Its explicit flag acknowledges that it deletes the test Worker's Pod. Use it only on a disposable kind lab.

The parking-success check uses a test-only 30-second budget, which separates the handoff behavior from host restore speed. It first checks a timeout with the chart's default budget, then restores the router's original configuration after checking a successful original request. This does not change the reader setup's budget.

## Linux CI

`.github/workflows/lab.yaml` runs on pull requests, pushes to `main`, and manual dispatch. The job uses an Ubuntu 24.04 VM and read-only repository permissions. It installs checksum-verified kind 0.31.0, kubectl 1.35.0, and Helm 3.19.0, then runs `test-lab.sh`. Official checkout and artifact actions are pinned by commit.

Logs are retained for seven days, including setup, unit tests, deployed tests, cleanup, Pod listings, events, and bounded logs for the API/controller/router/egress deployments, atelet, the Worker, the separate certificate controller, PostgreSQL, and the web application. The collector excludes Secrets, kubeconfigs, environment dumps, and signing-key pools. Cleanup runs through the shell exit trap and a final workflow step.

The workflow must be committed to a GitHub repository to execute there. Local workflow validation does not establish a successful GitHub-hosted run.

## Windows/WSL2

Run the same commands inside the WSL2 Linux filesystem, with Docker Desktop running and integration enabled for that distribution. Do not run the shell suite directly in PowerShell.

If the three CLI tools are absent, install them in the companion directory:

```shell
./scripts/install-ci-tools.sh
export PATH="$PWD/.ci-tools/bin:$PATH"
./scripts/test-lab.sh
```

This installer does not install or reconfigure Docker, WSL, Python, jq, OpenSSL, or shasum.

## Host troubleshooting

If the certificate controller does not become ready, check `podcertificate-controller.log` and confirm that `service-dns-ca-pool`, `pod-identity-ca-pool`, and `postgres-ca-pool` exist before debugging PostgreSQL. If PostgreSQL remains Pending or its TLS projection fails, check `postgres.log` and the cluster events before increasing timeouts. An API server error opening `/run/postgres.podcert.ate.dev/*.pem` points back to the PostgreSQL client-certificate projection or the certificate-controller authorization, while role/schema errors point to `setup-postgres.sh` and the two connection Secret definitions.

A cold Worker downloads the pinned gVisor archive before it can prepare the ActorTemplate's golden snapshot. If that ranged download consumes the first five-minute preparation attempt, the control plane retries after the asset is cached; setup allows ten minutes for the golden tag so that bounded retry can complete. Check `atelet.log` for `Sandbox asset prewarm failed` and `gVisor release download complete` before treating this as an ActorTemplate error.

The companion sets `ndots:4` on the API server and web application Pods. This makes cluster hostnames such as `kubernetes.default.svc.cluster.local` resolve before inherited host search domains, which delayed API authentication on the Linux test host.

If kube-proxy or other Pods report `too many open files`, check the Docker host's `fs.inotify.max_user_instances` and `fs.inotify.max_user_watches` limits. Multiple kind clusters share these limits. The [kind troubleshooting guide](https://kind.sigs.k8s.io/docs/user/known-issues/#pod-errors-due-to-too-many-open-files) describes temporarily increasing them. Record their original values and restore them after deleting the test cluster. The lab scripts do not change these host limits.

A noninteractive Windows/SSH session can fail a public image pull with `A specified logon session does not exist`. If Docker Desktop's credential helper causes that error, use a temporary Docker CLI configuration for this public-image lab:

```shell
export DOCKER_CONFIG="$(mktemp -d)"
printf '{}\n' > "$DOCKER_CONFIG/config.json"
./scripts/test-lab.sh
rm -rf "$DOCKER_CONFIG"
unset DOCKER_CONFIG
```

This configuration has no private-registry credentials. It leaves the existing Docker configuration intact. See [Docker CLI configuration](https://docs.docker.com/reference/cli/docker/#configuration-files). Keep Docker Desktop running during the WSL2 tests.

## Browser check and remaining coverage

The API suite does not exercise DOM controls. For each supported host, open the deployed workbench and check New run, Next step, the repaired file and Git diff, suspend/resume, the agent report, passing tests, and Actor deletion. Record the actual browser and where the application was hosted.

The companion's publish action exercises cleartext HTTP egress, credential injection, and Actor JWT delivery against the in-cluster source-control service. HTTPS interception and the Actor-side interception trust bundle still need a separate egress fixture. Linux/WSL2, ARM64, and GitHub-hosted Linux runs are deferred until the final release candidate rather than repeated for every interim alpha.
