#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

recreate="false"
if [[ "${1:-}" == "--recreate" ]]; then
  recreate="true"
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--recreate]" >&2
  exit 2
fi

require_command docker
require_command git
require_command go
require_command jq
require_command kubectl
require_substrate_source

if [[ "${recreate}" == "true" ]] || ! kubectl config get-contexts "${KUBECTL_CONTEXT}" --no-headers >/dev/null 2>&1; then
  echo "Creating the ${KIND_CLUSTER_NAME} kind cluster and local registry..."
  (cd "${SUBSTRATE_SOURCE_DIR}" && KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME}" ./hack/create-kind-cluster.sh)
else
  echo "Reusing Kubernetes context ${KUBECTL_CONTEXT}. Pass --recreate for a clean cluster."
fi

echo "Installing Agent Substrate from ${SUBSTRATE_SOURCE_DIR}..."
(cd "${SUBSTRATE_SOURCE_DIR}" && KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME}" KUBECTL_CONTEXT="${KUBECTL_CONTEXT}" ./hack/install-ate-kind.sh --deploy-ate-system --atenet-dataplane=agentgateway)

for dataplane_deployment in atenet-router atenet-egress; do
  agentgateway_container="$(kubectl --context "${KUBECTL_CONTEXT}" get deployment "${dataplane_deployment}" -n ate-system -o jsonpath='{.spec.template.spec.containers[?(@.name=="agentgateway")].name}')"
  if [[ "${agentgateway_container}" != "agentgateway" ]]; then
    echo "error: ${dataplane_deployment} is not using the required AgentGateway data plane" >&2
    exit 1
  fi
done
echo "Verified AgentGateway for Substrate ingress and egress."

mkdir -p "${BOOK_EXAMPLES_ROOT}/.bin"
substrate_ldflags="$(cd "${SUBSTRATE_SOURCE_DIR}" && make -s ldflags)"
(cd "${SUBSTRATE_SOURCE_DIR}" && go build -ldflags "${substrate_ldflags}" -o "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" ./cmd/kubectl-ate)

"${BOOK_EXAMPLES_ROOT}/scripts/deploy-example.sh"

echo
echo "Setup complete. Add the companion CLI to this shell, then open the workbench:"
echo "  export PATH=\"${BOOK_EXAMPLES_ROOT}/.bin:\$PATH\""
echo "  ${BOOK_EXAMPLES_ROOT}/scripts/open-workbench.sh"
