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

## Current development setup

This pre-release example builds against a pinned commit in a local Substrate source checkout. The setup does not clone, fetch, or switch that checkout. By default, the repositories are siblings:

```text
agent-substrate/
  substrate-book/              Substrate source at the pinned commit
  book-src/
```

The pinned commit is declared in `scripts/lib.sh`. The setup stops with the required commit if the local checkout does not match. Set `SUBSTRATE_SOURCE_DIR` and `SUBSTRATE_SOURCE_COMMIT` together to use another explicitly selected source checkout.

### Prerequisites

- Docker with the daemon running
- Git
- Go 1.27 or newer, matching the current Substrate source tree
- `kubectl`
- `jq`

## Walk through Repo Workbench

Run all commands below from this repository.

### 1. Create the local platform

```shell
./scripts/setup-kind.sh
```

The setup script:

1. Creates or reuses the `kind-substrate` Kubernetes context.
2. Installs Substrate with AgentGateway as its ingress and egress data plane.
3. Builds the agent, web application, Linux `kubectl-ate` client, and matching gVisor Worker image.
4. Creates the `generic-worker` WorkerPool and web application.
5. Creates the `book-workbench` Atespace and `repo-workbench` ActorTemplate.
6. Waits for the ActorTemplate's golden snapshot.

It deliberately creates no repository-run Actor. The first Actor is created from the browser.

Use a clean cluster when rerunning setup after the ActorTemplate has been created:

```shell
./scripts/setup-kind.sh --recreate
```

Add the locally built CLI to the current shell:

```shell
export PATH="$PWD/.bin:$PATH"
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

The tests cover step-by-step execution, fast-forward equivalence, stale-step conflicts, durable file changes, Actor identity, Actor routing, and control-plane management endpoints.

## Clean up

Delete every example Actor, the ActorTemplate, Atespace, and Kubernetes namespace:

```shell
./scripts/teardown-example.sh
```

Delete the entire kind cluster and local registry:

```shell
./scripts/delete-kind.sh
```
