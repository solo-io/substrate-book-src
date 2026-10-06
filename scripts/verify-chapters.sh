#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_command kubectl
require_command python3
ensure_cli
export KUBECTL_CONTEXT SUBSTRATE_VERSION
python3 "${BOOK_EXAMPLES_ROOT}/tests/verify_chapters.py" "$@"
