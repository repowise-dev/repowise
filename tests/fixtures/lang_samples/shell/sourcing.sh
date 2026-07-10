#!/usr/bin/env bash
# Source chains: literal relative + the $SCRIPT_DIR / dirname idioms.

SCRIPT_DIR="$(dirname "$0")"

source "$SCRIPT_DIR/lib/util.sh"
. ./helpers.sh
source "${BASH_SOURCE%/*}/lib/log.sh"

main() {
    util_init
    helper_run
}

main "$@"
