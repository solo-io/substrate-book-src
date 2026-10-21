#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

require_command docker
require_command jq
require_command kubectl
require_command openssl
require_command shasum
ensure_cli
mkdir -p "${BOOK_EXAMPLES_ROOT}/.rendered"

if [[ -n "${BOOK_AGENT_IMAGE:-}" || -n "${BOOK_WEBAPP_IMAGE:-}" || -n "${BOOK_SOURCE_CONTROL_IMAGE:-}" ]]; then
  if [[ -z "${BOOK_AGENT_IMAGE:-}" || -z "${BOOK_WEBAPP_IMAGE:-}" || -z "${BOOK_SOURCE_CONTROL_IMAGE:-}" ]]; then
    echo "error: set BOOK_AGENT_IMAGE, BOOK_WEBAPP_IMAGE, and BOOK_SOURCE_CONTROL_IMAGE together" >&2; exit 1
  fi
  agent_image="${BOOK_AGENT_IMAGE}"
  webapp_image="${BOOK_WEBAPP_IMAGE}"
  source_control_image="${BOOK_SOURCE_CONTROL_IMAGE}"
else
  "${BOOK_EXAMPLES_ROOT}/scripts/download-cli.sh"
  echo "Building only the companion demo images (Substrate uses published images)..."
  for target in agent webapp source-control; do
    tag="localhost:5001/book-src/repo-workbench-${target}:v060-alpha3"
    docker build --target "${target}" -t "${tag}" "${BOOK_EXAMPLES_ROOT}"
    docker push "${tag}"
    digest="$(docker inspect --format '{{index .RepoDigests 0}}' "${tag}")"
    if [[ -z "${digest}" ]]; then echo "error: no digest for ${tag}" >&2; exit 1; fi
    case "${target}" in
      agent) agent_image="${digest}" ;;
      webapp) webapp_image="${digest}" ;;
      source-control) source_control_image="${digest}" ;;
    esac
  done
fi
for image in "${agent_image}" "${webapp_image}" "${source_control_image}"; do
  if [[ "${image}" != *@sha256:* ]]; then echo "error: demo image must be pinned by digest: ${image}" >&2; exit 1; fi
done
worker_image="${SUBSTRATE_WORKER_IMAGE}"
if ate get actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  echo "error: ActorTemplate already exists; run ./scripts/teardown-example.sh before redeploying" >&2; exit 1
fi

platform_manifest="${BOOK_EXAMPLES_ROOT}/.rendered/platform.yaml"
template_manifest="${BOOK_EXAMPLES_ROOT}/.rendered/actor-template.yaml"
sed -e "s|__WORKER_IMAGE__|${worker_image}|g" \
    -e "s|__WEBAPP_IMAGE__|${webapp_image}|g" \
    -e "s|__SOURCE_CONTROL_IMAGE__|${source_control_image}|g" \
    "${BOOK_EXAMPLES_ROOT}/manifests/platform.yaml.tmpl" >"${platform_manifest}"
sed -e "s|__AGENT_IMAGE__|${agent_image}|g" \
    "${BOOK_EXAMPLES_ROOT}/manifests/actor-template.yaml.tmpl" >"${template_manifest}"

echo "Creating the WorkerPool and web application..."
kubectl --context "${KUBECTL_CONTEXT}" apply -f "${platform_manifest}"
kubectl --context "${KUBECTL_CONTEXT}" wait --for=create "deployment/${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" --timeout=180s
kubectl --context "${KUBECTL_CONTEXT}" rollout status "deployment/${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" --timeout=300s
worker_pool="$(kube get workerpool "${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" -o json)"
if ! jq -e '
  any(.status.sandboxClasses[];
      .name == "gvisor" and .configRef.name == "gvisor-default" and
      any(.conditions[]; .type == "Resolved" and .status == "True"))
' <<<"${worker_pool}" >/dev/null; then
  echo "error: WorkerPool gVisor class did not resolve to gvisor-default" >&2
  exit 1
fi
kubectl --context "${KUBECTL_CONTEXT}" rollout status deployment/repo-workbench-webapp -n "${BOOK_ATESPACE}" --timeout=180s

# The source-control account token exists in two places only: the credential
# store the provider reads (workbench-secrets/git-token) and the service that
# checks it (book-workbench/source-control-token). No Actor can read either.
# Reruns keep the existing token so a rotated value is never silently replaced.
account_token="$(kube get secret git-token -n workbench-secrets -o jsonpath='{.data.token}' 2>/dev/null | openssl base64 -d -A || true)"
if [[ -z "${account_token}" ]]; then
  account_token="$(openssl rand -hex 24)"
fi
kube create secret generic git-token -n workbench-secrets --from-literal=token="${account_token}" \
  --dry-run=client -o yaml | kube apply -f - >/dev/null
kube create secret generic source-control-token -n "${BOOK_ATESPACE}" --from-literal=token="${account_token}" \
  --dry-run=client -o yaml | kube apply -f - >/dev/null
echo "Source-control account token fingerprint: $(printf '%s' "${account_token}" | shasum -a 256 | cut -c1-16)"
kubectl --context "${KUBECTL_CONTEXT}" rollout status deployment/source-control -n "${BOOK_ATESPACE}" --timeout=180s

if ! ate create atespace "${BOOK_ATESPACE}" >/dev/null 2>&1 && ! ate get atespace "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  echo "error: could not create or find Atespace ${BOOK_ATESPACE}" >&2
  exit 1
fi

echo "Creating the ActorTemplate and its golden snapshot..."
ate create actor-template -f "${template_manifest}"
# A cold Worker may spend its first five-minute operation downloading gVisor.
# The control plane retries after the asset is cached, so allow that retry to finish.
deadline=$((SECONDS + 600))
while ((SECONDS < deadline)); do
  template_json="$(ate get actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}" -o json 2>/dev/null || true)"
  if [[ -z "${template_json}" ]]; then
    sleep 5
    continue
  fi
  golden_tag="$(jq -r '.status.goldenSnapshotStatus.goldenTag.name // empty' <<<"${template_json}")"
  template_error="$(jq -r '.status.goldenSnapshotStatus.errorMessage // empty' <<<"${template_json}")"
  if [[ -n "${golden_tag}" ]]; then
    echo "ActorTemplate ready with golden tag ${golden_tag}."
    break
  fi
  if [[ -n "${template_error}" ]]; then
    echo "error: ActorTemplate preparation failed: ${template_error}" >&2
    exit 1
  fi
  sleep 5
done
if [[ -z "${golden_tag:-}" ]]; then
  echo "error: timed out waiting for the ActorTemplate golden snapshot" >&2
  exit 1
fi

echo "Repo Workbench infrastructure is ready. Open the UI to create repository-run Actors."
