# Presenting the OpenBao live demo

The steps to present `./scripts/live-demo.sh --broker openbao`: the credential brokering story with the account token in OpenBao, read by a provider that logs in as its own ServiceAccount. [The OpenBao walkthrough](openbao-credential-walkthrough.md) is the long form, with expected output at every step.

## Before you start

- A book lab installed with `./scripts/setup-kind.sh`. These steps use `book-v100-rc1`. Every script targets `KIND_CLUSTER_NAME` with `--context` and never changes your current kube context. `live-demo.sh` defaults to `book-v060-cred`, so always set it.
- The OpenBao provider's source at `../credprovider-openbao`, checked out at the commit `OPENBAO_PROVIDER_COMMIT` in `scripts/lib.sh` names. Setup builds the provider from that checkout and fails if its `HEAD` differs.
- `kubectl`, `helm`, `jq`, `git`, `go`, `docker`, `openssl`, and `shasum`.
- tmux, for the pane that shows what each request carried when it reached the git server. Without tmux the demo still runs, without that pane.

Open the tmux session first, then set the lab and run everything from `book-src` in that session's shell:

```shell
tmux new -s demo        # or attach to the session you will present from
cd ~/go/src/github.com/agent-substrate/book-src
export KIND_CLUSTER_NAME=book-v100-rc1
```

Set the variable inside tmux, not before `tmux new`. When a tmux server is already running, a new session's shell starts from the server's environment, not from the shell you typed `tmux new` in, so a variable exported beforehand is gone. The demo's title card names the lab it targets; if it says `lab kind-book-v060-cred`, the variable is not set in this shell.

## 1. Install OpenBao, once per lab

```shell
./scripts/setup-openbao.sh
```

Setup upgrades the `substrate` Helm release, deploys a development OpenBao, builds and deploys both OpenBao providers (`openbao` and `openbao-actor`), and adds them to the egress gateway's config. It takes several minutes; run it well before the talk. It is safe to rerun.

It ends by printing both providers and their URIs. The install stays until you remove it in step 5; you do not need to rerun setup between presentations.

## 2. Rehearse

```shell
./scripts/live-demo.sh --broker openbao --auto 2
```

`--auto 2` runs the whole demo hands-free, pausing two seconds at each stop. Each run resets the lab first, so you can rehearse as often as you like.

## 3. Present

```shell
./scripts/live-demo.sh --broker openbao
```

The script first resets the lab with `prep-openbao-demo.sh --show-secrets`: it deletes any Actors, starts a fresh git server that logs tokens in clear text for the audience, restarts the egress gateway to empty its credential cache, writes the tokens into OpenBao again, and restarts the providers. That takes a minute or two behind a spinner. If it fails, the script prints the prep log and stops.

Then press any key to run each command or advance; `q` quits. The story:

| Step | What runs | What it shows |
| --- | --- | --- |
| Setup card | | The agent, AgentGateway, and the git server. The agent holds no token. |
| 1. Substrate in 30 seconds | `kubectl get pods`, `kubectl ate get workers`, create and resume `repo-workbench-1`, let it fix the bug | Agents are Actors, not Pods, and their traffic goes through AgentGateway. |
| 2. The agent's code | The code that sends `Authorization: placeholder` | On its own, the agent cannot succeed. |
| 3. No credential config | An egress policy that only allows the git server, then a publish | `401` from the git server, which received the dummy key. |
| 4. A token from OpenBao | `bao kv get` of the token, a policy naming `ate-secret://openbao/...`, a publish, OpenBao's audit log | `201`. The provider logged in as its ServiceAccount, read the token, and named the Actor in the audit log. |
| 5. The provider decides | A policy naming team-a's token, a publish, the provider's log, OpenBao's audit log | `403`. The provider refused before asking OpenBao, and OpenBao's log has no new entry. |
| 6. Its identity on egress | A policy that also injects an `Actor-Assertion` header, a publish, the Actor's UID | `201`. Next to the OpenBao token, the gateway injects a Substrate-minted Actor JWT, and the git server verifies it and records the Actor's UID. |
| Wrap-up | | Who answers each question: the EgressPolicy, OpenBao, the provider, OpenBao's audit log, the Actor JWT. |

To present again, rerun the same command; it resets the lab.

### Prepping ahead of time

To keep the prep spinner off the screen, prep before you go on and present with `--no-prep`:

```shell
./scripts/prep-openbao-demo.sh --show-secrets
./scripts/live-demo.sh --broker openbao --no-prep
```

`--no-prep` refuses to start if `repo-workbench-1` already exists or the git server is not showing tokens, so prep again after each run.

### Options

| Option | Effect |
| --- | --- |
| `--auto N` | Runs hands-free, pausing `N` seconds at each stop. |
| `--no-prep` | Skips the reset. |
| `--logs right` | Opens the git server's log pane beside the demo instead of below it. |
| `--logs off` | Opens no log pane. |
| `--broker openbao-actor` | The variant whose provider logs in to OpenBao as each Actor. Same install. |

## 4. If something goes wrong

- **Prep fails with "kind-book-v060-cred is not reachable".** `KIND_CLUSTER_NAME` is not set in this shell, so the demo fell back to its default lab. Export it in the shell you present from, as in "Before you start".
- **Step 4 returns `403`, and the gateway logs "no credential provider configured".** A `helm upgrade` rendered the gateway's config again without the OpenBao providers. Rerun the demo; prep adds them back.
- **Prep fails because a provider or OpenBao is not running.** Check `kubectl --context kind-$KIND_CLUSTER_NAME get pods -n openbao` and `-n credprovider-openbao`. If they are gone, rerun `./scripts/setup-openbao.sh`.
- **Every read is denied after OpenBao restarted.** The development OpenBao keeps its configuration and data in memory. Rerun the demo; prep configures it again.
- **No log pane.** The demo is not running inside tmux, or `--logs off` is set.

The [walkthrough's troubleshooting](openbao-credential-walkthrough.md#troubleshooting) covers more cases.

## 5. Remove OpenBao

```shell
./scripts/teardown-openbao.sh
```

Teardown restores the `substrate` release to `manifests/substrate-values.yaml` and deletes OpenBao and both providers. The lab keeps running the Kubernetes Secrets version of the demo, `./scripts/live-demo.sh`.
