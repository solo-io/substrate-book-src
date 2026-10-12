#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
if [[ $# -ne 0 ]]; then echo "usage: $0" >&2; exit 2; fi
require_command kubectl

manifest="${BOOK_EXAMPLES_ROOT}/manifests/postgres-kind.yaml"
setup_sql="${BOOK_EXAMPLES_ROOT}/manifests/postgres-setup.sql"

kube apply -f "${manifest}"
kube rollout status statefulset/postgres -n ate-system --timeout=5m
kube exec -i -n ate-system postgres-0 -c postgres -- \
  psql --no-psqlrc --set=ON_ERROR_STOP=1 --username postgres --dbname atepg \
  --set=substrate_schema=substrate \
  --set=substrate_owner_role=substrate_owner \
  --set=substrate_owner_user=substrate_owner_user \
  --set=substrate_owner_password= \
  --set=substrate_readwrite_role=substrate_readwrite \
  --set=substrate_readwrite_user=substrate_readwrite_user \
  --set=substrate_readwrite_password= <"${setup_sql}"

read_write_dsn='postgresql://substrate_readwrite_user@postgres.ate-system.svc:5432/atepg?sslmode=verify-full&sslrootcert=/run/servicedns.podcert.ate.dev/trust-bundle.pem&sslcert=/run/postgres.podcert.ate.dev/substrate_readwrite_user.pem&sslkey=/run/postgres.podcert.ate.dev/substrate_readwrite_user.pem'
owner_dsn='postgresql://substrate_owner_user@postgres.ate-system.svc:5432/atepg?sslmode=verify-full&sslrootcert=/run/servicedns.podcert.ate.dev/trust-bundle.pem&sslcert=/run/postgres.podcert.ate.dev/substrate_owner_user.pem&sslkey=/run/postgres.podcert.ate.dev/substrate_owner_user.pem'

kube create secret generic substrate-postgres-readwrite -n ate-system \
  --from-literal=readWriteConnectionString="${read_write_dsn}" --dry-run=client -o yaml | kube apply -f -
kube create secret generic substrate-postgres-owner -n ate-system \
  --from-literal=ownerConnectionString="${owner_dsn}" --dry-run=client -o yaml | kube apply -f -

echo "PostgreSQL is ready with the Substrate roles and connection Secrets."
