# Upgrade plan: kagent Substrate `v0.5.0-alpha2`

This repository currently installs the kagent-dev Substrate distribution at `v0.4.0-alpha1`. The target is [`v0.5.0-alpha2`](https://github.com/kagent-dev/substrate/releases/tag/v0.5.0-alpha2), published October 8, 2026 from commit `9c4b1fb52212028100fa02c8a7bc4526575ea37e`. This is still an alpha runtime, so the upgrade is successful only when the published artifacts, the bootstrap sequence, and the complete Actor lifecycle work together in a disposable kind cluster.

The important point is that this upgrade changes the bootstrap architecture. Between these releases, the main Helm chart stopped deploying PostgreSQL and the pod-certificate controller, `snapshotConfig.onPause` was removed, the router and egress deployments dropped their idle `atenet` sidecars, and PostgreSQL connections moved to two pre-created Secrets with separate roles and client certificates. The implementation needs to absorb those changes without bringing back a Substrate source checkout or building platform components locally.

## Outcome

When this work is complete, a reader can clone this repository and run `./scripts/setup-kind.sh` against a fresh supported kind cluster. The script will download and verify the released CLI binaries and three published Helm charts, install the certificate controller, prepare the development PostgreSQL database, install Substrate, build only the two companion demo images, and create the Repo Workbench golden snapshot. `./scripts/test-lab.sh` will then exercise that installation from an empty cluster through cleanup.

This plan targets fresh, disposable book clusters. It does not promise an in-place migration of Actors, snapshots, or the alpha1 PostgreSQL volume. `setup-kind.sh` already refuses a cluster running a different Substrate image, and users upgrading an existing book lab must use a new `KIND_CLUSTER_NAME` or explicitly run `./scripts/setup-kind.sh --recreate`. The `--recreate` path deletes the named cluster and all Actor state in it.

## Published inputs

Treat these values as the release baseline, but resolve and verify them again while implementing the lock-file change. The registry is the source of truth if a value in this document and a published artifact ever disagree.

| Artifact | Target | Verified value |
| --- | --- | --- |
| Release | `kagent-dev/substrate` | `v0.5.0-alpha2`, commit `9c4b1fb52212028100fa02c8a7bc4526575ea37e` |
| `substrate` chart | `0.5.0-alpha2` | archive SHA-256 `b303bffd003655f3e5623773aecb04e1efe4f65e2192545ebd540043ab076b0c`; OCI digest `sha256:361504a126e02473b78a0e9e10b1c613b62a2ec7429419cf6a83e808b9808a34` |
| `substrate-crds` chart | `0.5.0-alpha2` | archive SHA-256 `e54d159e6b253ec2d12f0946f543896262167dee865534e0ca7a259763e141fe`; OCI digest `sha256:328bd7a125d4e3f223309851cee74c8cb7a53c5613523f7d2bb7cd5b56aebc49` |
| `substrate-podcert` chart | `0.5.0-alpha2` | archive SHA-256 `89180dc1a1073d86f9de105756dd4e69217cd188df661d8c6bdaf9c1c985c436`; OCI digest `sha256:440e0052e5a8b8f96e4dd7baef90aaabafa7ab1e581364bb8035cc6a1cf17b13` |
| `kubectl-ate-darwin-amd64` | release asset | SHA-256 `1dd5fbcda94a67916938d5e8eb782d1c2131f435f4541dff4b84e7bcdfef7ea4` |
| `kubectl-ate-darwin-arm64` | release asset | SHA-256 `b560783f15e11dff1c70bc28097f818e65f21fb230cd91b7b83c24a3bd7376c5` |
| `kubectl-ate-linux-amd64` | release asset | SHA-256 `149d1dadc31dfe28a2d042bb66abd4f8e3faf6e784e4f15d7019f5a9b01548de` |
| `kubectl-ate-linux-arm64` | release asset | SHA-256 `ad22475c766af0b19db2d73bfab308495d8fb910bfa5daa5a91365fc53716616` |

The six released Substrate images used by this lab (`ateapi`, `atelet`, `atecontroller`, `ateom-gvisor`, `kubernetes-secrets`, and `podcertcontroller`) publish both `linux/amd64` and `linux/arm64` manifests at `v0.5.0-alpha2`. The main chart also changes agentgateway to `cr.agentgateway.dev/agentgateway:v0.0.0-alpha.168b05d6@sha256:5e8384d1fcfdc443a3ad5155ea6a64dc7953bde162df7f5f8e131a5c19ae08a3`, which publishes both architectures.

## Upgrade risks we need to handle

### The certificate controller is a separate Helm release

Alpha1 installed the pod-certificate controller from the main `substrate` chart. Alpha2 publishes it as `substrate-podcert`, installed once per cluster in `podcertificate-controller-system`. Its three CA-pool Secrets (`service-dns-ca-pool`, `pod-identity-ca-pool`, and the new `postgres-ca-pool`) must exist before the chart starts.

On a fresh kind cluster, remove the old namespace ownership annotations that claim `podcertificate-controller-system` for the `substrate` release, create the namespace and CA pools, and install a separate Helm release named `substrate-podcert`. An in-place alpha1 Helm upgrade would also require transferring resource ownership without deleting the CA pools, but that migration is outside this disposable-lab upgrade and must not be implied by the scripts or documentation.

### PostgreSQL is no longer in the application chart

Alpha2 expects a prepared database and two Secrets named `substrate-postgres-readwrite` and `substrate-postgres-owner`. The read/write and owner connections assume different roles (`substrate_readwrite` and `substrate_owner`) in the `substrate` schema. For the published development setup, the API server also needs `postgres.clientCertificates.enabled=true` so the connection strings can use the projected `substrate_readwrite_user.pem` and `substrate_owner_user.pem` credentials.

Keep the book lab self-contained by checking in the minimal kind PostgreSQL manifest and bootstrap SQL needed by this exact release, with their upstream provenance and Apache license headers. Add a repository script that applies the kind-sized database, waits for `statefulset/postgres`, runs the idempotent role/schema setup, and creates the two connection Secrets without printing their contents. Do not make readers clone the Substrate source tree or compile `ate-setup`.

The PostgreSQL image remains digest-pinned. The setup order is now strict: CRDs, CA pools, `substrate-podcert`, PostgreSQL, PostgreSQL roles and Secrets, application authentication resources, and finally the main `substrate` chart.

### Pause now uses `onCommit`

Alpha2 removes `snapshotConfig.onPause`; `snapshotConfig.onCommit` controls the scope used by both Pause and Suspend. Remove `onPause` from `manifests/actor-template.yaml.tmpl`, then rewrite the chapter regression that currently creates a Data-scope template but expects Pause to capture Full state. The revised test should prove both sides of the new contract: the normal Full-scope template preserves in-memory progress across Pause, while the Data-scope template preserves durable files but restarts process memory from the golden state.

Do not simply weaken the snapshot assertions until the suite passes. If the set of files in a released snapshot changes, inspect the snapshot manifest and classify the change as an expected format update or a regression before changing the expected set.

### Networking is now agentgateway-only

The alpha2 router and egress deployments no longer run idle `atenet` sidecars. The main chart also changes the agentgateway image registry and revises the generated HTTP/gRPC routes. Keep the deployment checks focused on the `agentgateway` container, remove the unused `atenet:v0.4.0-alpha1` lock entry if it is no longer rendered anywhere, and add the new digest-pinned `cr.agentgateway.dev` reference.

Request parking, gRPC control traffic, ordinary HTTP Actor traffic, and egress-policy admission are all affected by this path. The existing lifecycle and chapter suites already cover most of it, but failures around 404/503/504 responses, content-type matching, or parked requests need to be treated as route compatibility problems rather than worked around with longer timeouts.

### The chart now supplies resource requests and limits

Alpha2 adds resources for each chart container. Render and review those defaults against the single-node kind environment, especially PostgreSQL, RustFS, atelet, agentgateway, and the certificate controller. Use the upstream kind-sized PostgreSQL settings and override other defaults only if the current CI-sized node cannot schedule the lab. Any override belongs in `manifests/substrate-values.yaml` (or the podcert values passed by setup), with a comment explaining why the book lab differs from the published default.

## Implementation plan

### 1. Update the release lock as one atomic change

- Change `SUBSTRATE_VERSION` in `scripts/lib.sh` to `v0.5.0-alpha2`.
- Replace all four values in `scripts/cli-checksums.json` with the release asset digests above.
- Add `substrate-podcert` to `scripts/runtime-lock.json` and `scripts/download-charts.sh`, alongside `substrate-crds` and `substrate`.
- Resolve every rendered alpha2 image to its multi-platform index digest and update `scripts/runtime-lock.json`; retain third-party images used by checked-in manifests, including PostgreSQL, even when they are no longer rendered by the main chart.
- Remove obsolete image entries only after rendering all three charts and scanning the companion manifests. `scripts/pin-images.sh` must continue to reject any tagged or digest-pinned image absent from the lock.
- Extend `tests/test_runtime_lock.py` so the third chart is downloaded and verified and so fixtures derive the active Substrate version instead of embedding alpha1 strings.

### 2. Split certificate-controller installation from the main chart

- Create `ate-system` and `podcertificate-controller-system` without assigning the latter to the old `substrate` Helm release.
- Install `substrate-crds` first.
- Create `service-dns-ca-pool`, `pod-identity-ca-pool`, and `postgres-ca-pool` in `podcertificate-controller-system` with the released `kubectl-ate admin make-ca-pool` command.
- Install `substrate-podcert` as its own release in `podcertificate-controller-system`, through the same checksum-verified local chart archive and image-lock post-renderer used for the main chart.
- Wait for the controller deployment and its trust bundles before starting PostgreSQL. Add a targeted readiness assertion so a missing CA pool fails near the cause instead of surfacing later as a database or API timeout.

### 3. Add the kind PostgreSQL bootstrap

- Check in the alpha2-compatible PostgreSQL manifest and setup SQL rather than downloading moving files during setup.
- Keep the current `postgres:18-alpine` digest pin and use the alpha2 kind footprint: `250m` CPU request, `2` CPU limit, and a `1Gi` volume.
- Add a script that applies the manifest through the selected `KUBECTL_CONTEXT`, waits for PostgreSQL, creates the `substrate` schema and the owner/read-write users and roles, and creates the two connection Secrets expected by the chart.
- Make reruns safe. Applying the manifest, rerunning the SQL, and recreating the Secrets with `--dry-run=client -o yaml | kubectl apply -f -` should converge without disclosing credentials.
- Add unit tests with mocked `kubectl` calls for context isolation, ordering, Secret names/keys, and failure propagation. Do not include Secret values in diagnostics or test logs.

### 4. Install the alpha2 application chart

- Set `postgres.clientCertificates.enabled=true` in `manifests/substrate-values.yaml` and keep the default Secret names, role names, and schema unless the checked-in bootstrap deliberately changes them.
- Preserve the kind registry replacement used by atelet.
- Install the main `substrate` chart only after the database Secrets exist, using the image-lock post-renderer and the existing `--wait --timeout 10m` boundary.
- Verify that `ate-api-server` reads both connection strings from Secrets, mounts the PostgreSQL client certificates, reaches Ready, and reports the exact locked alpha2 image.
- Update `scripts/collect-diagnostics.sh` to include the pod-certificate controller and PostgreSQL logs while preserving the rule that Secrets, environment dumps, kubeconfigs, and signing pools are never collected.

### 5. Reconcile the Actor and test APIs

- Remove `snapshotConfig.onPause` from the Repo Workbench template.
- Update tests and fixtures that embed `v0.4.0-alpha1` so they derive or expect `v0.5.0-alpha2` consistently.
- Rewrite the Full/Data pause assertions around the alpha2 `onCommit` behavior, while preserving coverage for durable-directory restore, tag forks, snapshot deletion, crash/revert, and Actor UID stability.
- Render the CRDs and compare the fields used by `WorkerPool`, `ActorTemplate`, SandboxConfig, egress policies, and credential effects. Add `serviceAccountName` only if the worker needs it; do not adopt new optional API surface just because it exists.
- Confirm that the web application can still create, list, suspend, and delete Actors with its projected Kubernetes token. Alpha2 still authenticates callers without control-plane RBAC, so any authorization failure is likely an authentication/configuration regression.

### 6. Update reader-facing documentation

- Change README release links, version references, and setup steps to `v0.5.0-alpha2`.
- Explain that setup installs three verified charts and a checked-in, digest-pinned development PostgreSQL manifest.
- Document the clean-cluster requirement and the destructive effect of `--recreate` prominently near the upgrade instructions.
- Update `docs/testing.md` with the separate certificate-controller/PostgreSQL stages and their diagnostic locations.
- Add a dated validation record after the full lab passes, listing host OS/architecture, Docker, kind/Kubernetes, Helm, release commit, chart digests, and anything not tested.

## How we will test it in kind

### Phase 1: checks that do not need a cluster

Run the Python suite first:

```shell
python3 -m unittest discover -s tests -v
```

Then verify the released downloads and rendered image set without installing them:

```shell
./scripts/download-cli.sh --linux-all
./scripts/download-charts.sh
helm template substrate-podcert \
  .cache/charts/0.5.0-alpha2/substrate-podcert-0.5.0-alpha2.tgz \
  --namespace podcertificate-controller-system \
  --post-renderer ./scripts/pin-images.sh >/tmp/substrate-podcert-rendered.yaml
helm template substrate \
  .cache/charts/0.5.0-alpha2/substrate-0.5.0-alpha2.tgz \
  --namespace ate-system \
  -f manifests/substrate-values.yaml \
  --post-renderer ./scripts/pin-images.sh >/tmp/substrate-rendered.yaml
```

Inspect the rendered output for mutable tags, unknown images, accidental Secret data, old alpha1 references, an `atenet` sidecar, or an in-chart PostgreSQL workload. The render should contain the separately installed certificate controller, the main control-plane workloads, and only image references accepted by `runtime-lock.json`.

### Phase 2: retain one fresh cluster for diagnosis and browser testing

Use a unique name so the existing development clusters are not touched:

```shell
export KIND_CLUSTER_NAME=book-alpha2-validation
./scripts/test-lab.sh --keep-cluster
```

The runner must create the cluster, execute unit tests, install the published runtime, build the companion images, verify the demo lifecycle, run chapter regressions, and leave the cluster available. If setup fails, do not rerun over a partially installed cluster until diagnostics have been collected; the strict bootstrap order means a later component can obscure the original certificate or database failure.

Check the platform shape after the automated suite:

```shell
kubectl --context kind-book-alpha2-validation get pods -A -o wide
helm --kube-context kind-book-alpha2-validation list -A
kubectl --context kind-book-alpha2-validation get clustertrustbundles
kubectl --context kind-book-alpha2-validation -n ate-system get statefulset/postgres deployment/ate-api-server deployment/atenet-router deployment/atenet-egress
kubectl --context kind-book-alpha2-validation -n podcertificate-controller-system get deployment/substrate-podcert-podcertificate-controller
.bin/kubectl-ate --context kind-book-alpha2-validation --version
```

The expected shape is three Helm releases (`substrate-crds`, `substrate-podcert`, and `substrate`), one ready PostgreSQL StatefulSet, one ready certificate-controller Deployment, no `atenet` container in the router or egress Pods, an alpha2 CLI, and an empty `book-workbench` Atespace after the verification suites clean up their Actors.

### Phase 3: verify the lifecycle behaviors that matter to the book

The existing automated coverage remains the lifecycle acceptance test:

- Creating and listing Actors must leave them suspended and must not wake them through background polling.
- Two Actors must keep independent identity and durable state while reusing one Worker.
- Full-scope Pause/Suspend must preserve process memory, transcript, files, and step position.
- Data-scope Pause/Suspend must preserve durable files while restarting process memory according to the new alpha2 contract.
- Capacity exhaustion must fail direct resume and time out the default parked request; the test-only extended parking budget must let the original request complete after capacity is released.
- Tag forks, Actor deletion, snapshot cleanup, Worker loss, crash/revert/resume, UID preservation, egress-policy defaults, and credential-effect admission must continue to pass.
- The deployed API server image must exactly match the locked alpha2 digest, and the post-renderer must reject an unrecognized image before Helm changes the cluster.

### Phase 4: browser check

Keep the validation cluster, forward the application, and exercise the UI:

```shell
kubectl --context kind-book-alpha2-validation port-forward \
  -n book-workbench service/repo-workbench-webapp 8080:80
```

In the browser, create two runs, confirm that opening one explicitly resumes it, advance through the failing test and repair, inspect the Git diff and `AGENT_REPORT.md`, suspend and reopen the run, complete the workflow, and delete both runs. Confirm from the Actor list that one run never inherits another run's state and that deleting a run removes it without waking the other one.

### Phase 5: clean run and CI

After the retained cluster is understood, delete it with the repository scripts and run the disposable path again under a different name:

```shell
export KIND_CLUSTER_NAME=book-alpha2-clean
./scripts/test-lab.sh
```

The second run proves that success did not depend on residue from manual investigation. Push the implementation and require `.github/workflows/lab.yaml` to pass on Ubuntu 24.04 before merging. Also run one local ARM64 lab when available; registry inspection proves that the released images exist for ARM64, but it does not prove gVisor, Docker Desktop, the companion builds, and the complete suspend/resume path work together on that host.

## Things to watch while debugging

- A Helm ownership error in `podcertificate-controller-system` usually means the setup reused an alpha1 cluster. Use a new kind cluster for this work; do not delete signing pools from a cluster whose state matters.
- A missing `postgres-ca-pool` prevents the separate controller from serving PostgreSQL certificates. Check the three CA-pool Secret names before looking at the database.
- An API server error opening `/run/postgres.podcert.ate.dev/*.pem` means `postgres.clientCertificates.enabled` or the pod-certificate authorization settings are wrong.
- A PostgreSQL `permission denied`, `must be member of role`, or missing-schema error means the bootstrap SQL, Secret DSN username, chart role, and schema are out of agreement. Those four values must move together.
- `ImagePullBackOff` for agentgateway may point at the registry change from GHCR to `cr.agentgateway.dev`, not at the Substrate images.
- Pending Pods after the chart upgrade may be caused by the new default resource requests. Inspect scheduling events before increasing timeouts.
- A failed parked request may be an agentgateway route/version issue. Capture the generated ConfigMap and router logs before patching the config by hand.
- A snapshot assertion failure may be the intentional `onPause` removal or a changed released snapshot format. Do not collapse Full and Data expectations into a generic “some files exist” check.
- A stale `.bin/kubectl-ate` must fail the version check. Do not relax `ensure_cli`; rerun `./scripts/download-cli.sh` and verify the release checksum.
- The test harness intentionally refuses an existing `KIND_CLUSTER_NAME`, restores the previous Kubernetes context, and excludes credentials from diagnostics. Preserve those safety properties while adding bootstrap stages.

## Completion criteria

The upgrade is ready to merge when all repository references use `v0.5.0-alpha2`, every downloaded artifact and rendered image is checksum/digest locked, no Substrate source checkout or platform image build is required, the fresh and retained kind runs pass, the browser workflow passes, Linux CI passes, and the validation document records the environments that were and were not exercised. Any remaining failure in Actor lifecycle, snapshot semantics, routing, certificate issuance, or database identity blocks the upgrade; an alpha release is exactly where papering over one of those failures becomes expensive later.
