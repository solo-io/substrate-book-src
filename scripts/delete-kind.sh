#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_command kind
kind delete cluster --name "${KIND_CLUSTER_NAME}"
echo "Kept the shared kind-registry container."
