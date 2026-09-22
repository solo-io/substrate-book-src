#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

require_substrate_source
(cd "${SUBSTRATE_SOURCE_DIR}" && KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME}" ./hack/delete-kind-cluster.sh)

