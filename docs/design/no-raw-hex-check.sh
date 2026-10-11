#!/usr/bin/env bash
#
# Wrapper — the canonical no-raw-hex gate ships with the ui package
# (packages/ui/scripts/no-raw-hex-check.sh) so downstream consumers run the
# same check from node_modules. This wrapper keeps the historical invocation
# (`bash docs/design/no-raw-hex-check.sh`) working; defaults scan
# packages/ui/src exactly as before.
set -euo pipefail

exec bash "$(dirname "${BASH_SOURCE[0]}")/../../packages/ui/scripts/no-raw-hex-check.sh" "$@"
