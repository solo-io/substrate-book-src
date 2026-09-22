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
  ate delete actor "${actor_name}" -a "${BOOK_ATESPACE}" --any-state
done < <(jq -r '.actors[]?.metadata.name' <<<"${actors_json}")
if ate get actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  ate delete actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}"
fi
if ate get atespace "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  ate delete atespace "${BOOK_ATESPACE}"
fi
kubectl --context "${KUBECTL_CONTEXT}" delete namespace "${BOOK_ATESPACE}" --ignore-not-found
