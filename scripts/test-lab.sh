#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail
export KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-book-validation-$(date +%Y%m%d%H%M%S)-$$}"
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
keep=false
if [[ $# -eq 1 && "$1" == --keep-cluster ]]; then keep=true
elif [[ $# -gt 0 ]]; then echo "usage: $0 [--keep-cluster]" >&2; exit 2; fi
# Refuse existing clusters rather than recreating a reader's lab.
require_command kind
require_command python3
require_command kubectl
previous_context="$(kubectl config current-context 2>/dev/null || true)"
clusters="$(kind get clusters)"
if awk -v name="${KIND_CLUSTER_NAME}" '$0 == name {found=1} END {exit !found}' <<<"${clusters}"; then
  echo "error: test-lab requires a new KIND_CLUSTER_NAME; ${KIND_CLUSTER_NAME} already exists" >&2
  exit 2
fi
export LAB_RESULTS_DIR="${LAB_RESULTS_DIR:-${BOOK_EXAMPLES_ROOT}/.test-results/${KIND_CLUSTER_NAME}}"
if [[ -e "${LAB_RESULTS_DIR}" ]]; then echo "error: results directory already exists: ${LAB_RESULTS_DIR}" >&2; exit 2; fi
mkdir -p "${LAB_RESULTS_DIR}"
cluster_started=false
cleanup() {
  result=$?
  trap - EXIT
  if [[ "${cluster_started}" == true ]]; then
    "${BOOK_EXAMPLES_ROOT}/scripts/collect-diagnostics.sh" "${LAB_RESULTS_DIR}/diagnostics" || true
    if [[ "${keep}" == true ]]; then
      echo "Test cluster retained: ${KUBECTL_CONTEXT} (exit ${result})"
    else
      cleanup_result=0
      "${BOOK_EXAMPLES_ROOT}/scripts/teardown-example.sh" >"${LAB_RESULTS_DIR}/teardown.log" 2>&1 || cleanup_result=$?
      "${BOOK_EXAMPLES_ROOT}/scripts/delete-kind.sh" >"${LAB_RESULTS_DIR}/delete-kind.log" 2>&1 || cleanup_result=$?
      if [[ "${result}" == 0 && "${cleanup_result}" != 0 ]]; then result=${cleanup_result}; fi
    fi
  fi
  if [[ -n "${previous_context}" ]]; then
    kubectl config use-context "${previous_context}" >/dev/null 2>&1 || true
  fi
  echo "Results: ${LAB_RESULTS_DIR}"
  exit "${result}"
}
trap cleanup EXIT
cd "${BOOK_EXAMPLES_ROOT}"
python3 -m unittest discover -s tests -v 2>&1 | tee "${LAB_RESULTS_DIR}/unit.log"
# Setup may create a cluster before returning an error; the exit trap owns its cleanup.
cluster_started=true
./scripts/setup-kind.sh 2>&1 | tee "${LAB_RESULTS_DIR}/setup.log"
./scripts/verify-example.sh 2>&1 | tee "${LAB_RESULTS_DIR}/demo.log"
./scripts/verify-chapters.sh --allow-worker-replacement 2>&1 | tee "${LAB_RESULTS_DIR}/chapters.log"
echo "PASS: fresh published runtime, companion tests, demo lifecycle, and chapter regressions"
