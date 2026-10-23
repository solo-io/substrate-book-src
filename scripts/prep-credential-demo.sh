#!/usr/bin/env bash
# Reset a running book lab to the starting state of the credential brokering
# walkthrough (docs/credential-brokering-walkthrough.md, section 2): the lab
# installed, no Actors, a fresh source-control service with no pull history,
# and one account token shared by the credential store and the service.
#
# It assumes setup-kind.sh already installed Substrate and deployed the
# example. It never installs, upgrades, or deletes the cluster, and it refuses
# a cluster that is not this release's demo lab.
#
# --show-secrets makes the source-control service log and record the bearer
# token and Actor JWT it receives as is (live-demo.sh uses it). Without it, the
# service shows only token fingerprints.

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

for command in kubectl jq openssl shasum; do require_command "${command}"; done
ensure_cli
show_secrets=false
if [[ "${1:-}" == --show-secrets ]]; then show_secrets=true; shift; fi
if [[ $# -gt 0 ]]; then echo "usage: KIND_CLUSTER_NAME=<lab> $0 [--show-secrets]" >&2; exit 2; fi

fail() { echo "error: $*" >&2; exit 1; }

echo "Checking ${KUBECTL_CONTEXT} is the ${SUBSTRATE_VERSION} demo lab..."
kube get --raw=/readyz >/dev/null 2>&1 || fail "${KUBECTL_CONTEXT} is not reachable; set KIND_CLUSTER_NAME to the lab cluster"
installed_image="$(kube get deployment/ate-api-server -n ate-system -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
if [[ "${installed_image}" != "${SUBSTRATE_API_IMAGE}" &&
      "${installed_image}" != "ghcr.io/kagent-dev/substrate/ateapi:${SUBSTRATE_VERSION}" ]]; then
  fail "${KUBECTL_CONTEXT} does not run the locked ${SUBSTRATE_VERSION} API server (found '${installed_image:-none}'); this script only prepares the demo lab"
fi
for deployment in atenet-egress k8s-credential-provider ate-idp-server; do
  kube rollout status "deployment/${deployment}" -n ate-system --timeout=120s >/dev/null ||
    fail "deployment/${deployment} is not ready in ate-system"
done
grant="$(kube get configmap k8s-credential-provider-namespace-policy -n ate-system \
  -o jsonpath='{.data.namespace-policy\.yaml}' 2>/dev/null || true)"
if ! grep -q 'workbench-secrets' <<<"${grant}" || ! grep -q "${BOOK_ATESPACE}" <<<"${grant}"; then
  fail "the credential provider has no ${BOOK_ATESPACE} -> workbench-secrets grant; rerun ./scripts/setup-kind.sh so the chart values apply"
fi
for deployment in "${BOOK_WORKER_POOL}" repo-workbench-webapp source-control; do
  kube get "deployment/${deployment}" -n "${BOOK_ATESPACE}" >/dev/null 2>&1 ||
    fail "deployment/${deployment} is missing from ${BOOK_ATESPACE}; run ./scripts/setup-kind.sh first"
done
golden_tag="$(golden_tag_ready)" ||
  fail "ActorTemplate ${BOOK_TEMPLATE} has no stored golden snapshot; run ./scripts/setup-kind.sh first"

echo "Deleting Actors from earlier runs (their egress policies go with them)..."
actors_json="$(ate get actors -a "${BOOK_ATESPACE}" -o json 2>/dev/null || echo '{}')"
while IFS= read -r actor_name; do
  [[ -z "${actor_name}" ]] && continue
  ate delete actor "${actor_name}" -a "${BOOK_ATESPACE}"
done < <(jq -r '.actors[]?.metadata.name' <<<"${actors_json}")
deadline=$((SECONDS + 120))
while ((SECONDS < deadline)); do
  remaining="$(ate get actors -a "${BOOK_ATESPACE}" -o json 2>/dev/null | jq -r '.actors | length' || echo unknown)"
  [[ "${remaining}" == 0 ]] && break
  sleep 2
done
[[ "${remaining}" == 0 ]] || fail "Actors remain in ${BOOK_ATESPACE}: ${remaining}"
extra_templates="$(ate get actor-templates -a "${BOOK_ATESPACE}" -o json | jq -r --arg t "${BOOK_TEMPLATE}" '[.actorTemplates[]?.metadata.name | select(. != $t)] | join(" ")')"
extra_tags="$(ate get tags -a "${BOOK_ATESPACE}" -o json | jq -r --arg g "${golden_tag}" '[.tags[]?.metadata.name | select(. != $g)] | join(" ")')"
[[ -z "${extra_templates}" ]] || echo "note: extra ActorTemplates left in place: ${extra_templates}"
[[ -z "${extra_tags}" ]] || echo "note: extra Tags left in place: ${extra_tags}"

echo "Checking the account token in both Secrets..."
store_token="$(kube get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' 2>/dev/null | openssl base64 -d -A || true)"
service_token="$(kube get secret source-control-token -n "${BOOK_ATESPACE}" -o jsonpath='{.data.token}' 2>/dev/null | openssl base64 -d -A || true)"
if [[ -z "${store_token}" || "${store_token}" != "${service_token}" ]]; then
  echo "Secrets are missing or differ; writing one new token to both."
  store_token="$(openssl rand -hex 24)"
  kube create namespace workbench-secrets --dry-run=client -o yaml | kube apply -f - >/dev/null
  kube create secret generic git-token -n workbench-secrets --from-literal=token="${store_token}" \
    --dry-run=client -o yaml | kube apply -f - >/dev/null
  kube create secret generic source-control-token -n "${BOOK_ATESPACE}" --from-literal=token="${store_token}" \
    --dry-run=client -o yaml | kube apply -f - >/dev/null
fi
fingerprint="$(printf '%s' "${store_token}" | shasum -a 256 | cut -c1-16)"
unset store_token service_token

if [[ "${show_secrets}" == true ]]; then
  kube set env deployment/source-control -n "${BOOK_ATESPACE}" DEMO_SHOW_SECRETS=1 >/dev/null
else
  kube set env deployment/source-control -n "${BOOK_ATESPACE}" DEMO_SHOW_SECRETS- >/dev/null
fi
echo "Restarting the source-control service to clear its pull history and pick up the token..."
kube rollout restart deployment/source-control -n "${BOOK_ATESPACE}" >/dev/null
kube rollout status deployment/source-control -n "${BOOK_ATESPACE}" --timeout=180s >/dev/null
# The gateway caches resolved credentials and minted Actor JWTs by Atespace and
# Actor name, not UID, so a recreated repo-workbench-1 would otherwise receive
# its predecessor's JWT (and old UID) for up to two thirds of its lifetime.
echo "Restarting the egress gateway to drop cached credentials and Actor JWTs..."
kube rollout restart deployment/atenet-egress -n ate-system >/dev/null
kube rollout status deployment/atenet-egress -n ate-system --timeout=180s >/dev/null
kube rollout status "deployment/${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" --timeout=180s >/dev/null
kube rollout status deployment/repo-workbench-webapp -n "${BOOK_ATESPACE}" --timeout=180s >/dev/null
# The Service can route to the terminating Pod for a few seconds after the rollout.
deadline=$((SECONDS + 60))
while :; do
  pulls="$(kube exec -n "${BOOK_ATESPACE}" deployment/repo-workbench-webapp -- python3 -c '
import json
from urllib.request import urlopen
print(len(json.load(urlopen("http://127.0.0.1/api/source-control/pulls", timeout=30))["pulls"]))')"
  [[ "${pulls}" == 0 ]] && break
  ((SECONDS < deadline)) || fail "source-control still reports ${pulls} pulls after restart"
  sleep 2
done

workers="$(ate get workers -n "${BOOK_ATESPACE}" -o json | jq -r '[.workers[]? | select(.status.state == "WORKER_STATE_ACTIVE")] | length')"
[[ "${workers}" == 1 ]] || fail "expected one active Worker in ${BOOK_ATESPACE}, found ${workers}"

cat <<EOF

Credential demo ready on ${KUBECTL_CONTEXT} (${SUBSTRATE_VERSION}).
  Actors: none            Worker: 1 active       source-control: fresh, 0 pulls
  Account token fingerprint: ${fingerprint}

Next, in this shell:
  KIND_CLUSTER_NAME=${KIND_CLUSTER_NAME} source ./scripts/demo-env.sh
then continue from section 2 of docs/credential-brokering-walkthrough.md.
EOF
