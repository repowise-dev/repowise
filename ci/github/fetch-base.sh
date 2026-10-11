# Sourced by run-gates.sh and impacted-tests.sh. A diff against the pull
# request's target needs its history and the target branch, and a shallow clone
# has neither; fetch them rather than fail to evaluate.

if [[ "$(git rev-parse --is-shallow-repository)" == "true" ]]; then
  git fetch --quiet --no-tags --unshallow origin || true
fi
if [[ -n "${GITHUB_BASE_REF:-}" ]] && ! git rev-parse --verify --quiet "refs/remotes/origin/$GITHUB_BASE_REF" >/dev/null; then
  git fetch --quiet --no-tags origin "+refs/heads/$GITHUB_BASE_REF:refs/remotes/origin/$GITHUB_BASE_REF" || true
fi
