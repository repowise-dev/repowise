#!/usr/bin/env bash
#
# ui-gates — run both design gates (no-raw-hex + contrast) in one command.
#
#   ui-gates [--root DIR] [--allow REGEX]... [--contrast-extend JSON]...
#
#   --root DIR              scan root for the no-raw-hex gate
#                           (default: this package's src/)
#   --allow REGEX           extra no-raw-hex allowlist entry; repeatable
#   --contrast-extend JSON  consumer token/check extension file for the
#                           contrast gate; repeatable
#
# Exits non-zero if either gate fails. Consumers wire this into CI from
# node_modules so both products enforce the same canonical token rules.
set -euo pipefail

# Resolve through the npm .bin symlink so the sibling gate scripts are
# found when invoked as `repowise-ui-gates` from a consumer repo.
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  target="$(readlink "$SELF")"
  case "$target" in
    /*) SELF="$target" ;;
    *) SELF="$(dirname "$SELF")/$target" ;;
  esac
done
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"

hex_args=()
contrast_args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --root|--allow)
      hex_args+=("$1" "$2"); shift 2 ;;
    --contrast-extend)
      contrast_args+=(--extend "$2"); shift 2 ;;
    -h|--help)
      sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2 ;;
  esac
done

echo "== no-raw-hex gate =="
bash "$SCRIPT_DIR/no-raw-hex-check.sh" ${hex_args[@]+"${hex_args[@]}"}
echo
echo "== contrast gate =="
python3 "$SCRIPT_DIR/contrast-check.py" ${contrast_args[@]+"${contrast_args[@]}"}
