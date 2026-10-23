# Shell helpers for docs/credential-brokering-walkthrough.md. Source this file;
# do not execute it. It changes nothing in the cluster and never switches your
# current kube context: every command names the lab with --context.
#
#   KIND_CLUSTER_NAME=book-v060-cred source ./scripts/demo-env.sh

if [[ -n "${BASH_SOURCE:-}" && "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "source this file instead of running it: source ${BASH_SOURCE[0]}" >&2
  exit 2
fi

export CTX="kind-${KIND_CLUSTER_NAME:-book-v060-cred}"
__demo_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
case ":${PATH}:" in
  *":${__demo_root}/.bin:"*) ;;
  *) export PATH="${__demo_root}/.bin:${PATH}" ;;
esac
unset __demo_root

# wb <path> [GET|POST|DELETE] [json-body]: call the companion backend inside the cluster.
wb() {
  kubectl --context "$CTX" exec -n book-workbench deployment/repo-workbench-webapp -- python3 -c '
import json, sys
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

# publish <actor> [assert]: publish the Actor's report with placeholder credentials.
publish() {
  local assert=false
  [[ "${2:-}" == assert ]] && assert=true
  wb "/api/actors/$1/publish" POST "{\"assertActor\": ${assert}}" | jq '.publish'
}

echo "demo helpers loaded: CTX=${CTX}; wb and publish defined; kubectl ate on PATH"
