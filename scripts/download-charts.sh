#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
if [[ $# -ne 0 ]]; then echo "usage: $0" >&2; exit 2; fi
require_command helm
require_command shasum
mkdir -p "${SUBSTRATE_CHART_DIR}"
work="$(mktemp -d "${SUBSTRATE_CHART_DIR}/download.XXXXXX")"
trap 'rm -rf "${work}"' EXIT

for chart in substrate-crds substrate-podcert substrate; do
  archive="${chart}-${SUBSTRATE_CHART_VERSION}.tgz"
  destination="${SUBSTRATE_CHART_DIR}/${archive}"
  expected="$(jq -er --arg chart "${chart}" '.charts[$chart].sha256' "${SUBSTRATE_RUNTIME_LOCK}")"
  if [[ ! "${expected}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "error: invalid chart checksum for ${chart}" >&2; exit 1
  fi
  if [[ -f "${destination}" && "$(shasum -a 256 "${destination}" | awk '{print $1}')" == "${expected}" ]]; then
    continue
  fi
  helm pull "${SUBSTRATE_CHART_REPO}/${chart}" --version "${SUBSTRATE_CHART_VERSION}" --destination "${work}"
  actual="$(shasum -a 256 "${work}/${archive}" | awk '{print $1}')"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "error: SHA-256 mismatch for ${archive}; refusing to install a changed chart" >&2
    exit 1
  fi
  mv "${work}/${archive}" "${destination}"
done
echo "Verified Substrate ${SUBSTRATE_VERSION} chart packages at ${SUBSTRATE_CHART_DIR}."
