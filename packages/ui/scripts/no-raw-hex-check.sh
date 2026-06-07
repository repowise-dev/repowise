#!/usr/bin/env bash
#
# no-raw-hex gate (design tokens) — fails if a raw hex color literal appears
# in the scan root OUTSIDE the allowlist below. Wire into CI.
#
# This gate ships with the ui package so every consumer enforces the SAME
# token discipline over its own source tree. Consumers extend (never fork)
# via flags:
#
#   no-raw-hex-check.sh [--root DIR] [--allow REGEX]...
#
#   --root DIR     directory to scan (default: this package's src/)
#   --allow REGEX  extra allowlist entry, matched against the file path;
#                  repeatable. Document every entry at the call site.
#
# The base allowlist has two kinds of entries:
#   * BRAND/STRUCTURAL — hex that must stay literal because it's an identity
#     color or is consumed where a CSS var() cannot resolve (e.g. <canvas>).
#   * DEFERRED-VIZ — viz subsystems whose theme-aware tokenization needs
#     runtime CSS-var resolution + visual QA; tracked as follow-up. Listed
#     explicitly so the gate stays honest (it is NOT silently ignoring them).
#
# Everything else — charts, badges, tooltips, treemaps, shared/primitives —
# must route color through the design tokens. New raw hex there fails CI.
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$PKG_DIR/src"

# Files/dirs allowed to contain raw hex (regex, matched against the path).
ALLOW='(__tests__|\.test\.)'                              # test fixtures
ALLOW+='|src/brand\.ts'                                   # BRAND: canonical constants for var()-less surfaces (OG/email/badge)
ALLOW+='|lib/confidence\.ts'                              # BRAND: LANGUAGE/EDGE color hex (canvas)
ALLOW+='|costs/provider-comparison\.tsx'                  # BRAND: provider identity colors
ALLOW+='|workspace/workspace-graph-node\.tsx'            # dynamic langColor hex-alpha concat
ALLOW+='|graph-primitives/tone-styles\.ts'               # BRAND: categorical node-tone palette (like lang colors)
ALLOW+='|wiki/git-history-panel\.tsx'                     # BRAND: per-author categorical bar colors
# DEFERRED-VIZ — canvas subsystems with their own runtime light/dark palette
# (driven by the global theme), pending an aesthetic warm-palette retune:
ALLOW+='|c4/export/svg-exporter\.ts'                      # SVG serializer resolves LIVE theme tokens; hex are headless-export fallbacks only (kg-ux B7)
ALLOW+='|/graph/sigma/'                                   # Sigma canvas renderer (internal light/dark palette)

while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      ROOT="$2"; shift 2 ;;
    --allow)
      ALLOW+="|$2"; shift 2 ;;
    -h|--help)
      sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2 ;;
  esac
done

if [ ! -d "$ROOT" ]; then
  echo "no-raw-hex gate: scan root not found: $ROOT" >&2
  exit 2
fi

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
