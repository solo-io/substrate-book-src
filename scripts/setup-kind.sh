#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

recreate=false
if [[ "${1:-}" == "--recreate" ]]; then recreate=true
elif [[ $# -gt 0 ]]; then echo "usage: $0 [--recreate]" >&2; exit 2; fi
for command in docker kind helm kubectl curl jq openssl shasum; do require_command "${command}"; done
docker info >/dev/null
"${BOOK_EXAMPLES_ROOT}/scripts/download-cli.sh"
"${BOOK_EXAMPLES_ROOT}/scripts/download-charts.sh"

if [[ "${recreate}" == true ]]; then
  kind delete cluster --name "${KIND_CLUSTER_NAME}"
fi
if ! kind get clusters 2>/dev/null | awk -v name="${KIND_CLUSTER_NAME}" '$0 == name {found=1} END {exit !found}'; then
  kind create cluster --name "${KIND_CLUSTER_NAME}" --image "${KIND_NODE_IMAGE}"     --config "${BOOK_EXAMPLES_ROOT}/manifests/kind.yaml" --wait 120s
else
  kind export kubeconfig --name "${KIND_CLUSTER_NAME}"
  kube get --raw=/readyz >/dev/null
  installed_image="$(kube get deployment/ate-api-server -n ate-system -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
  if [[ -n "${installed_image}" && "${installed_image}" != "${SUBSTRATE_API_IMAGE}" &&
        "${installed_image}" != "ghcr.io/kagent-dev/substrate/ateapi:${SUBSTRATE_VERSION}" ]]; then
    echo "error: ${KUBECTL_CONTEXT} uses another Substrate release; choose a new KIND_CLUSTER_NAME or use --recreate" >&2
    exit 1
  fi
fi

# Only the demo development images use this registry. Keep it shared across labs.
if ! docker inspect kind-registry >/dev/null 2>&1; then
  docker run -d --restart=always --name kind-registry -p 127.0.0.1:5001:5000 "${LOCAL_REGISTRY_IMAGE}"
elif [[ "$(docker inspect -f '{{.State.Running}}' kind-registry)" != true ]]; then
  docker start kind-registry >/dev/null
fi
docker network connect kind kind-registry 2>/dev/null || true
for node in $(kind get nodes --name "${KIND_CLUSTER_NAME}"); do
  docker exec "${node}" sysctl net.ipv4.conf.all.proxy_arp=1 >/dev/null
  docker exec "${node}" mkdir -p /etc/containerd/certs.d/localhost:5001
  printf '[host."http://kind-registry:5000"]\n' | docker exec -i "${node}" cp /dev/stdin /etc/containerd/certs.d/localhost:5001/hosts.toml
done

for namespace in ate-system podcertificate-controller-system; do
  kube create namespace "${namespace}" --dry-run=client -o yaml | kube apply -f -
done
# The platform chart owns this namespace, even though bootstrap needs it first.
kube label namespace podcertificate-controller-system app.kubernetes.io/managed-by=Helm --overwrite >/dev/null
kube annotate namespace podcertificate-controller-system meta.helm.sh/release-name=substrate meta.helm.sh/release-namespace=ate-system --overwrite >/dev/null
helm upgrade --install substrate-crds "${SUBSTRATE_CHART_DIR}/substrate-crds-${SUBSTRATE_CHART_VERSION}.tgz" \
  --kube-context "${KUBECTL_CONTEXT}" --namespace ate-system --wait --timeout 5m

# These pools are generated locally; no private keys are shipped in the charts.
for name in service-dns-ca-pool pod-identity-ca-pool; do
  if ! kube get secret "${name}" -n podcertificate-controller-system >/dev/null 2>&1; then
    ate admin make-ca-pool --ca-id=1 --name="${name}" --secret-namespace=podcertificate-controller-system
  fi
done
if ! kube get secret actor-id-jwt-pool -n ate-system >/dev/null 2>&1; then
  ate admin make-jwt-pool --key-id=1 --name=actor-id-jwt-pool --secret-namespace=ate-system
fi
for name in actor-id-ca-pool egress-mitm-ca-pool; do
  if ! kube get secret "${name}" -n ate-system >/dev/null 2>&1; then
    ate admin make-ca-pool --ca-id=1 --name="${name}" --secret-namespace=ate-system --key-type=ECDSAP256
  fi
done
actor_ca="$(kube get secret actor-id-ca-pool -n ate-system -o json | jq -r '.data.pool' | openssl base64 -d -A | jq -r '.CAs[0].RootCertificateDER' | openssl base64 -d -A | openssl x509 -inform DER -outform PEM)"
kube create secret generic actor-id-ca-certs -n ate-system --from-literal=ca.crt="${actor_ca}" --dry-run=client -o yaml | kube apply -f -
issuer="$(kube get --raw /.well-known/openid-configuration | jq -r '.issuer')"
kube create configmap ate-api-authentication -n ate-system   --from-literal=authentication.yaml="$(cat <<EOF
actorIdentityJWTProvider: kubernetes
jwtProviders:
- name: kubernetes
  issuer: ${issuer}
  audiences: [api.ate-system.svc]
  certificateAuthorityFile: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
  discoveryTokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token
EOF
)" --dry-run=client -o yaml | kube apply -f -

helm upgrade --install substrate "${SUBSTRATE_CHART_DIR}/substrate-${SUBSTRATE_CHART_VERSION}.tgz" \
  --kube-context "${KUBECTL_CONTEXT}" --namespace ate-system \
  -f "${BOOK_EXAMPLES_ROOT}/manifests/substrate-values.yaml" \
  --post-renderer "${BOOK_EXAMPLES_ROOT}/scripts/pin-images.sh" --wait --timeout 20m
configure_api_dns

for deployment in atenet-router atenet-egress; do
  if [[ "$(kube get deploy "${deployment}" -n ate-system -o jsonpath='{.spec.template.spec.containers[?(@.name=="agentgateway")].name}')" != agentgateway ]]; then
    echo "error: ${deployment} must use AgentGateway" >&2; exit 1
  fi
done
"${BOOK_EXAMPLES_ROOT}/scripts/deploy-example.sh"
printf '\nSetup complete (%s). In this shell:\n  export PATH="%s/.bin:$PATH"\n  kubectl config use-context %s\n  ./scripts/open-workbench.sh\n' "${SUBSTRATE_VERSION}" "${BOOK_EXAMPLES_ROOT}" "${KUBECTL_CONTEXT}"
