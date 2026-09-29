#!/usr/bin/env bash
# Runs the Repowise CI gates for the composite action in action.yml.
#
# Every selected gate runs even when an earlier one fails, so one job reports
# all of them. Exit codes follow the gates: 1 when any gate failed, 2 when any
# could not evaluate (and none failed), 0 otherwise. Inputs arrive as
# environment variables set by action.yml; nothing is interpolated into code.

set -u

# Commas, spaces and newlines all separate names, so a YAML block list works.
names=$(tr -s ', \t\r\n' ' ' <<<"$CHECKS")
checks=" $names "
wants() { [[ "$checks" == *" $1 "* ]]; }

if [[ -z "${names// /}" ]]; then
  echo "::error::No check selected; set checks to coverage, doc-drift or security."
  exit 2
fi
for name in $names; do
  case "$name" in
    coverage | doc-drift | security) ;;
    *) echo "::error::Unknown check '$name'; use coverage, doc-drift or security." ; exit 2 ;;
  esac
done

# Coverage and security diff the change, so they need its history and the
# target branch. A shallow clone has neither; fetch them rather than exit 2.
if wants coverage || wants security; then
  if [[ "$(git rev-parse --is-shallow-repository)" == "true" ]]; then
    git fetch --quiet --no-tags --unshallow origin || true
  fi
  if [[ -n "${GITHUB_BASE_REF:-}" ]] && ! git rev-parse --verify --quiet "refs/remotes/origin/$GITHUB_BASE_REF" >/dev/null; then
    git fetch --quiet --no-tags origin "+refs/heads/$GITHUB_BASE_REF:refs/remotes/origin/$GITHUB_BASE_REF" || true
  fi
fi

failed=0
broken=0

# run NAME COMMAND...: one gate in a log group, its exit code recorded.
run() {
  local name=$1
  shift
  echo "::group::repowise $name"
  "$@"
  local code=$?
  echo "::endgroup::"
  echo "$name=$code" >>"$GITHUB_OUTPUT"
  case "$code" in
    0) ;;
    1) failed=1 ;;
    *) broken=1 ;;
  esac
}

base=()
[[ -n "$BASE" ]] && base=("$BASE")

if wants coverage; then
  args=(coverage check ${base[@]+"${base[@]}"} --format github)
  while IFS= read -r report; do
    [[ -n "$report" ]] && args+=(--report "$report")
  done <<<"$COVERAGE_REPORT"
  [[ -n "$COVERAGE_FAIL_UNDER" ]] && args+=(--fail-under "$COVERAGE_FAIL_UNDER")
  run coverage repowise "${args[@]}"
fi

drift=(doc-drift --check)
[[ -n "$DOC_DRIFT_BASELINE" ]] && drift+=(--baseline "$DOC_DRIFT_BASELINE")
security=(security check ${base[@]+"${base[@]}"} --fail-on "$SECURITY_FAIL_ON")
[[ -n "$SECURITY_BASELINE" ]] && security+=(--baseline "$SECURITY_BASELINE")

wants doc-drift && run doc-drift repowise "${drift[@]}" --format github
wants security && run security repowise "${security[@]}" --format github

# SARIF is a second run in its own format. A gate that could not evaluate
# writes an empty file, which code scanning would reject, so it is dropped.
if [[ "$SARIF" == "true" ]] && { wants doc-drift || wants security; }; then
  mkdir -p "$SARIF_DIR"
  wants doc-drift && repowise "${drift[@]}" --format sarif >"$SARIF_DIR/doc-drift.sarif"
  wants security && repowise "${security[@]}" --format sarif >"$SARIF_DIR/security.sarif"
  find "$SARIF_DIR" -name '*.sarif' -empty -delete
  if [[ -n "$(ls -A "$SARIF_DIR")" ]]; then
    echo "sarif-dir=$SARIF_DIR" >>"$GITHUB_OUTPUT"
  fi
fi

if ((failed)); then
  exit 1
fi
if ((broken)); then
  exit 2
fi
exit 0
