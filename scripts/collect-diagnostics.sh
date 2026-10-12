#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
if [[ $# != 1 ]]; then echo "usage: $0 <output-directory>" >&2; exit 2; fi
mkdir -p "$1"
kube get pods -A -o wide >"$1/pods.txt" 2>&1 || true
kube get events -A --sort-by=.metadata.creationTimestamp >"$1/events.txt" 2>&1 || true
for deployment in ate-api-server ate-controller atenet-router atenet-egress; do
  kube logs -n ate-system "deployment/${deployment}" --all-containers --tail=200 >"$1/${deployment}.log" 2>&1 || true
done
kube logs -n ate-system daemonset/atelet \
  --all-containers --tail=200 >"$1/atelet.log" 2>&1 || true
kube logs -n podcertificate-controller-system deployment/podcertificate-controller \
  --all-containers --tail=200 >"$1/podcertificate-controller.log" 2>&1 || true
kube logs -n ate-system statefulset/postgres \
  --all-containers --tail=200 >"$1/postgres.log" 2>&1 || true
kube logs -n "${BOOK_ATESPACE}" deployment/generic-worker \
  --all-containers --tail=200 >"$1/generic-worker.log" 2>&1 || true
kube logs -n "${BOOK_ATESPACE}" deployment/repo-workbench-webapp --tail=200 >"$1/webapp.log" 2>&1 || true
# Do not collect Secrets, kubeconfigs, environment variables, or signing-key pools.
