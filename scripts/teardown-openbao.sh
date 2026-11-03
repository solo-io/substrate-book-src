#!/usr/bin/env bash
# Remove the OpenBao credential broker demo from a book lab: upgrade the
# substrate release with substrate-values.yaml alone (which renders the gateway
# config without the OpenBao providers and drops the actor-jwt provider's
# egress_gateway grant), then delete both providers and the development
# OpenBao. Actor egress policies that name ate-secret://openbao/... or
# ate-secret://openbao-actor/... URIs fail closed afterwards.
#
# Usage: KIND_CLUSTER_NAME=<lab> ./scripts/teardown-openbao.sh

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

for command in kubectl helm jq; do require_command "${command}"; done
if [[ $# -gt 0 ]]; then echo "usage: KIND_CLUSTER_NAME=<lab> $0" >&2; exit 2; fi

kube get --raw=/readyz >/dev/null 2>&1 || { echo "error: ${KUBECTL_CONTEXT} is not reachable" >&2; exit 1; }

echo "== Restoring the substrate release to substrate-values.yaml"
helm upgrade substrate "${SUBSTRATE_CHART_DIR}/substrate-${SUBSTRATE_CHART_VERSION}.tgz" \
  --kube-context "${KUBECTL_CONTEXT}" --namespace ate-system \
  -f "${BOOK_EXAMPLES_ROOT}/manifests/substrate-values.yaml" \
  --post-renderer "${BOOK_EXAMPLES_ROOT}/scripts/pin-images.sh" --wait --timeout 20m >/dev/null
configure_api_dns >/dev/null
# The ConfigMap now renders without the provider; restart so the gateway drops it.
kube rollout restart deployment/atenet-egress -n ate-system >/dev/null
kube rollout status deployment/atenet-egress -n ate-system --timeout=180s >/dev/null

echo "== Deleting the providers and the development OpenBao"
kube delete namespace "${OPENBAO_PROVIDER_NAMESPACE}" "${OPENBAO_ACTOR_PROVIDER_NAMESPACE}" openbao --ignore-not-found
kube delete clusterrolebinding openbao-tokenreview --ignore-not-found
echo "OpenBao credential broker removed from ${KUBECTL_CONTEXT}."
