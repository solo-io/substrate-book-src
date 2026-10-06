#!/usr/bin/env bash
# Compatibility entry point: the lab now downloads a released CLI, not source.
set -o errexit -o nounset -o pipefail
exec "$(cd "$(dirname "$0")" && pwd)/download-cli.sh" "$@"
