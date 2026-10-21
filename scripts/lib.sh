#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail

BOOK_EXAMPLES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBSTRATE_VERSION="v0.6.0-alpha3"
SUBSTRATE_COMMIT="e2fc0fa9f77ab5fc09eb01b43ccd74bd84c67c71"
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
