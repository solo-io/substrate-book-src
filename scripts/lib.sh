#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail

BOOK_EXAMPLES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBSTRATE_SOURCE_DIR="${SUBSTRATE_SOURCE_DIR:-${BOOK_EXAMPLES_ROOT}/../substrate}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-substrate}"
KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
BOOK_ATESPACE="book-workbench"
BOOK_WORKER_POOL="generic-worker"
BOOK_TEMPLATE="repo-workbench"
BOOK_ACTOR_PREFIX="repo-workbench"
BOOK_ACTOR="${BOOK_ACTOR_PREFIX}-1"

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "error: required command not found: ${command_name}" >&2
    exit 1
  fi
}

require_substrate_source() {
  if [[ ! -f "${SUBSTRATE_SOURCE_DIR}/go.mod" || ! -x "${SUBSTRATE_SOURCE_DIR}/hack/install-ate-kind.sh" ]]; then
    echo "error: Agent Substrate source not found at ${SUBSTRATE_SOURCE_DIR}" >&2
    echo "For this pre-release development setup, place this repository beside ./substrate or set SUBSTRATE_SOURCE_DIR." >&2
    exit 1
  fi
}

ate() {
  "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" --context "${KUBECTL_CONTEXT}" "$@"
}

ensure_cli() {
  if [[ ! -x "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" ]]; then
    echo "error: ${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate does not exist; run ./scripts/setup-kind.sh first" >&2
    exit 1
  fi
}
