#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

require_command kubectl

echo "Repo Workbench will be available at http://localhost:8080"
echo "Keep this process running while you use the browser. The UI does not poll Actors in the background."
kubectl --context "${KUBECTL_CONTEXT}" port-forward -n "${BOOK_ATESPACE}" service/repo-workbench-webapp 8080:80
