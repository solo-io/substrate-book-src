#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

require_command docker
require_command go
require_command jq
require_command kubectl
require_substrate_source
ensure_cli

mkdir -p "${BOOK_EXAMPLES_ROOT}/.rendered"

go_arch="$(cd "${SUBSTRATE_SOURCE_DIR}" && go env GOARCH)"
substrate_ldflags="$(cd "${SUBSTRATE_SOURCE_DIR}" && make -s ldflags)"
echo "Building the in-cluster kubectl-ate client for the web application..."
(
  cd "${SUBSTRATE_SOURCE_DIR}"
  CGO_ENABLED=0 GOOS=linux GOARCH="${go_arch}" \
    go build -ldflags "${substrate_ldflags}" -o "${BOOK_EXAMPLES_ROOT}/.bin/kubectl-ate-linux" ./cmd/kubectl-ate
)

echo "Building the Repo Workbench agent image..."
agent_tag="localhost:5001/agent-substrate-book/repo-workbench-agent:dev"
docker build --target agent -t "${agent_tag}" "${BOOK_EXAMPLES_ROOT}"
docker push "${agent_tag}"
agent_image="$(docker inspect --format '{{index .RepoDigests 0}}' "${agent_tag}")"
if [[ -z "${agent_image}" ]]; then
  echo "error: could not resolve the pushed Repo Workbench agent image digest" >&2
  exit 1
fi

echo "Building the Repo Workbench web app image..."
webapp_tag="localhost:5001/agent-substrate-book/repo-workbench-webapp:dev"
docker build --target webapp -t "${webapp_tag}" "${BOOK_EXAMPLES_ROOT}"
docker push "${webapp_tag}"
webapp_image="$(docker inspect --format '{{index .RepoDigests 0}}' "${webapp_tag}")"
if [[ -z "${webapp_image}" ]]; then
  echo "error: could not resolve the pushed Repo Workbench web app image digest" >&2
  exit 1
fi

echo "Building the gVisor Worker image from the same Substrate revision..."
worker_image="$({
  cd "${SUBSTRATE_SOURCE_DIR}"
  KO_DOCKER_REPO=localhost:5001 \
  KO_DEFAULTPLATFORMS="linux/${go_arch}" \
    ./hack/run-tool.sh ko build --ldflags="${substrate_ldflags}" ./cmd/ateom-gvisor
} | tail -n 1)"
if [[ -z "${worker_image}" ]]; then
  echo "error: could not build the gVisor Worker image" >&2
  exit 1
fi

substrate_version="$(kubectl --context "${KUBECTL_CONTEXT}" get nodes -o json | jq -r '[.items[].metadata.labels["ate.dev/substrate-version"] // empty] | unique | if length == 1 then .[0] else empty end')"
if [[ -z "${substrate_version}" ]]; then
  echo "error: cluster nodes do not have one consistent ate.dev/substrate-version label" >&2
  exit 1
fi

platform_manifest="${BOOK_EXAMPLES_ROOT}/.rendered/platform.yaml"
template_manifest="${BOOK_EXAMPLES_ROOT}/.rendered/actor-template.yaml"
sed -e "s|__WORKER_IMAGE__|${worker_image}|g" \
    -e "s|__SUBSTRATE_VERSION__|${substrate_version}|g" \
    -e "s|__WEBAPP_IMAGE__|${webapp_image}|g" \
    "${BOOK_EXAMPLES_ROOT}/manifests/platform.yaml.tmpl" >"${platform_manifest}"
sed -e "s|__AGENT_IMAGE__|${agent_image}|g" \
    "${BOOK_EXAMPLES_ROOT}/manifests/actor-template.yaml.tmpl" >"${template_manifest}"

echo "Creating the WorkerPool and web application..."
kubectl --context "${KUBECTL_CONTEXT}" apply -f "${platform_manifest}"
kubectl --context "${KUBECTL_CONTEXT}" wait --for=create "deployment/${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" --timeout=180s
kubectl --context "${KUBECTL_CONTEXT}" rollout status "deployment/${BOOK_WORKER_POOL}" -n "${BOOK_ATESPACE}" --timeout=300s
kubectl --context "${KUBECTL_CONTEXT}" rollout status deployment/repo-workbench-webapp -n "${BOOK_ATESPACE}" --timeout=180s

if ! ate create atespace "${BOOK_ATESPACE}" >/dev/null 2>&1 && ! ate get atespace "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  echo "error: could not create or find Atespace ${BOOK_ATESPACE}" >&2
  exit 1
fi

if ate get actor-template "${BOOK_TEMPLATE}" -a "${BOOK_ATESPACE}" >/dev/null 2>&1; then
  echo "error: ActorTemplate ${BOOK_ATESPACE}/${BOOK_TEMPLATE} already exists and is immutable" >&2
  echo "Run ./scripts/teardown-example.sh before redeploying a changed workload." >&2
  exit 1
fi

echo "Creating the ActorTemplate and its golden snapshot..."
ate create actor-template -f "${template_manifest}"
deadline=$((SECONDS + 300))
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
