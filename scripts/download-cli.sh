#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--linux-all" ) ]]; then
  echo "usage: $0 [--linux-all]" >&2; exit 2
fi

require_command curl
require_command jq
require_command shasum
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "${os}" in darwin|linux) ;; *) echo "error: use Linux (WSL2 on Windows) or macOS" >&2; exit 1 ;; esac
arch="$(host_arch)"
mkdir -p "${BOOK_EXAMPLES_ROOT}/.bin"

download() {
  local asset="$1" destination="$2" expected actual temporary
  expected="$(jq -er --arg asset "${asset}" '.[$asset]' "${BOOK_EXAMPLES_ROOT}/scripts/cli-checksums.json")"
  if [[ -f "${destination}" ]]; then
    actual="$(shasum -a 256 "${destination}" | awk '{print $1}')"
    if [[ "${actual}" == "${expected}" ]]; then chmod +x "${destination}"; return; fi
  fi
  temporary="$(mktemp "${destination}.XXXXXX")"
  if ! curl --fail --location --silent --show-error --retry 3     "https://github.com/kagent-dev/substrate/releases/download/${SUBSTRATE_VERSION}/${asset}" -o "${temporary}"; then
    rm -f "${temporary}"; return 1
  fi
  actual="$(shasum -a 256 "${temporary}" | awk '{print $1}')"
  if [[ "${actual}" != "${expected}" ]]; then
    rm -f "${temporary}"
    echo "error: SHA-256 mismatch for ${asset}" >&2
    return 1
  fi
  chmod +x "${temporary}"
  mv "${temporary}" "${destination}"
}

download "kubectl-ate-${os}-${arch}" "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate"
if [[ "${1:-}" == "--linux-all" ]]; then
  for linux_arch in amd64 arm64; do
    download "kubectl-ate-linux-${linux_arch}" "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate-linux-${linux_arch}"
  done
elif [[ $# -eq 0 ]]; then
  download "kubectl-ate-linux-${arch}" "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate-linux-${arch}"
else
  echo "usage: $0 [--linux-all]" >&2; exit 2
fi
ensure_cli
"${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate" --version
