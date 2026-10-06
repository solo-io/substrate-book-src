#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
[[ "$(uname -s)" == Linux ]] || { echo "error: CI tool installer requires Linux (including WSL2)" >&2; exit 2; }
for command in curl shasum tar; do require_command "${command}"; done
arch="$(host_arch)"
tools="${BOOK_EXAMPLES_ROOT}/.ci-tools"
mkdir -p "${tools}/bin"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
verify() {
  expected="$(awk '{print $1}' "$2")"
  actual="$(shasum -a 256 "$1" | awk '{print $1}')"
  [[ "${actual}" == "${expected}" ]] || { echo "error: tool checksum mismatch: $1" >&2; exit 1; }
}
curl -fsSL --retry 3 "https://github.com/kubernetes-sigs/kind/releases/download/v0.31.0/kind-linux-${arch}" -o "${work}/kind"
curl -fsSL --retry 3 "https://github.com/kubernetes-sigs/kind/releases/download/v0.31.0/kind-linux-${arch}.sha256sum" -o "${work}/kind.sha256"
verify "${work}/kind" "${work}/kind.sha256"
curl -fsSL --retry 3 "https://dl.k8s.io/release/v1.35.0/bin/linux/${arch}/kubectl" -o "${work}/kubectl"
curl -fsSL --retry 3 "https://dl.k8s.io/release/v1.35.0/bin/linux/${arch}/kubectl.sha256" -o "${work}/kubectl.sha256"
verify "${work}/kubectl" "${work}/kubectl.sha256"
curl -fsSL --retry 3 "https://get.helm.sh/helm-v3.19.0-linux-${arch}.tar.gz" -o "${work}/helm.tar.gz"
curl -fsSL --retry 3 "https://get.helm.sh/helm-v3.19.0-linux-${arch}.tar.gz.sha256sum" -o "${work}/helm.sha256"
verify "${work}/helm.tar.gz" "${work}/helm.sha256"
tar -xzf "${work}/helm.tar.gz" -C "${work}"
for command in kind kubectl; do install -m 755 "${work}/${command}" "${tools}/bin/${command}"; done
install -m 755 "${work}/linux-${arch}/helm" "${tools}/bin/helm"
echo "Tools installed. Add ${tools}/bin to PATH."
