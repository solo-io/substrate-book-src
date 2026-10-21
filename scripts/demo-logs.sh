#!/usr/bin/env bash
# Follow the source-control service's request log, formatted for a demo pane:
# what each request carried when it arrived, and what the service decided.
# live-demo.sh opens this in a tmux pane; it also runs on its own.
#
#   KIND_CLUSTER_NAME=book-alpha3-cred ./scripts/demo-logs.sh

set -o errexit -o nounset -o pipefail
export KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-book-alpha3-cred}"
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_command kubectl

ESC=$'\033'
secret_fp="$(kube get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' 2>/dev/null |
  base64 -d | shasum -a 256 | cut -c1-16 || true)"

printf '%s%s source-control %s%s what the server receives%s\n' "${ESC}[1m" "${ESC}[38;5;213m" "${ESC}[0m" "${ESC}[38;5;244m" "${ESC}[0m"
printf '%s%s%s\n\n' "${ESC}[38;5;244m" "$(printf '%*s' 42 '' | tr ' ' '-')" "${ESC}[0m"

# Follow the newest live Pod, never one still terminating from an earlier run,
# and reconnect if it is replaced; a new Pod starts with an empty log.
while :; do
  pod="$(kube get pods -n "${BOOK_ATESPACE}" -l app=source-control -o json 2>/dev/null | jq -r '
    [.items[] | select(.metadata.deletionTimestamp == null and .status.phase == "Running")]
    | sort_by(.metadata.creationTimestamp) | last | .metadata.name // empty' || true)"
  if [[ -z "${pod}" ]]; then sleep 1; continue; fi
  kube logs -f "pod/${pod}" -n "${BOOK_ATESPACE}" 2>/dev/null |
    jq --unbuffered -rR --arg fp "${secret_fp}" --arg e "${ESC}" '
      fromjson? | select(.event == "pull_request") |
      ($e + "[0m") as $r | ($e + "[38;5;114m") as $g | ($e + "[38;5;203m") as $rd |
      ($e + "[38;5;221m") as $y | ($e + "[38;5;244m") as $d | ($e + "[38;5;81m") as $c |
      def hdr($name; $value; $note): "  \($c)\($name)\($r) \($y)\($value)\($r)\($note)";
      "\($d)\(.time[11:19])\($r)  POST /repos/\(.repo)/pulls",
      hdr("Authorization  "; .authorization // "missing";
          if (.authorization // "") == "Bearer sha256:\($fp)" and $fp != "" then "  \($g)✔ real token\($r)" else "" end),
      (if .actorAssertion == null then empty
       elif .actor then hdr("Actor-Assertion"; "signed JWT"; "  \($g)✔ verified\($r)"),
            "    \($g)\(.actor.subject)\($r)", "    \($d)uid\($r) \(.actor.uid)"
       else hdr("Actor-Assertion"; .actorAssertion; "") end),
      (if .status == 201 then "  \($g)✔ 201\($r) PR #\(.number) as \(.authenticatedAs)"
       else "  \($rd)✘ \(.status)\($r) \(.error)" end),
      ""' || true
  sleep 1
done
