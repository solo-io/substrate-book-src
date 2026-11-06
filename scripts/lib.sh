#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail

BOOK_EXAMPLES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBSTRATE_VERSION="v1.0.0"
SUBSTRATE_COMMIT="563df06f6be751c101539986896c17c55aa22a9d"
SUBSTRATE_CHART_VERSION="${SUBSTRATE_VERSION#v}"
SUBSTRATE_CHART_REPO="oci://ghcr.io/kagent-dev/substrate/helm"
SUBSTRATE_CHART_DIR="${BOOK_EXAMPLES_ROOT}/.cache/charts/${SUBSTRATE_CHART_VERSION}"
SUBSTRATE_RUNTIME_LOCK="${BOOK_EXAMPLES_ROOT}/scripts/runtime-lock.json"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-book-workbench}"
KUBECTL_CONTEXT="kind-${KIND_CLUSTER_NAME}"
BOOK_ATESPACE="book-workbench"
BOOK_WORKER_POOL="generic-worker"
BOOK_TEMPLATE="repo-workbench"
BOOK_ACTOR_PREFIX="repo-workbench"
BOOK_ACTOR="${BOOK_ACTOR_PREFIX}-1"

# The OpenBao credential broker demo (setup-openbao.sh). Its provider has no
# published image yet, so setup builds it from a sibling checkout pinned to one
# commit; override both together for deliberate local development.
OPENBAO_PROVIDER_SOURCE_DIR="${OPENBAO_PROVIDER_SOURCE_DIR:-$(cd "${BOOK_EXAMPLES_ROOT}/.." && pwd)/credprovider-openbao}"
OPENBAO_PROVIDER_COMMIT="${OPENBAO_PROVIDER_COMMIT:-64b76a2cc1e7f4b3f9bba5a3168bbf69923a418f}"
# The default provider logs in to OpenBao as its own ServiceAccount and decides
# itself which Atespace reads what.
OPENBAO_PROVIDER_NAME="openbao"
OPENBAO_PROVIDER_NAMESPACE="credprovider-openbao"
OPENBAO_PROVIDER_ADDRESS="openbao-credential-provider.${OPENBAO_PROVIDER_NAMESPACE}.svc:50051"
# The actor-jwt provider logs in as each Actor with an Actor JWT it mints, which needs the egress_gateway role for its
# identity, so OpenBao decides and audits by Actor.
OPENBAO_ACTOR_PROVIDER_NAME="openbao-actor"
OPENBAO_ACTOR_PROVIDER_NAMESPACE="credprovider-openbao-actor"
OPENBAO_ACTOR_PROVIDER_ADDRESS="openbao-credential-provider.${OPENBAO_ACTOR_PROVIDER_NAMESPACE}.svc:50051"
OPENBAO_ACTOR_PROVIDER_SPIFFE_ID="spiffe://cluster.local/ns/${OPENBAO_ACTOR_PROVIDER_NAMESPACE}/sa/openbao-credential-provider"
# The source-control account token, at the path book-workbench's Actors may
# read, and the URIs an EgressPolicy names it by through each provider.
OPENBAO_SECRET_PATH="secret/atespaces/${BOOK_ATESPACE}/source-control"
OPENBAO_CREDENTIAL_URI="ate-secret://${OPENBAO_PROVIDER_NAME}/kv/${OPENBAO_SECRET_PATH}/token"
OPENBAO_ACTOR_CREDENTIAL_URI="ate-secret://${OPENBAO_ACTOR_PROVIDER_NAME}/kv/${OPENBAO_SECRET_PATH}/token"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: required command not found: $1" >&2
    exit 1
  fi
}

locked_image() {
  jq -er --arg reference "$1" '.images[$reference]' "${SUBSTRATE_RUNTIME_LOCK}"
}

require_command jq
if ! jq -e --arg version "${SUBSTRATE_VERSION}" '.version == $version' "${SUBSTRATE_RUNTIME_LOCK}" >/dev/null; then
  echo "error: runtime-lock.json must match ${SUBSTRATE_VERSION}" >&2
  exit 1
fi
SUBSTRATE_API_IMAGE="$(locked_image "ghcr.io/kagent-dev/substrate/ateapi:${SUBSTRATE_VERSION}")"
SUBSTRATE_WORKER_IMAGE="$(locked_image "ghcr.io/kagent-dev/substrate/ateworker-gvisor:${SUBSTRATE_VERSION}")"
LOCAL_REGISTRY_IMAGE="$(locked_image registry:3)"

configure_host_dns() {
  # Some WSL virtual DNS resolvers reject Go's EDNS0 queries.
  case "$(uname -r)" in
    *[Mm]icrosoft*|*[Ww][Ss][Ll]*)
      export GODEBUG="${GODEBUG:+${GODEBUG},}netedns0=0"
      ;;
  esac
}
configure_host_dns

host_arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo amd64 ;;
    arm64|aarch64) echo arm64 ;;
    *) echo "error: unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
  esac
}

kube() {
  kubectl --context "${KUBECTL_CONTEXT}" "$@"
}

ate() {
  "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" --context "${KUBECTL_CONTEXT}" "$@"
}

# Print the ActorTemplate's golden tag once its snapshot is stored; fail otherwise.
# deploy-example.sh creates it once; every Actor from the template restores from it.
golden_tag_ready() {
  local tag
  tag="$(ate get actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}" -o json 2>/dev/null |
    jq -r '.status.goldenSnapshotStatus.goldenTag.name // empty' || true)"
  [[ -n "${tag}" ]] || return 1
  ate get tags -a "${BOOK_ATESPACE}" -o json 2>/dev/null | jq -e --arg tag "${tag}" '
    [.tags[]? | select(.metadata.name == $tag)
     | .status.snapshot.durableSnapshot.state == "SNAPSHOT_STORAGE_STATE_COMPLETED"] | any' >/dev/null || return 1
  echo "${tag}"
}

configure_api_dns() {
  # Resolve cluster FQDNs before inherited host search domains.
  kube patch deployment ate-api-server -n ate-system --type=strategic \
    -p '{"spec":{"template":{"spec":{"dnsConfig":{"options":[{"name":"ndots","value":"4"}]}}}}}'
  kube rollout status deployment/ate-api-server -n ate-system --timeout=180s
}

ensure_cli() {
  local cli_version
  if [[ ! -x "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" ]]; then
    echo "error: run ./scripts/download-cli.sh or ./scripts/setup-kind.sh first" >&2
    exit 1
  fi
  cli_version="$("${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" --version)"
  if [[ "${cli_version}" != *"${SUBSTRATE_VERSION} "* &&
        "${cli_version}" != *"commit=${SUBSTRATE_COMMIT}"* ]]; then
    echo "error: kubectl-ate must match ${SUBSTRATE_VERSION}; run ./scripts/download-cli.sh" >&2
    exit 1
  fi
}

# bao runs the OpenBao CLI in the demo's development OpenBao, over its loopback
# listener with the development root token. Demo labs only.
bao() {
  kube exec -i -n openbao deployment/openbao -- \
    env BAO_ADDR=http://127.0.0.1:8201 BAO_TOKEN=root bao "$@"
}

ensure_openbao_provider_source() {
  local head
  if [[ ! -d "${OPENBAO_PROVIDER_SOURCE_DIR}/.git" ]]; then
    echo "error: no credprovider-openbao checkout at ${OPENBAO_PROVIDER_SOURCE_DIR}; set OPENBAO_PROVIDER_SOURCE_DIR" >&2
    exit 1
  fi
  head="$(git -C "${OPENBAO_PROVIDER_SOURCE_DIR}" rev-parse HEAD)"
  if [[ "${head}" != "${OPENBAO_PROVIDER_COMMIT}" ]]; then
    echo "error: ${OPENBAO_PROVIDER_SOURCE_DIR} is at ${head}, want ${OPENBAO_PROVIDER_COMMIT}" >&2
    echo "       check out that commit, or override OPENBAO_PROVIDER_SOURCE_DIR and OPENBAO_PROVIDER_COMMIT together" >&2
    exit 1
  fi
}

# ensure_gateway_openbao_provider NAME ADDRESS adds a provider to the egress
# gateway's config if a helm upgrade dropped it, and restarts the gateway to
# load it. Prints "added" or "present". See openbao-gateway-config.sh.
ensure_gateway_openbao_provider() {
  local name="$1" address="$2" current patched
  current="$(kube get configmap atenet-egress-agentgateway-config -n ate-system -o json | jq -r '.data["config.yaml"]')"
  patched="$(printf '%s' "${current}" |
    OPENBAO_PROVIDER_NAME="${name}" OPENBAO_PROVIDER_ADDRESS="${address}" \
      "${BOOK_EXAMPLES_ROOT}/scripts/openbao-gateway-config.sh")"
  if [[ "${patched}" == "${current}" ]]; then
    echo present
    return 0
  fi
  kube patch configmap atenet-egress-agentgateway-config -n ate-system --type merge \
    -p "$(jq -n --arg config "${patched}" '{data: {"config.yaml": $config}}')" >/dev/null
  kube rollout restart deployment/atenet-egress -n ate-system >/dev/null
  kube rollout status deployment/atenet-egress -n ate-system --timeout=180s >/dev/null
  echo added
}
