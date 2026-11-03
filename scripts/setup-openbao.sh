#!/usr/bin/env bash
# Install the OpenBao credential broker demo into a running book lab.
#
# It installs two providers that read the same OpenBao:
#
#   - "openbao" logs in to OpenBao as its own ServiceAccount (OpenBao's
#     Kubernetes auth method) and limits each Actor to the secrets under its
#     own Atespace's path. It needs nothing from Substrate beyond a place in
#     the egress gateway's provider list.
#   - "openbao-actor" logs in to OpenBao as each Actor with an Actor JWT it
#     mints through ate-api-server, so OpenBao's policy decides and its audit
#     log names the Actor. Minting needs the egress_gateway role.
#
# Setup:
#
#   1. upgrades the substrate release with
#      manifests/substrate-values-openbao-actor.yaml, which grants
#      openbao-actor the egress_gateway role;
#   2. deploys a development OpenBao (in-memory, TLS from a servicedns pod
#      certificate) configured for both providers;
#   3. builds the provider from the pinned credprovider-openbao checkout into
#      the lab's local registry and deploys it twice, once per mode;
#   4. adds both providers to the egress gateway's config (a temporary bridge
#      until the chart can list them; see scripts/openbao-gateway-config.sh).
#
# It assumes setup-kind.sh already installed the lab, and is safe to rerun.
# prep-openbao-demo.sh then resets the lab for the walkthrough or live demo.
#
# Usage: KIND_CLUSTER_NAME=<lab> ./scripts/setup-openbao.sh

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

for command in kubectl helm jq git go docker; do require_command "${command}"; done
if [[ $# -gt 0 ]]; then echo "usage: KIND_CLUSTER_NAME=<lab> $0" >&2; exit 2; fi
fail() { echo "error: $*" >&2; exit 1; }

ensure_cli
ensure_openbao_provider_source
kube get --raw=/readyz >/dev/null 2>&1 || fail "${KUBECTL_CONTEXT} is not reachable; set KIND_CLUSTER_NAME to the lab cluster"
installed_image="$(kube get deployment/ate-api-server -n ate-system -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
if [[ "${installed_image}" != "${SUBSTRATE_API_IMAGE}" &&
      "${installed_image}" != "ghcr.io/kagent-dev/substrate/ateapi:${SUBSTRATE_VERSION}" ]]; then
  fail "${KUBECTL_CONTEXT} does not run the locked ${SUBSTRATE_VERSION} API server (found '${installed_image:-none}'); run ./scripts/setup-kind.sh first"
fi
docker inspect kind-registry >/dev/null 2>&1 || fail "the kind-registry container is missing; run ./scripts/setup-kind.sh"

[[ -f "${SUBSTRATE_CHART_DIR}/substrate-${SUBSTRATE_CHART_VERSION}.tgz" ]] ||
  fail "no cached ${SUBSTRATE_CHART_VERSION} chart; run ./scripts/download-charts.sh or ./scripts/setup-kind.sh"
echo "== Granting ${OPENBAO_ACTOR_PROVIDER_NAME} the egress_gateway role (helm upgrade)"
helm upgrade substrate "${SUBSTRATE_CHART_DIR}/substrate-${SUBSTRATE_CHART_VERSION}.tgz" \
  --kube-context "${KUBECTL_CONTEXT}" --namespace ate-system \
  -f "${BOOK_EXAMPLES_ROOT}/manifests/substrate-values.yaml" \
  -f "${BOOK_EXAMPLES_ROOT}/manifests/substrate-values-openbao-actor.yaml" \
  --post-renderer "${BOOK_EXAMPLES_ROOT}/scripts/pin-images.sh" --wait --timeout 20m >/dev/null
configure_api_dns >/dev/null
kube get deployment/ate-api-server -n ate-system -o json |
  jq -e --arg id "--egress-gateway-spiffe-id=${OPENBAO_ACTOR_PROVIDER_SPIFFE_ID}" \
    '.spec.template.spec.containers[] | select(.name == "ate-api-server") | .args | index($id)' >/dev/null ||
  fail "ate-api-server does not grant ${OPENBAO_ACTOR_PROVIDER_SPIFFE_ID} the egress_gateway role"

echo "== Deploying and configuring the development OpenBao"
KUBECTL_CONTEXT="${KUBECTL_CONTEXT}" "${OPENBAO_PROVIDER_SOURCE_DIR}/hack/kind/setup-openbao.sh" >/dev/null

# deploy_provider KUSTOMIZE_DIR NAMESPACE builds the provider with ko, for this
# machine's node platform, into the registry the lab's nodes pull localhost:5001
# from, and deploys it.
deploy_provider() {
  (
    cd "${OPENBAO_PROVIDER_SOURCE_DIR}"
    kube kustomize "$1" |
      KO_DOCKER_REPO=localhost:5001 KO_DEFAULTPLATFORMS="linux/$(host_arch)" GO111MODULE=on GOFLAGS= \
        go run github.com/google/ko@v0.19.1 resolve --base-import-paths -f - 2>/dev/null |
      kube apply -f - >/dev/null
  )
  kube rollout status deployment/openbao-credential-provider -n "$2" --timeout=180s >/dev/null
}

echo "== Building and deploying the OpenBao credential providers (${OPENBAO_PROVIDER_COMMIT:0:12})"
deploy_provider config "${OPENBAO_PROVIDER_NAMESPACE}"
deploy_provider config/actor-jwt "${OPENBAO_ACTOR_PROVIDER_NAMESPACE}"

echo "== Adding the providers to the egress gateway's config"
echo "   ${OPENBAO_PROVIDER_NAME}: $(ensure_gateway_openbao_provider "${OPENBAO_PROVIDER_NAME}" "${OPENBAO_PROVIDER_ADDRESS}")"
echo "   ${OPENBAO_ACTOR_PROVIDER_NAME}: $(ensure_gateway_openbao_provider "${OPENBAO_ACTOR_PROVIDER_NAME}" "${OPENBAO_ACTOR_PROVIDER_ADDRESS}")"

cat <<EOF

OpenBao credential broker installed on ${KUBECTL_CONTEXT}.
  OpenBao:        openbao/openbao (development mode: state is in memory)
  openbao:        ${OPENBAO_PROVIDER_ADDRESS}
                  ${OPENBAO_CREDENTIAL_URI}
  openbao-actor:  ${OPENBAO_ACTOR_PROVIDER_ADDRESS}
                  ${OPENBAO_ACTOR_CREDENTIAL_URI}

Next: KIND_CLUSTER_NAME=${KIND_CLUSTER_NAME} ./scripts/prep-openbao-demo.sh
then docs/openbao-credential-walkthrough.md, or ./scripts/live-demo.sh --broker openbao (or openbao-actor).
Remove it with ./scripts/teardown-openbao.sh.
EOF
