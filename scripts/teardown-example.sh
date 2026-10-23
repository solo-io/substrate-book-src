#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

require_command kubectl
require_command jq
ensure_cli

actors_json="$(ate get actors -a "${BOOK_ATESPACE}" -o json 2>/dev/null || true)"
[[ -z "${actors_json}" ]] && actors_json='{}'
while IFS= read -r actor_name; do
  [[ -z "${actor_name}" ]] && continue
  ate delete actor "${actor_name}" -a "${BOOK_ATESPACE}"
done < <(jq -r '.actors[]?.metadata.name' <<<"${actors_json}")
if ate get actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  ate delete actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}"
fi
if ate get atespace "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  ate delete atespace "${BOOK_ATESPACE}"
fi
if kube get namespace "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  kube delete workerpool "${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" --ignore-not-found --timeout=180s
  kube delete deployment "${BOOK_WORKER_POOL}" repo-workbench-webapp source-control -n "${BOOK_ATESPACE}" --ignore-not-found --timeout=180s
  kube wait --for=delete pod --all -n "${BOOK_ATESPACE}" --timeout=180s
  # Kubernetes 1.35.0 can leave certificate requests with invalid Pod owner references.
  kube delete podcertificaterequests --all -n "${BOOK_ATESPACE}" --ignore-not-found --timeout=60s
  kube delete namespace "${BOOK_ATESPACE}" --ignore-not-found --timeout=180s
fi
kube delete namespace workbench-secrets --ignore-not-found --timeout=180s
kubectl --context "${KUBECTL_CONTEXT}" delete clusterrole,clusterrolebinding repo-workbench-webapp --ignore-not-found
kubectl --context "${KUBECTL_CONTEXT}" delete role,rolebinding repo-workbench-webapp-token -n ate-system --ignore-not-found
