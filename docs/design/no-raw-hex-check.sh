#!/usr/bin/env bash
#
# no-raw-hex gate (October Sunset) — fails if a raw hex color literal appears
# in packages/ui/src OUTSIDE the allowlist below. Wire into CI.
#
# The allowlist has two kinds of entries:
#   * BRAND/STRUCTURAL — hex that must stay literal because it's an identity
#     color or is consumed where a CSS var() cannot resolve (e.g. <canvas>).
#   * DEFERRED-VIZ — viz subsystems whose theme-aware tokenization needs
#     runtime CSS-var resolution + visual QA; tracked as follow-up. Listed
#     explicitly so the gate stays honest (it is NOT silently ignoring them).
#
# Everything else — charts, badges, tooltips, treemaps, shared/primitives —
# must route color through the design tokens. New raw hex there fails CI.
#
# Usage:  bash docs/design/no-raw-hex-check.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="packages/ui/src"

# Files/dirs allowed to contain raw hex (regex, matched against the path).
ALLOW='(__tests__|\.test\.)'                              # test fixtures
ALLOW+='|lib/confidence\.ts'                              # BRAND: LANGUAGE/EDGE color hex (canvas)
ALLOW+='|costs/provider-comparison\.tsx'                  # BRAND: provider identity colors
ALLOW+='|workspace/workspace-graph-node\.tsx'            # dynamic langColor hex-alpha concat
# DEFERRED-VIZ — canvas / self-contained subsystems pending dual-theme pass:
ALLOW+='|/c4/'                                            # C4 architecture diagram subsystem
ALLOW+='|/graph/sigma/'                                   # Sigma canvas renderer
ALLOW+='|graph-primitives/tone-styles\.ts'               # BRAND: categorical node-tone palette (like lang colors)
ALLOW+='|decisions/decision-graph-view\.tsx'             # decision graph viz
ALLOW+='|wiki/git-history-panel\.tsx'                     # graph viz
ALLOW+='|graph/(graph-flow|path-finder-panel|workspace-graph)\.tsx' # remaining graph canvas glue

violations=$(grep -rnE '#[0-9a-fA-F]{3,8}\b' "$ROOT" 2>/dev/null \
  | grep -vE "$ALLOW" || true)

if [ -n "$violations" ]; then
  echo "no-raw-hex gate FAILED — raw hex outside the token system:"
  echo "$violations"
  echo
  echo "Route color through a design token (var(--color-*)), or add a"
  echo "documented allowlist entry if it is a brand/canvas constant."
  exit 1
fi

echo "no-raw-hex gate passed — cleaned surfaces route color through tokens."
echo "(Deferred viz subsystems are allowlisted; see this script's header.)"
