#!/usr/bin/env bash
# Reset a lab with the OpenBao credential broker installed (setup-openbao.sh)
# to the starting state of docs/openbao-credential-walkthrough.md: everything
# prep-credential-demo.sh resets (no Actors, a fresh source-control service, an
# empty gateway cache), plus:
#
#   - the gateway's openbao provider entries, reapplied if a helm upgrade
#     dropped them;
#   - the development OpenBao's configuration, reapplied in case it restarted
#     (it keeps everything in memory);
#   - the source-control account token stored in OpenBao for book-workbench,
#     and a different token stored for another Atespace, team-a;
#   - restarted providers, so the first request logs in to OpenBao afresh.
#
# --show-secrets is passed to prep-credential-demo.sh (live-demo.sh uses it).
#
# Usage: KIND_CLUSTER_NAME=<lab> ./scripts/prep-openbao-demo.sh [--show-secrets]

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

for command in kubectl jq openssl shasum git; do require_command "${command}"; done
prep_args=()
if [[ "${1:-}" == --show-secrets ]]; then prep_args=(--show-secrets); shift; fi
if [[ $# -gt 0 ]]; then echo "usage: KIND_CLUSTER_NAME=<lab> $0 [--show-secrets]" >&2; exit 2; fi
fail() { echo "error: $*" >&2; exit 1; }

ensure_cli
ensure_openbao_provider_source
kube get --raw=/readyz >/dev/null 2>&1 || fail "${KUBECTL_CONTEXT} is not reachable; set KIND_CLUSTER_NAME to the lab cluster"
providers=("${OPENBAO_PROVIDER_NAMESPACE}" "${OPENBAO_ACTOR_PROVIDER_NAMESPACE}")
for ns in "${providers[@]}"; do
  kube rollout status deployment/openbao-credential-provider -n "${ns}" --timeout=60s >/dev/null 2>&1 ||
    fail "the OpenBao credential provider in ${ns} is not running on ${KUBECTL_CONTEXT}; run ./scripts/setup-openbao.sh"
done
kube get deployment/ate-api-server -n ate-system -o json |
  jq -e --arg id "--egress-gateway-spiffe-id=${OPENBAO_ACTOR_PROVIDER_SPIFFE_ID}" \
    '.spec.template.spec.containers[] | select(.name == "ate-api-server") | .args | index($id)' >/dev/null ||
  fail "ate-api-server does not grant ${OPENBAO_ACTOR_PROVIDER_NAME} the egress_gateway role (a helm upgrade without its values?); run ./scripts/setup-openbao.sh"

echo "Checking the egress gateway names the OpenBao providers..."
for entry in "${OPENBAO_PROVIDER_NAME} ${OPENBAO_PROVIDER_ADDRESS}" "${OPENBAO_ACTOR_PROVIDER_NAME} ${OPENBAO_ACTOR_PROVIDER_ADDRESS}"; do
  read -r name address <<<"${entry}"
  case "$(ensure_gateway_openbao_provider "${name}" "${address}")" in
    added) echo "  re-added ${name} (a helm upgrade had rendered the gateway config again)" ;;
  esac
done

echo "Reapplying the development OpenBao's configuration..."
KUBECTL_CONTEXT="${KUBECTL_CONTEXT}" "${OPENBAO_PROVIDER_SOURCE_DIR}/hack/kind/setup-openbao.sh" >/dev/null

# The ${a[@]+...} form keeps an empty array legal under nounset in older bash.
"${BOOK_EXAMPLES_ROOT}/scripts/prep-credential-demo.sh" ${prep_args[@]+"${prep_args[@]}"} >/dev/null

echo "Storing the account token in OpenBao..."
# The token travels on stdin, never on a command line.
kube get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' | openssl base64 -d -A |
  bao kv put "${OPENBAO_SECRET_PATH}" token=- >/dev/null
openssl rand -hex 24 | tr -d '\n' | bao kv put "secret/atespaces/team-a/source-control" token=- >/dev/null
stored_fp="$(bao kv get -field=token "${OPENBAO_SECRET_PATH}" | shasum -a 256 | cut -c1-16)"
expected_fp="$(kube get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' | openssl base64 -d -A |
  shasum -a 256 | cut -c1-16)"
[[ "${stored_fp}" == "${expected_fp}" ]] || fail "the token in OpenBao does not match the account token"

echo "Restarting the providers so the first request logs in to OpenBao afresh..."
for ns in "${providers[@]}"; do
  kube rollout restart deployment/openbao-credential-provider -n "${ns}" >/dev/null
done
for ns in "${providers[@]}"; do
  kube rollout status deployment/openbao-credential-provider -n "${ns}" --timeout=180s >/dev/null
done

cat <<EOF

OpenBao credential demo ready on ${KUBECTL_CONTEXT} (${SUBSTRATE_VERSION}).
  Actors: none            source-control: fresh, 0 pulls
  OpenBao: ${OPENBAO_SECRET_PATH} holds the account token (fingerprint ${stored_fp})
           secret/atespaces/team-a/source-control holds another Atespace's token
  Providers: ${providers[*]}

Next, in this shell:
  KIND_CLUSTER_NAME=${KIND_CLUSTER_NAME} source ./scripts/demo-env.sh
then continue from section 2 of docs/openbao-credential-walkthrough.md.
EOF
