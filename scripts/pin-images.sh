#!/usr/bin/env bash
# Helm post-renderer for the checksum-locked chart's image fields.
set -o errexit -o nounset -o pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

jq -er '
  .images | to_entries[] |
  if (.value | test("^[^[:space:]]+@sha256:[0-9a-f]{64}$")) then
    [.key, .value] | @tsv
  else error("invalid locked image digest") end
' "${SUBSTRATE_RUNTIME_LOCK}" >"${work}/images.tsv"
awk '
  NR == FNR { locked[$1] = $2; pinned[$2] = 1; next }
  match($0, /^[[:space:]]*image:[[:space:]]*/) {
    prefix = substr($0, 1, RLENGTH)
    image = substr($0, RLENGTH + 1)
    sub(/[[:space:]]+$/, "", image)
    quote = substr(image, 1, 1)
    if ((quote == "\"" || quote == sprintf("%c", 39)) && substr(image, length(image), 1) == quote)
      image = substr(image, 2, length(image) - 2)
    if (image in locked) image = locked[image]
    else if (!(image in pinned)) {
      print "error: chart image is absent from runtime-lock.json: " image > "/dev/stderr"
      exit 1
    }
    print prefix image
    next
  }
  { print }
' "${work}/images.tsv" - >"${work}/manifests.yaml"
# Emit no partial manifests if any image was rejected.
cat "${work}/manifests.yaml"
