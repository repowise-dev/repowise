#!/usr/bin/env bash
# Selects the tests a change needs, for the composite action in action.yml.
#
# Sets two step outputs: impacted-tests (runner arguments, or ":all") and
# run-all-tests ("true" or "false"). It fails closed: when the cached index
# cannot be updated, selection cannot run, or its output is not one line, the
# outputs say run everything and a warning says why; the step never fails.
# Inputs arrive as environment variables set by action.yml.

set -u

here=${0//\\//}
source "${here%/*}/fetch-base.sh"

# write TESTS RUN_ALL: both outputs, the arguments in delimiter form so no
# value can be read as another output.
write() {
  local delim="REPOWISE_${RANDOM}${RANDOM}"
  {
    echo "impacted-tests<<$delim"
    echo "$1"
    echo "$delim"
    echo "run-all-tests=$2"
  } >>"$GITHUB_OUTPUT"
}

run_everything() {
  echo "::warning::$1; running every test."
  write ":all" true
  exit 0
}

# A restored cache is behind the change; bring it up to date first.
if [[ -f .repowise/state.json ]]; then
  echo "::group::repowise update --index-only"
  repowise update --index-only
  code=$?
  echo "::endgroup::"
  ((code == 0)) || run_everything "The cached index could not be updated (exit $code)"
fi

args=(impacted-tests)
[[ -n "$BASE" ]] && args+=("$BASE")
args+=(--format args --runner "${RUNNER:-auto}")

echo "::group::repowise impacted-tests"
tests=$(repowise "${args[@]}")
code=$?
echo "::endgroup::"

((code == 0)) || run_everything "Test selection could not run (exit $code)"
[[ "$tests" != *$'\n'* ]] || run_everything "Test selection printed more than one line"
if [[ "$tests" == ":all" ]]; then
  write ":all" true
else
  write "$tests" false
fi
exit 0
