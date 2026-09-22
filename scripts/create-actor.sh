#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

ensure_cli

require_command jq

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [actor-name]" >&2
  exit 2
fi

if [[ $# -eq 1 ]]; then
  actor_name="$1"
else
  actors_json="$(ate get actors -a "${BOOK_ATESPACE}" -o json)"
  next_ordinal="$({
    jq -r '.actors[].metadata.name' <<<"${actors_json}" \
      | sed -n "s/^${BOOK_ACTOR_PREFIX}-\\([0-9][0-9]*\\)$/\\1/p"
    echo 0
  } | sort -n | tail -n 1)"
  actor_name="${BOOK_ACTOR_PREFIX}-$((next_ordinal + 1))"
fi

if ate get actor "${actor_name}" -a "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  echo "Actor ${BOOK_ATESPACE}/${actor_name} already exists."
else
  ate create actor "${actor_name}" -a "${BOOK_ATESPACE}" --template "${BOOK_TEMPLATE}"
fi

ate get actor "${actor_name}" -a "${BOOK_ATESPACE}"
echo
echo "Open the browser path in another terminal with:"
echo "  ./scripts/open-workbench.sh"
