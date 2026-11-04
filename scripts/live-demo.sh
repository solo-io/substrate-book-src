#!/usr/bin/env bash
# Presenter-paced credential brokering demo for a live talk.
#
# Resets the demo lab with prep-credential-demo.sh, shows a one-screen setup
# card (agent, gateway, git server), then tells a 3-5 minute story in five
# steps: Substrate basics, the agent's code, a dummy key that
# fails, gateway credential injection that succeeds, and the Actor's identity
# on egress. Each command is shown and runs when you press a key; its output
# stays alone on screen until you press another, so you can talk it through.
# Each step ends with a one-line takeaway. scripts/demo-ui.sh holds these
# presenter helpers. docs/credential-brokering-walkthrough.md is the long form.
#
#   ./scripts/live-demo.sh               # prep, then present
#   ./scripts/live-demo.sh --no-prep     # present against an already-prepped lab
#   ./scripts/live-demo.sh --auto 2      # rehearse hands-free, 2s per pause
#   ./scripts/live-demo.sh --logs right  # log pane beside instead of below
#   ./scripts/live-demo.sh --broker openbao
#   ./scripts/live-demo.sh --broker openbao-actor
#
# --broker openbao tells the same story with the token in OpenBao instead of a
# Kubernetes Secret (install it first with setup-openbao.sh). The provider logs
# in to OpenBao as its own ServiceAccount: step 4 shows the token in OpenBao and
# OpenBao's audit log recording the provider's read on the Actor's behalf,
# step 5 shows the provider refusing another Atespace's token to this Actor,
# and step 6 adds the Actor's identity on egress, as the Kubernetes version's
# step 5 does.
#
# --broker openbao-actor uses the actor-jwt provider, which setup-openbao.sh
# installs alongside it. It logs in to OpenBao as the Actor itself: OpenBao's audit
# log names the Actor, in step 5 OpenBao's own policy refuses, and step 6 adds
# the Actor's identity on egress.
#
# docs/openbao-credential-walkthrough.md is the long form of both.
#
# Inside tmux it opens a pane that follows the source-control service's request
# log (scripts/demo-logs.sh), so the audience sees each request arrive with the
# dummy key, the real injected token, or the decoded Actor JWT. Prep turns on
# the service's DEMO_SHOW_SECRETS so it logs them as is. --logs off skips it.
#
# --check-snippets verifies that the code excerpts still match the source.
#
# Keys while presenting: any key runs the next command or advances; q quits.
# Targets KIND_CLUSTER_NAME (default book-v060-cred) by --context only; it
# never switches your current kube context.

set -o errexit -o nounset -o pipefail
export KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-book-v060-cred}"
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
cd "${BOOK_EXAMPLES_ROOT}"

PREP=true
CHECK_SNIPPETS=false
AUTO=""
LOGS=bottom
BROKER=k8s
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-prep) PREP=false ;;
    --broker) BROKER="${2:-}"; shift
              [[ "${BROKER}" =~ ^(k8s|openbao|openbao-actor)$ ]] || { echo "--broker takes k8s, openbao, or openbao-actor" >&2; exit 2; } ;;
    --check-snippets) CHECK_SNIPPETS=true ;;
    --auto) AUTO="${2:-2}"; shift ;;
    --logs) LOGS="${2:-}"; shift
            [[ "${LOGS}" =~ ^(bottom|right|off)$ ]] || { echo "--logs takes bottom, right, or off" >&2; exit 2; } ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done
CTX="${KUBECTL_CONTEXT}"
NS="${BOOK_ATESPACE}"
A1="${BOOK_ACTOR_PREFIX}-1"
TOTAL=5
[[ "${BROKER}" == openbao* ]] && TOTAL=6

# ── look ─────────────────────────────────────────────────────────────────────
# Colors, pause, run, say, takeaway, and spin are shared with talk scripts.
source "${BOOK_EXAMPLES_ROOT}/scripts/demo-ui.sh"

LOG_PANE=""
cleanup_tty() {
  tput cnorm 2>/dev/null || true
  printf '%s' "${RST}"
  [[ -n "${LOG_PANE}" ]] && tmux kill-pane -t "${LOG_PANE}" 2>/dev/null || true
}
trap cleanup_tty EXIT
trap 'cleanup_tty; echo; exit 130' INT

step() {
  local n="$1" title="$2" dots="" i
  for ((i = 1; i <= TOTAL; i++)); do
    if ((i < n)); then dots+="${GRN}●"; elif ((i == n)); then dots+="${ACC}●"; else dots+="${GRY}○"; fi
    dots+=" "
  done
  clear
  hr "${ACC}"
  printf '  %s%s%d/%d%s  %s%s%s\n' "${ACC}" "${B}" "${n}" "${TOTAL}" "${RST}" "${B}" "${title}" "${RST}"
  printf '  %s%s\n' "${dots}" "${RST}"
  hr "${ACC}"
  echo
}

# ── commands the audience sees ───────────────────────────────────────────────
# kubectl and kubectl ate always target the demo lab.
kubectl() {
  if [[ "${1:-}" == ate ]]; then
    shift
    { "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" --context "${CTX}" "$@" 2>&1; } |
      { grep -v 'no --token-file given' || true; } | trim_tables
  else
    command kubectl --context "${CTX}" "$@"
  fi
}

# Trim the wide `kubectl ate get actors` and `get workers` tables so they fit a
# projector: Actors keep NAME / STATE / WORKER POD / AGE; Workers keep their Pod,
# STATE, ACTORS, and AGE.
trim_tables() {
  awk '
    NR == 1 && /^ATESPACE +NAME +TEMPLATE +STATE/ {
      table = "actors"
      split("NAME|TEMPLATE|STATE|WORKER POD|WORKER IPS|VERSION|AGE", all, "|")
      for (i = 1; i <= 7; i++) start[all[i]] = index($0, all[i])
    }
    NR == 1 && /^NAME +POOL +STATE +ACTORS/ {
      table = "workers"
      split("NAME|POOL|STATE|ACTORS|CPU|MEMORY|POD|AGE", all, "|")
      for (i = 1; i <= 8; i++) start[all[i]] = index($0, all[i])
      printf "%-32s %-8s %-8s %s\n", "WORKER POD", "STATE", "ACTORS", "AGE"
      next
    }
    !table { print; next }
    table == "workers" {
      state = substr($0, start["STATE"], start["ACTORS"] - start["STATE"])
      actors = substr($0, start["ACTORS"], start["CPU"] - start["ACTORS"])
      pod = substr($0, start["POD"], start["AGE"] - start["POD"])
      age = substr($0, start["AGE"])
      sub(/WORKER_STATE_/, "", state); sub(/^[^\/]*\//, "", pod)
      gsub(/ +$/, "", state); gsub(/ +$/, "", actors); gsub(/ +$/, "", pod)
      printf "%-32s %-8s %-8s %s\n", pod, state, actors, age
      next
    }
    {
      name = substr($0, start["NAME"], start["TEMPLATE"] - start["NAME"])
      state = substr($0, start["STATE"], start["WORKER POD"] - start["STATE"])
      pod = substr($0, start["WORKER POD"], start["WORKER IPS"] - start["WORKER POD"])
      age = substr($0, start["AGE"])
      sub(/ACTOR_STATE_/, "", state); sub(/^book-workbench\//, "", pod)
      gsub(/ +$/, "", name); gsub(/ +$/, "", state); gsub(/ +$/, "", pod)
      printf "%-18s %-11s %-32s %s\n", name, state, pod, age
    }'
}

# Call the companion backend inside the cluster (the Chapter 3 bridge).
wb() {
  command kubectl --context "${CTX}" exec -n "${NS}" deployment/repo-workbench-webapp -- python3 -c '
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
path, method, body = sys.argv[1:]
request = Request("http://127.0.0.1" + path, data=body.encode() if body else None,
                  method=method, headers={"Content-Type": "application/json"})
try:
    response = urlopen(request, timeout=600)
except HTTPError as error:
    response = error
with response:
    print(response.read().decode())
' "$1" "${2:-GET}" "${3:-}"
}

# actor <name> work | publish [--assert]: drive the Actor's own HTTP API.
actor() {
  local name="$1" action="$2"
  case "${action}" in
    work)
      local step
      step="$(wb "/api/actors/${name}/state" | jq -r '.currentStep')"
      wb "/api/actors/${name}/fast-forward" POST "{\"expectedStep\": ${step}}" | jq -r --arg g "${GRN}" --arg y "${YLW}" --arg r "${RST}" '
        "inspect → reproduce → diagnose → repair → report → verify → review",
        "phase   \($g)\(.phase)\($r)  (step \(.currentStep)/7)",
        "report  \(if .canPublish then "\($g)AGENT_REPORT.md written\($r)" else "not yet" end)",
        "uid     \($y)\(.actor.uid)\($r)"'
      ;;
    publish)
      local assert=false
      [[ "${3:-}" == --assert ]] && assert=true
      publish_render "${name}" "${assert}"
      ;;
    *) echo "usage: actor <name> work|publish [--assert]" >&2; return 2 ;;
  esac
}

publish_once() {
  wb "/api/actors/$1/publish" POST "{\"assertActor\": $2}" | jq -c '.publish'
}

publish_render() {
  local name="$1" assert="$2" result tries=0
  result="$(publish_once "${name}" "${assert}")"
  # A fresh EgressPolicy takes a few seconds to reach the DNS relay and gateway.
  while [[ "${EXPECT_ROUTED:-}" == true ]] && jq -e '.error // "" | test("Name or service")' <<<"${result}" >/dev/null &&
        ((tries++ < 15)); do
    { printf '\r    %s(policy propagating, retry %d)%s' "${DIM}" "${tries}" "${RST}" >/dev/tty; } 2>/dev/null || true
    sleep 2
    result="$(publish_once "${name}" "${assert}")"
  done
  if ((tries > 0)); then { printf '\r%*s\r' 40 '' >/dev/tty; } 2>/dev/null || true; fi
  jq -r --arg g "${GRN}" --arg y "${YLW}" --arg rd "${RED}" --arg r "${RST}" --arg d "${DIM}" '
    def why: if (.response | type) == "object" then (.response.error // .response.message // (.response | tostring))
             elif (.response // "" | tostring | test("is not permitted")) then
               .response | capture("(?<m>atespace .*)") | .m | gsub("[\\\\\"]"; "") | "provider refused: \(.)"
             else (.response // "" | tostring) end | .[0:110];
    "actor sent   \($y)\(.sentHeaders | to_entries | map("\(.key): \(.value)") | join("   "))\($r)",
    (if .error and (.error | test("Name or service")) then
       "result       \($rd)✘ blocked in the sandbox\($r): DNS refused \($d)(no policy for this host)\($r)"
     elif .error then "result       \($rd)✘ \(.error)\($r)"
     elif .status == 201 then "result       \($g)HTTP 201\($r)  PR #\(.response.number) opened as \($y)\(.response.authenticatedAs)\($r)"
     else "result       \($rd)HTTP \(.status)\($r)  \(why)" end),
    (if .status != 201 then empty
     elif .response.actor then "actor claim  \($g)\(.response.actor.subject)\($r)  uid=\($y)\(.response.actor.uid)\($r)"
     else "actor claim  \($d)none: the service only knows the account\($r)" end)' <<<"${result}"
}

# What the source-control service recorded: the token it received and, when
# the Actor's JWT came along, that JWT decoded.
pulls() {
  local last
  last="$(wb /api/source-control/pulls | jq -c '.pulls[-1] // empty')"
  [[ -n "${last}" ]] || return 0
  local source="Secret workbench-secrets/git-token"
  [[ "${BROKER}" == openbao* ]] && source="OpenBao ${OPENBAO_SECRET_PATH}"
  jq -r --arg secret "${SECRET}" --arg source "${source}" --arg g "${GRN}" --arg y "${YLW}" --arg r "${RST}" '
    "PR #\(.number) by account \($y)\(.authenticatedAs)\($r)",
    "token  \($y)\(.token)\($r)  \(if .token == $secret then "\($g)✔ the real token from \($source)\($r)" else "" end)"' <<<"${last}"
  if jq -e '.actorAssertion' <<<"${last}" >/dev/null; then
    jwt_decode "$(jq -r '.actorAssertion' <<<"${last}")"
  fi
}

# Pretty-print a JWT's header and payload (decoded, not verified).
jwt_decode() {
  local part
  printf 'Actor JWT  %s%s…%s\n' "${YLW}" "${1:0:40}" "${RST}"
  for part in header payload; do
    printf '%s%s%s\n' "${GRY}" "${part}" "${RST}"
    jq -CR --arg part "${part}" '
      split(".")[if $part == "header" then 0 else 1 end] | gsub("-"; "+") | gsub("_"; "/") |
      (. + ("=" * ((4 - length % 4) % 4))) | @base64d | fromjson' <<<"$1"
  done
}

# What OpenBao's audit log recorded for the providers: each login and each
# read, by who OpenBao authenticated (the provider's ServiceAccount, or an
# Actor's own entity), the Actor a read was for, and whether it was allowed.
openbao_audit() {
  command kubectl --context "${CTX}" logs -n openbao deployment/openbao --since-time="${AUDIT_SINCE}" 2>/dev/null |
    jq -rR --arg g "${GRN}" --arg y "${YLW}" --arg c "${CYN}" --arg rd "${RED}" --arg r "${RST}" --arg d "${DIM}" '
      fromjson? | select(.type == "response")
      | select((.auth.display_name // "") | test("^(jwt|kubernetes)-"))
      | select((.request.path | test("^auth/(jwt|kubernetes)/login$")) or (.request.path | startswith("secret/data/")))
      | (.request.path | endswith("/login")) as $login
      | (if .auth.metadata.service_account_name
         then "sa \(.auth.metadata.service_account_namespace)/\(.auth.metadata.service_account_name)"
         else .auth.display_name | sub("^jwt-"; "") end) as $who
      | (.request.headers["x-substrate-actor"][0] // "") as $for
      | "\($d)\(.time[11:19])\($r)  \(if $login then "login" else "read " end)  \($y)\($who)\($r)"
        + (if $login then "" else "  \(.request.path | sub("^secret/data/"; ""))" end)
        + (if $login or $for == "" or ($who | startswith("actor/")) then "" else "  \($c)for \($for)\($r)" end)
        + "  \(if .error then "\($rd)✘ \(.error | gsub("[\\n\\t]+"; " ") | sub("^1 error occurred: \\* "; "") | sub(" +$"; ""))\($r)" else "\($g)✔\($r)" end)"' |
    tail -n 3
}

# The default OpenBao provider's last refusal, from its own log.
provider_refusal() {
  command kubectl --context "${CTX}" logs -n "${OPENBAO_PROVIDER_NAMESPACE}" deployment/openbao-credential-provider \
      --since-time="${AUDIT_SINCE}" 2>/dev/null |
    jq -rR --arg rd "${RED}" --arg y "${YLW}" --arg r "${RST}" '
      fromjson? | select(.msg | startswith("credential request denied"))
      | "\($rd)✘ \(.msg | sub("^credential request denied: "; ""))\($r)\n  actor \($y)\(.actor)\($r)  secret \($y)\(.secret)\($r)"' |
    tail -n 2
}

# Show a policy file, marking lines that are new since the previous one.
show_policy() {
  local file="$1" previous="${2:-}" line
  prompt_cmd "cat ${file}"
  printf '\n'
  while IFS= read -r line; do
    local gutter="  "
    if [[ -n "${previous}" ]] && ! grep -qxF -- "${line}" "${previous}"; then gutter="${GRN}▍${RST} "; fi
    printf '    %s%s\n' "${gutter}" "$(sed -E "s/: (.+)$/: ${YLW}\1${RST}/; s/^( *- )([^:]+)$/\1${YLW}\2${RST}/; s/^( *-? *)([A-Za-z]+):/\1${CYN}\2${RST}:/" <<<"${line}")"
  done <"${file}"
  after_output
}

# Swap the Actor's policy. The previous one is deleted quietly first, because
# `kubectl ate update egress-policy` needs the stored uid and version.
apply_policy() {
  "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" --context "${CTX}" delete egress-policy "${A1}" -a "${NS}" >/dev/null 2>&1 || true
  run "kubectl ate create egress-policy ${A1} -a ${NS} -f $1"
}

# Code excerpts: a file, then start/end text pairs. Matching text instead of
# line numbers keeps the excerpts honest; --check-snippets fails on any drift.
AGENT_CODE=(workbench/server.py
  'PLACEHOLDER = "placeholder"' 'PLACEHOLDER = "placeholder"'
  'headers = {"Content-Type": "application/json", "Authorization": PLACEHOLDER}' 'headers[ASSERTION_HEADER] = PLACEHOLDER')

# code_ranges <file> <start> <end> ...: print "line<TAB>text" for each range, "--" between.
code_ranges() {
  local file="$1" start end first last count
  shift
  while (($#)); do
    start="$1" end="$2"
    shift 2
    count="$(grep -cF -- "${start}" "${file}" || true)"
    [[ "${count}" == 1 ]] || { echo "excerpt start found ${count} times in ${file}: ${start}" >&2; return 1; }
    first="$(grep -nF -- "${start}" "${file}" | cut -d: -f1)"
    last="$(awk -v first="${first}" -v end="${end}" 'NR >= first && index($0, end) { print NR; exit }' "${file}")"
    [[ -n "${last}" ]] && ((last - first < 8)) || { echo "excerpt end not found near line ${first} of ${file}: ${end}" >&2; return 1; }
    awk -v first="${first}" -v last="${last}" 'NR >= first && NR <= last { printf "%d\t%s\n", NR, $0 }' "${file}"
    if (($#)); then echo "--"; fi
  done
}

# show_code <label> <highlight-regex> <file> <start> <end> ...: an editor-style excerpt.
show_code() {
  local label="$1" highlight="$2" excerpt line number
  shift 2
  excerpt="$(code_ranges "$@")" || exit 1
  # Dedent each range on its own so nested code lines up beside module-level code.
  excerpt="$(awk -F'\t' '
    function flush() { for (i = 1; i <= n; i++) print num[i] "\t" substr(txt[i], min + 1); n = 0; min = -1 }
    BEGIN { min = -1 }
    $0 == "--" { flush(); print; next }
    { n++; num[n] = $1; txt[n] = $2; match($2, /^ */); if (min < 0 || RLENGTH < min) min = RLENGTH }
    END { flush() }' <<<"${excerpt}")"
  printf '\n    %s── %s%s%s · %s%s\n' "${GRY}" "${CYN}" "$1" "${GRY}" "${label}" "${RST}"
  while IFS= read -r line; do
    if [[ "${line}" == "--" ]]; then printf '    %s   ⋮%s\n' "${GRY}" "${RST}"; continue; fi
    number="${line%%$'\t'*}"
    text="$(sed -E "s/(${highlight})/${YLW}\1${RST}/g" <<<"${line#*$'\t'}")"
    printf '    %s%4d%s  %s\n' "${GRY}" "${number}" "${RST}" "${text}"
  done <<<"${excerpt}"
}

if [[ "${CHECK_SNIPPETS}" == true ]]; then
  trap - EXIT
  code_ranges "${AGENT_CODE[@]}" >/dev/null || exit 1
  echo "code excerpts match the source"
  exit 0
fi
for command in kubectl jq; do require_command "${command}"; done
ensure_cli

# ── prep ─────────────────────────────────────────────────────────────────────
clear
cat <<EOF

  ${ACC}${B}┌──────────────────────────────────────────────────────────┐${RST}
  ${ACC}${B}│${RST}  ${B}The agent never holds the token${RST}                         ${ACC}${B}│${RST}
  ${ACC}${B}│${RST}  ${GRY}credential brokering with Agent Substrate${RST}               ${ACC}${B}│${RST}
  ${ACC}${B}└──────────────────────────────────────────────────────────┘${RST}

  ${GRY}lab ${RST}${CTX}  ${GRY}substrate ${RST}${SUBSTRATE_VERSION}  ${GRY}broker ${RST}$(case "${BROKER}" in openbao) echo OpenBao ;; openbao-actor) echo "OpenBao, as each Actor" ;; *) echo "Kubernetes Secret" ;; esac)

EOF

if [[ "${PREP}" == true ]]; then
  prep_log="$(mktemp -t live-demo-prep)"
  prep_script=prep-credential-demo.sh
  [[ "${BROKER}" == openbao* ]] && prep_script=prep-openbao-demo.sh
  "${BOOK_EXAMPLES_ROOT}/scripts/${prep_script}" --show-secrets >"${prep_log}" 2>&1 &
  prep_pid=$!
  spin "resetting the lab: no Actors, fresh source-control, empty gateway cache…" "${prep_pid}"
  if ! wait "${prep_pid}"; then
    printf '  %s✘ prep failed%s\n\n' "${RED}" "${RST}"
    sed 's/^/    /' "${prep_log}"
    exit 1
  fi
  rm -f "${prep_log}"
  printf '  %s✔%s lab reset\n' "${GRN}" "${RST}"
elif ! golden_tag_ready >/dev/null; then
  # prep checks this too; without prep, make sure step 1 has a snapshot to restore from.
  printf '  %s✘ ActorTemplate %s has no stored golden snapshot; run ./scripts/setup-kind.sh%s\n' \
    "${RED}" "${BOOK_TEMPLATE}" "${RST}"
  exit 1
elif [[ "$(command kubectl --context "${CTX}" get deployment/source-control -n "${NS}" \
        -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="DEMO_SHOW_SECRETS")].value}')" != 1 ]]; then
  printf '  %s✘ source-control is not showing secrets; drop --no-prep so prep can turn it on%s\n' "${RED}" "${RST}"
  exit 1
elif ate get actor "${A1}" -a "${NS}" >/dev/null 2>&1; then
  printf '  %s✘ %s already exists from an earlier run; drop --no-prep to reset the lab%s\n' "${RED}" "${A1}" "${RST}"
  exit 1
fi
SECRET="$(command kubectl --context "${CTX}" get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' |
  base64 -d)"
# OpenBao's audit view shows only this run's requests.
AUDIT_SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# The log pane starts after prep so it follows the fresh source-control Pod.
if [[ "${LOGS}" != off && -n "${TMUX:-}" ]] && command -v tmux >/dev/null; then
  if [[ "${LOGS}" == right ]]; then split=(-h -l 42%); else split=(-v -l 40%); fi
  LOG_PANE="$(tmux split-window -d "${split[@]}" -P -F '#{pane_id}' \
    "KIND_CLUSTER_NAME=${KIND_CLUSTER_NAME} '${BOOK_EXAMPLES_ROOT}/scripts/demo-logs.sh'")"
  COLS="$(tput cols 2>/dev/null || echo 100)"; ((COLS > 110)) && COLS=110
  printf '  %s✔%s server log pane open\n' "${GRN}" "${RST}"
elif [[ "${LOGS}" != off ]]; then
  printf '  %s(run inside tmux for a live server log pane)%s\n' "${GRY}" "${RST}"
fi
pause "start"

# ── setup ────────────────────────────────────────────────────────────────────
if [[ "${BROKER}" == openbao* ]]; then
  TOKEN_HOME_1="the token: in OpenBao,"
  TOKEN_HOME_2="outside the cluster's Secrets"
else
  TOKEN_HOME_1="the token: a Kubernetes"
  TOKEN_HOME_2="Secret, outside the sandbox"
fi
clear
hr "${ACC}"
printf '  %sThe setup%s\n' "${B}" "${RST}"
hr "${ACC}"
cat <<EOF

  ┌──────────────────┐     ┌──────────────┐     ┌─────────────────────┐
  │ ${B}repo-workbench-1${RST} │ ──► │ ${B}AgentGateway${RST} │ ──► │ ${B}source-control${RST}      │
  │ the agent        │     │ egress       │     │ internal git server │
  │ fixes a bug,     │     │ policy and   │     │ requires a Bearer   │
  │ opens a PR       │     │ credentials  │     │ token               │
  └──────────────────┘     └──────────────┘     └─────────────────────┘
    ${YLW}holds no token${RST}                           ${GRY}${TOKEN_HOME_1}${RST}
                                             ${GRY}${TOKEN_HOME_2}${RST}

  ${B}How does the agent open a PR without ever holding the token?${RST}
EOF
pause

# ── 1 ────────────────────────────────────────────────────────────────────────
step 1 "Substrate in 30 seconds"
say "${B}Worker${RST}: a prepared, sandboxed Pod. Capacity. Kubernetes sees this."
say "${B}Actor${RST}: one agent's stateful run. Created, suspended, resumed onto any Worker."
run "kubectl get pods -n ${NS} -l ate.dev/worker-pool=${BOOK_WORKER_POOL}"
say "Substrate's view of that Pod: one Worker, no Actors on it yet."
run "kubectl ate get workers -n ${NS}"
run "kubectl ate create actor ${A1} -a ${NS} --template ${BOOK_TEMPLATE}"
run "kubectl ate resume actor ${A1} -a ${NS}"
run "kubectl ate get actors -a ${NS}"
say "The agent does its job: fixes a failing test in a small repo and writes a report."
say "${GRY}(actor is a small helper that calls the agent's own API.)${RST}"
run "actor ${A1} work"
takeaway "Agents aren't Pods. An Actor's outbound traffic goes through AgentGateway."
pause

# ── 2 ────────────────────────────────────────────────────────────────────────
step 2 "The agent's code: a dummy key"
say "The agent has no token. Its code sends a placeholder:"
show_code "the agent" 'PLACEHOLDER|"placeholder"' "${AGENT_CODE[@]}"
echo
say "The git server accepts a PR only with a valid ${YLW}Bearer <token>${RST}."
takeaway "On its own, the agent can't succeed. Something has to supply the token."
pause

# ── 3 ────────────────────────────────────────────────────────────────────────
step 3 "No credential config: it fails"
say "Policy lets the agent reach the git server, nothing more:"
show_policy demo/policies/1-allow.yaml
apply_policy demo/policies/1-allow.yaml
EXPECT_ROUTED=true run "actor ${A1} publish"
if [[ -n "${LOG_PANE}" ]]; then say "The server pane shows exactly what arrived: the dummy key."; fi
takeaway "No credential config, no access. The server got the literal dummy key."
pause

# identity_step N POLICY PREVIOUS: the gateway injects a Substrate-minted Actor
# JWT next to the token, and the service verifies which Actor is calling.
identity_step() {
  local n="$1" policy="$2" previous="$3"
  step "${n}" "Which agent is calling? Its identity on egress"
  say "The token says ${YLW}repo-workbench-bot${RST}: a shared account. Who is actually calling?"
  say "Ask the gateway to inject a Substrate-signed JWT for this exact Actor:"
  show_policy "${policy}" "${previous}"
  apply_policy "${policy}"
  EXPECT_ROUTED=true run "actor ${A1} publish --assert"
  if [[ -n "${LOG_PANE}" ]]; then
    say "The server pane: a signed JWT arrived and verified, naming this exact Actor."
  else
    say "What the service received:"
    run "pulls"
  fi
  run "kubectl ate get actor ${A1} -a ${NS} -o json | jq -r .metadata.uid"
  takeaway "The service verified the signature: this exact Actor, by UID. No secret in the agent."
  pause "finish"
}

# ── 4-6, OpenBao, provider's ServiceAccount ──────────────────────────────────
if [[ "${BROKER}" == openbao ]]; then
  step 4 "AgentGateway injects a token from OpenBao"
  say "Same agent, same dummy key. This time the token lives in OpenBao:"
  run "bao kv get -field=token ${OPENBAO_SECRET_PATH}; echo"
  say "The rule names it by an ${YLW}ate-secret://openbao/...${RST} URI:"
  show_policy demo/policies/openbao/2-inject.yaml demo/policies/1-allow.yaml
  apply_policy demo/policies/openbao/2-inject.yaml
  EXPECT_ROUTED=true run "actor ${A1} publish"
  if [[ -n "${LOG_PANE}" ]]; then
    say "The server pane: the real bearer token arrived, the one from OpenBao."
  else
    say "What the service received:"
    run "pulls"
  fi
  say "The provider logged in to OpenBao as its own ServiceAccount, Kubernetes auth,"
  say "and named the Actor it read for in a request header OpenBao audits:"
  run "openbao_audit"
  takeaway "One broker identity reads from OpenBao, and the audit log says which Actor each read was for."
  pause

  step 5 "The provider decides what this Actor may read"
  say "Point the same rule at another Atespace's token, team-a's:"
  show_policy demo/policies/openbao/3-other-atespace.yaml demo/policies/openbao/2-inject.yaml
  apply_policy demo/policies/openbao/3-other-atespace.yaml
  EXPECT_ROUTED=true run "actor ${A1} publish"
  say "OpenBao would allow the provider this read. The provider refused it first:"
  run "provider_refusal"
  say "OpenBao never saw the request; its log still ends at step 4:"
  run "openbao_audit"
  takeaway "A policy can name any path. The provider keeps each Actor to its own Atespace's secrets, and the gateway fails closed."
  pause

  identity_step 6 demo/policies/openbao/4-assert.yaml demo/policies/openbao/2-inject.yaml

  clear
  echo
  hr "${ACC}"
  printf '  %sThe agent only ever sent "placeholder"%s\n' "${B}" "${RST}"
  hr "${ACC}"
  cat <<EOF

  ${GRY}question${RST}                         ${GRY}answered by${RST}
  Where may this Actor go?         ${CYN}its EgressPolicy${RST}
  Where does the credential live?  ${CYN}OpenBao, read by the provider's ServiceAccount${RST}
  Who may read which credential?   ${CYN}the provider: each Actor its own Atespace's path${RST}
  Who read what, and when?         ${CYN}OpenBao's audit log: the provider, for each Actor${RST}
  Who is really calling?           ${CYN}a Substrate-minted Actor JWT${RST}

  ${B}The token never enters the sandbox,${RST} and Substrate needed no change: the
  provider holds an OpenBao login, not Substrate's power to mint Actor tokens.

  ${GRY}Rerun ./scripts/live-demo.sh --broker openbao to reset and present again.${RST}

EOF
  if [[ -n "${LOG_PANE}" ]]; then pause "close"; fi
  exit 0
fi

# ── 4-6, OpenBao, as each Actor ──────────────────────────────────────────────
if [[ "${BROKER}" == openbao-actor ]]; then
  step 4 "AgentGateway injects a token from OpenBao"
  say "Same agent, same dummy key. This time the token lives in OpenBao:"
  run "bao kv get -field=token ${OPENBAO_SECRET_PATH}; echo"
  say "The rule names it by an ${YLW}ate-secret://openbao-actor/...${RST} URI:"
  show_policy demo/policies/openbao-actor/2-inject.yaml demo/policies/1-allow.yaml
  apply_policy demo/policies/openbao-actor/2-inject.yaml
  EXPECT_ROUTED=true run "actor ${A1} publish"
  if [[ -n "${LOG_PANE}" ]]; then
    say "The server pane: the real bearer token arrived, the one from OpenBao."
  else
    say "What the service received:"
    run "pulls"
  fi
  say "Who read it from OpenBao? Not a shared broker account. The provider logged in"
  say "as this Actor, with a Substrate-minted Actor JWT, and read with its token:"
  run "openbao_audit"
  takeaway "OpenBao saw ${A1} itself log in and read the token. The agent never held it."
  pause

  step 5 "OpenBao decides what this Actor may read"
  say "Point the same rule at another Atespace's token, team-a's:"
  show_policy demo/policies/openbao-actor/3-other-atespace.yaml demo/policies/openbao-actor/2-inject.yaml
  apply_policy demo/policies/openbao-actor/3-other-atespace.yaml
  EXPECT_ROUTED=true run "actor ${A1} publish"
  say "OpenBao's policy keys on the Atespace in the Actor's JWT:"
  run "openbao_audit"
  takeaway "A policy can name any path. OpenBao refuses what this Actor may not read, and the gateway fails closed."
  pause

  identity_step 6 demo/policies/openbao-actor/4-assert.yaml demo/policies/openbao-actor/2-inject.yaml

  clear
  echo
  hr "${ACC}"
  printf '  %sThe agent only ever sent "placeholder"%s\n' "${B}" "${RST}"
  hr "${ACC}"
  cat <<EOF

  ${GRY}question${RST}                         ${GRY}answered by${RST}
  Where may this Actor go?         ${CYN}its EgressPolicy${RST}
  Where does the credential live?  ${CYN}OpenBao, read with the Actor's own login${RST}
  Who may read which credential?   ${CYN}OpenBao's policy, keyed on Substrate's Actor JWT${RST}
  Who read what, and when?         ${CYN}OpenBao's audit log, by Actor${RST}
  Who is really calling?           ${CYN}a Substrate-minted Actor JWT${RST}

  ${B}The token never enters the sandbox,${RST} and no broker account reads on the
  Actors' behalf: OpenBao authorizes and audits each Actor itself.

  ${GRY}Rerun ./scripts/live-demo.sh --broker openbao-actor to reset and present again.${RST}

EOF
  if [[ -n "${LOG_PANE}" ]]; then pause "close"; fi
  exit 0
fi

# ── 4 ────────────────────────────────────────────────────────────────────────
step 4 "AgentGateway injects the real credential"
say "Same agent, same dummy key. The gateway replaces the header on the way out."
say "${GRY}(The platform granted Atespace ${NS} the Secrets in namespace workbench-secrets.)${RST}"
show_policy demo/policies/2-inject.yaml demo/policies/1-allow.yaml
say "The real token, in a Secret the agent can't read:"
run "kubectl get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' | base64 -d; echo"
apply_policy demo/policies/2-inject.yaml
EXPECT_ROUTED=true run "actor ${A1} publish"
if [[ -n "${LOG_PANE}" ]]; then
  say "The server pane: the real bearer token arrived, the one from the Secret."
else
  say "What the service received:"
  run "pulls"
fi
takeaway "The agent sent 'placeholder'. The service got the real token. The agent never saw it."
pause

# ── 5 ────────────────────────────────────────────────────────────────────────
identity_step 5 demo/policies/4-assert.yaml demo/policies/2-inject.yaml

# ── wrap ─────────────────────────────────────────────────────────────────────
clear
echo
hr "${ACC}"
printf '  %sThe agent only ever sent "placeholder"%s\n' "${B}" "${RST}"
hr "${ACC}"
cat <<EOF

  ${GRY}question${RST}                      ${GRY}answered by${RST}
  Where may this Actor go?      ${CYN}its EgressPolicy${RST}
  What credential is injected?  ${CYN}AgentGateway, from a platform-granted Secret${RST}
  Who is really calling?        ${CYN}a Substrate-minted Actor JWT${RST}

  ${B}The token never enters the sandbox.${RST} A compromised or prompt-injected agent
  can't leak it. It can still use it, but only where its policy allows, and the
  gateway logs which Actor made each call.

  ${GRY}Rerun ./scripts/live-demo.sh to reset and present again.${RST}

EOF
if [[ -n "${LOG_PANE}" ]]; then pause "close"; fi
