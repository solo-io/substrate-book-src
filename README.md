# Agent Substrate Book Examples

This repository contains runnable examples for the Agent Substrate book. The first example, **Repo Workbench**, shows how a repository task can run as a stateful Substrate Actor.

Each Actor represents one independent repository run. It owns:

- A workflow position and transcript in process memory.
- A private repository in a Substrate durable directory.
- A terminal result when all task steps are complete.

The browser application creates and lists Actors through the Substrate control plane. It contacts an individual Actor through AgentGateway only when the user explicitly opens or advances that run. There is no background polling that silently resumes suspended Actors.

The current agent is deterministic rather than model-backed. Readers can therefore concentrate on Actor creation, state transitions, suspend/resume, durable files, and reusable Worker capacity without needing an LLM account or API key.

## The resource model

| Resource | Name | Purpose |
| --- | --- | --- |
| Kubernetes namespace and Atespace | `book-workbench` | Contains the example's resources and Actors |
| WorkerPool | `generic-worker` | Supplies reusable gVisor Worker capacity |
| ActorTemplate | `repo-workbench` | Defines the repository-agent runtime and initial workspace |
| Actor instances | `repo-workbench-1`, `repo-workbench-2`, ... | Independent stateful repository task runs |
| Web application | `repo-workbench-webapp` | Creates, lists, opens, advances, suspends, and deletes runs |

An ActorTemplate is a reusable blueprint. An Actor is one stateful execution created from that blueprint. Several Actors can exist at different workflow steps even when only one Worker is prepared to run them at a time. This example gives the Worker one CPU and each Actor a one-CPU limit, so CPU capacity permits only one running workbench Actor even though the Worker's Actor count limit is higher.

## What the example demonstrates

- A web application using the Substrate control plane to list and manage Actors.
- Explicit Actor selection so merely listing runs does not wake them.
- A seven-step repository workflow advanced one transition at a time.
- Optimistic step checks that prevent a repeated request from executing a step twice.
- Fast-forward implemented by executing the same transitions in sequence.
- Full suspend snapshots preserving the workflow position and transcript.
- A durable repository retaining modified and newly created files.
- Several independent Actors sharing one ActorTemplate and WorkerPool.
- Terminal task completion followed by inspection or Actor deletion, rather than resetting history.

## Repository layout

```text
Dockerfile                     Agent and web-application image targets
workbench/                     Stateful repository-run API
webapp/                        Multi-Actor browser UI and control-plane backend
sample-project/                Small repository with intentionally failing tests
manifests/                     WorkerPool, RBAC, web app, and ActorTemplate templates
scripts/                       Setup, deployment, port-forwarding, and cleanup
tests/                         Agent and web-application tests
docs/images/                   Guide diagrams and screenshots
```

## Set up the published Substrate runtime

This example uses the [kagent-dev distribution of Agent Substrate v0.5.0-alpha2](https://github.com/kagent-dev/substrate/releases/tag/v0.5.0-alpha2). This is still an alpha runtime, so the setup pins and verifies the exact release artifacts rather than following mutable tags.

Setup downloads its released `kubectl-ate` binaries and the published `substrate-crds`, `substrate-podcert`, and `substrate` Helm chart packages, verifies their SHA-256 checksums, and installs the verified packages. The lab also applies a checked-in, kind-sized PostgreSQL manifest from the release and prepares the separate owner/read-write database identities the alpha2 API server expects. All platform container images, including PostgreSQL and the matching gVisor Worker, are pinned by digest. No Substrate source checkout, Go compiler, or runtime image build is required.

Run the commands below from this companion repository. The version is declared in `scripts/lib.sh`; the binary checksums are in `scripts/cli-checksums.json`, and the chart checksums and image digests are in `scripts/runtime-lock.json`. Setup creates the three certificate-authority pools, installs the certificate controller as its own Helm release, prepares PostgreSQL, configures API authentication, and waits for the platform before deploying the example.

The alpha2 bootstrap is for a fresh, disposable book cluster. It does not migrate Actors, snapshots, or the PostgreSQL volume from alpha1. Use a new `KIND_CLUSTER_NAME`, or use `./scripts/setup-kind.sh --recreate` only when you intend to delete the named cluster and all Actor state in it.

The two **companion demo images** are still built locally with Docker: the repository worker and its web application. Publishing these for AMD64 and ARM64 is a follow-up task; Substrate, its Worker, and both CLI binaries already come from the public kagent-dev release. The local demo images are pushed to `localhost:5001`, and their deployment references use digests. No cloud account or private registry credentials are required.

### Prerequisites

- Docker with the daemon running
- Git to obtain this companion repository
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/), [Helm](https://helm.sh/docs/intro/install/), and `kubectl`
- `curl`, `jq`, `openssl`, and `shasum`
- Internet access for release binaries, charts, container images, and gVisor assets

Setup creates a single-node Kubernetes 1.35.0 kind cluster with the certificate feature gates and API Substrate requires. The cluster uses gVisor and does not require `/dev/kvm`. On Windows, run the lab in WSL2 with Docker Desktop integration enabled. Python 3 and Git are also needed for local mode and the verification commands, but the deployed application includes its own Python runtime.

To download the CLI binaries without creating a cluster:

```shell
./scripts/download-cli.sh
```

The host binary is installed at `.bin/kubectl-ate`. A matching Linux binary is downloaded for the demo web application. `--linux-all` downloads both Linux architectures when preparing multi-platform demo builds. Cached binaries are checked before reuse. The old `download-substrate.sh` entry point now forwards to this command and does not download source.

To download and verify the chart packages without creating a cluster, run `./scripts/download-charts.sh`. Setup performs this step automatically and checks cached packages before reuse. A changed chart package is rejected before cluster creation or installation. The Helm post-renderer replaces the chart's image tags with locked digests and rejects images absent from the lock file.

## Walk through Repo Workbench

Run all commands below from this repository.

### 1. Create the local platform

```shell
./scripts/setup-kind.sh
```

Keep Docker running and wait for **Setup complete**. The first run downloads the runtime images and builds the two companion images; later builds reuse Docker’s cache.

The setup script:

1. Downloads and checks the host and Linux `kubectl-ate` binaries for `v0.5.0-alpha2`.
2. Creates or reuses the `kind-book-workbench` context with the required certificate APIs.
3. Installs the CRDs, creates the three CA pools, and installs the separate `substrate-podcert` release.
4. Applies the digest-pinned development PostgreSQL manifest and creates its roles and connection Secrets.
5. Configures application authentication and installs the main Substrate chart.
6. Builds only the agent and web application, then deploys them with one published gVisor Worker.
7. Creates the `book-workbench` Atespace and `repo-workbench` ActorTemplate, then waits for its golden snapshot.

It deliberately creates no repository-run Actor. The first Actor is created from the browser.

The default cluster name is `book-workbench`, separate from a development cluster named `substrate`. To use another name, export `KIND_CLUSTER_NAME` before running setup and keep that value for the other scripts. The examples below assume `kind-book-workbench` is the current context; select it with `kubectl config use-context kind-book-workbench` if necessary.

Use a clean cluster when rerunning setup after the ActorTemplate has been created. **`--recreate` deletes the named cluster, including its Actors and stored state:**

```shell
./scripts/setup-kind.sh --recreate
```

Add the downloaded CLI to the current shell and select the lab context:

```shell
export PATH="$PWD/.bin:$PATH"
kubectl config use-context kind-book-workbench
kubectl ate --version
```

### 2. Inspect the prepared infrastructure

```shell
kubectl get workerpools -n book-workbench
kubectl get pods -n book-workbench
kubectl ate get actor-template repo-workbench -a book-workbench
kubectl ate get actors -a book-workbench
```

The WorkerPool and web application should be ready, the ActorTemplate should have a golden snapshot, and the Actor list should initially be empty.

The prepared Worker consumes Kubernetes capacity but has no assigned user Actor. Suspended Actors do not consume that Worker capacity.

### 3. Open the workbench

Start a foreground port-forward in a separate terminal:

```shell
./scripts/open-workbench.sh
```

Open [http://localhost:8080](http://localhost:8080).

The page initially requests only the Actor list from the control plane. It does not call an Actor application endpoint and therefore does not resume a suspended Actor.

The web application's management path is:

```text
Browser
  -> Repo Workbench web backend
  -> kubectl-ate using the web application's ServiceAccount
  -> Substrate control API
  -> list/create/suspend/delete Actors
```

The web application's task path is separate:

```text
Browser
  -> Repo Workbench web backend
  -> AgentGateway with ate-target-actor: book-workbench/<actor-name>
  -> selected repository Actor
  -> inspect state or advance the workflow
```

The web application's Kubernetes permissions are intentionally narrow: it can read the serving trust bundle and request an `ate-client` token. The browser never receives those credentials.

### 4. Create the first repository run

Click **New run**. The backend finds the next available ordinal and creates `repo-workbench-1` from the `repo-workbench` ActorTemplate.

The new Actor appears in the list as suspended. Creating it does not immediately assign it to a Worker. Selecting the run is an explicit request to resume it and inspect its ready state.

The equivalent CLI command is:

```shell
kubectl ate create actor repo-workbench-1 \
  -a book-workbench \
  --template repo-workbench
```

Inspect the control-plane view:

```shell
kubectl ate get actors -a book-workbench
kubectl ate get workers
```

### 5. Advance the stateful workflow

Select `repo-workbench-1`, then use **Next step** to execute one transition at a time:

1. Inspect repository
2. Reproduce the failure
3. Diagnose the bug
4. Repair the implementation
5. Write the agent report
6. Verify the test suite
7. Review the changes

After each step, the UI shows:

- The completed-step count and next transition.
- The accumulated Actor transcript.
- The repository file tree.
- Modified and newly added file markers.
- The selected file, complete Git diff, and latest test output.

Step four modifies `src/slugify.py`. Step five creates `AGENT_REPORT.md`. These files live in the Actor's durable directory, while the step number and transcript live in the Actor's process memory.

Every advance request includes the expected current step. A duplicate or stale request receives a conflict instead of executing another transition.

### 6. Suspend and resume at the same step

Advance the Actor partway through its task, then click **Suspend**.

The control plane snapshots the Actor and releases its Worker assignment. The UI retains the last observed task details but performs no Actor polling. The Actor list reports the control-plane lifecycle as suspended.

Confirm the capacity was released:

```shell
kubectl ate get actor repo-workbench-1 -a book-workbench
kubectl ate get workers
```

The equivalent CLI operation is:

```shell
kubectl ate suspend actor repo-workbench-1 -a book-workbench
```

Click **Resume & next step**. That task request passes through AgentGateway, resumes the same Actor, and executes exactly the next transition. The previous step position, transcript, modified files, and new files remain intact.

### 7. Run several independent Actors

Click **New run** again to create `repo-workbench-2`. Each Actor has its own:

- Workflow position
- Transcript and state revision
- Repository files and Git diff
- Test output
- Suspend/resume lifecycle

Advance the two Actors to different steps and switch between them. Selecting a run may resume that Actor; refreshing the run list alone does not.

The WorkerPool currently has one Worker with capacity for one Actor. Suspend the active Actor before opening another, or scale the WorkerPool when demonstrating concurrent execution.

The CLI helper also creates the next ordinal:

```shell
./scripts/create-actor.sh
```

An explicit name may be supplied:

```shell
./scripts/create-actor.sh repo-workbench-7
```

### 8. Fast-forward and complete a task

Use **Fast-forward** to execute every remaining transition through the same state-machine path. It is not a separate repair implementation.

When the final review step completes, the Actor enters the terminal `done` task state. Its result remains available for inspection. The normal next actions are to keep it as a completed run, delete it, or create another Actor; the workflow does not reset a completed Actor.

Deleting an Actor removes its task history and durable repository. The UI asks for confirmation before issuing that control-plane operation.

## State represented by the demo

| State | Location | UI behavior |
| --- | --- | --- |
| Actor lifecycle and assignment | Substrate control plane | Listed without contacting or waking Actors |
| Current step, transcript, test output | Full Actor process snapshot | Restored after suspend/resume |
| Repository files and Git changes | Actor `durableDir` | Modified and added files remain visible |
| Actor identity | Read-only `systemInfo` volume | Atespace, Actor name, and UID identify the run |
| Prepared compute | `generic-worker` WorkerPool | Reused as Actors suspend, resume, and finish |

## Run the components locally

Local mode exercises the same state machine and file UI without a Substrate control plane. Actor creation, suspension, and deletion are unavailable; the web app lists one configured local Actor.

Start the agent with a disposable workspace:

```shell
PORT=8081 \
WORKSPACE_DIR=/tmp/repo-workbench/repository \
SEED_DIR="$PWD/sample-project" \
python3 workbench/server.py
```

In another terminal:

```shell
PORT=8080 \
AGENT_URL=http://127.0.0.1:8081 \
TARGET_ACTOR=local/repo-workbench-1 \
python3 webapp/server.py
```

Open [http://localhost:8080](http://localhost:8080), select the local run, and advance or fast-forward its steps.

## Test the example

```shell
python3 -m unittest discover -s tests -v
```

The tests cover step-by-step execution, fast-forward equivalence, stale-step conflicts, durable file changes, Actor identity, Actor routing, control-plane management endpoints, verified CLI downloads, lab context isolation, and image-reference validation.

After setup, verify the deployed example on an empty `book-workbench` Atespace:

```shell
./scripts/verify-example.sh
```

This verification creates two Actors and exercises the deployed web backend, control plane, and AgentGateway. It checks independent workflow state, stale-request conflicts, one Worker Pod reused by both Actors, full suspend/resume restoration of memory and durable files, and seven-step completion with passing repository tests. It deletes the Actors it created before exiting. It refuses to run when the Atespace already contains Actors.

## Automated lab regression suite

Run a fresh setup, the application/setup tests, the deployed demo verifier, and the Chapter 4–6 regression checks with one command:

```shell
./scripts/test-lab.sh
```

The runner selects a unique kind cluster name and refuses to use an existing cluster. It deletes its test resources and cluster on success or failure, restores the previous Kubernetes context, and saves logs under `.test-results/<cluster-name>/`. Set `KIND_CLUSTER_NAME` to choose a new name. `--keep-cluster` retains the disposable lab for a browser check; use `teardown-example.sh` and `delete-kind.sh` with the same name afterward.

The maintained chapter suite checks capacity exhaustion, request parking, Full and Data snapshots, pause/resume, tags, Worker-loss recovery, stable identity, and HTTP policy admission. Its parked-success case temporarily sets a 30-second request budget to accommodate slower test hosts, then restores the original router configuration. The default-budget timeout is checked separately. Browser interaction and outbound enforcement are not part of this suite.

The Linux GitHub Actions workflow runs the same fresh-lab command on `ubuntu-24.04` and retains test logs for seven days. On Linux or WSL2, `./scripts/install-ci-tools.sh` installs checksum-verified kind, kubectl, and Helm into `.ci-tools/bin` when those tools are needed. See [the testing guide](docs/testing.md) for commands and coverage, and the [dated alpha2 validation record](docs/validation-kagent-alpha2.md) for the exact host, artifacts, lifecycle checks, and remaining gaps from the local clean run.

## Clean up

Delete every example Actor, the ActorTemplate, Atespace, and Kubernetes namespace. Teardown waits for the demo Pods to exit and removes their certificate requests before deleting the namespace:

```shell
./scripts/teardown-example.sh
```

Delete the named kind cluster:

```shell
./scripts/delete-kind.sh
```

The shared `kind-registry` container is retained because other kind clusters may use it. The downloaded CLI binaries remain in the ignored `.bin/` directory for reuse.

## Follow-up: publish the companion images

After validating the published Substrate runtime, publish the agent and web application images for `linux/amd64` and `linux/arm64`. The web application must include the matching released Linux CLI. Pin both published image digests in the companion so that the reader setup no longer builds demo images or needs the local registry.

The deployment script already accepts `BOOK_AGENT_IMAGE` and `BOOK_WEBAPP_IMAGE` together, each as a complete image reference ending in `@sha256:<digest>`. When both are supplied, it skips the demo builds. Until publication, the default uses local Docker builds of these two images only.

## License

This repository is licensed under the [Apache License, Version 2.0](LICENSE).
