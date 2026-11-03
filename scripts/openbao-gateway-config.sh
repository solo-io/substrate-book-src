#!/usr/bin/env bash
# Add an OpenBao credential provider to the egress gateway's AgentGateway
# config: reads the config on stdin, writes it with the provider
# OPENBAO_PROVIDER_NAME at OPENBAO_PROVIDER_ADDRESS (default: "openbao", the
# default provider) after the bundled "k8s.io" provider in each
# credentialProviders list.
#
# TEMPORARY BRIDGE. The released v1.0.0-rc1 chart renders only the k8s.io
# provider and has no value for another one, so setup-openbao.sh patches the
# rendered ConfigMap with this filter. Any later `helm upgrade` renders the
# ConfigMap again and drops the entry; prep-openbao-demo.sh reapplies it. Once a
# chart release can list external providers, list them in the chart values
# instead and delete this script.
#
# Output equals input when the provider is already present.

set -o errexit -o nounset -o pipefail

NAME="${OPENBAO_PROVIDER_NAME:-openbao}"
ADDRESS="${OPENBAO_PROVIDER_ADDRESS:-openbao-credential-provider.credprovider-openbao.svc:50051}"

config="$(cat; printf x)"
config="${config%x}"

if grep -q "^ *- uriAuthority: ${NAME}\$" <<<"${config}"; then
  printf '%s' "${config}"
  exit 0
fi

# Copy each k8s.io entry, then emit the same entry for the new provider: same
# indentation and the same mTLS client identity and trust bundle, with only
# the URI authority and host changed. The k8s.io entry ends at its trust root.
# The output is buffered and written only on success, so a caller never
# applies a half-transformed config.
printf '%s' "${config}" | awk -v name="${NAME}" -v address="${ADDRESS}" '
  function emit(line) { out = out line "\n" }
  function flush(   i, line) {
    for (i = 1; i <= n; i++) {
      line = block[i]
      sub(/uriAuthority: k8s\.io$/, "uriAuthority: " name, line)
      sub(/host: [^ ]+$/, "host: " address, line)
      emit(line)
    }
    n = 0
    added++
  }
  /^ *- uriAuthority: k8s\.io$/ { inblock = 1; n = 0; seen++ }
  { emit($0) }
  inblock { block[++n] = $0 }
  inblock && /root: / { inblock = 0; flush() }
  END {
    if (seen != 2 || added != 2) {
      printf "expected 2 complete k8s.io credential provider entries, found %d (%d complete)\n", seen, added > "/dev/stderr"
      exit 1
    }
    printf "%s", out
  }
'
