# Repowise in CI

Three gates judge a pull request, each on what the change adds rather than on
the whole repository's past:

| Gate | Question | Needs | History |
|------|----------|-------|---------|
| `repowise coverage check` | Did the tests run the lines this change touched? | git and a coverage report | the merge-base with the target branch |
| `repowise doc-drift --check` | Does the documentation still describe files, links and commands that exist? | git | none (full history only improves rename suggestions) |
| `repowise security check` | Did this change add a secret or a risky call? | git | every commit of the change |

None of them needs an index, a database, a model or an API key, and none
stores anything. They share one set of exit codes, output formats and base
resolution, described once below. Each layer's own page has the detail:
[test intelligence](../layers/TEST_INTELLIGENCE.md#patch-coverage-in-ci),
[documentation drift](../layers/DOC_DRIFT.md#in-ci),
[security](../layers/SECURITY.md#in-ci-repowise-security-check).

## What every gate does the same way

**Exit codes.** `0` passed, or had nothing to judge. `1` the change failed the
gate. `2` the gate could not evaluate: not a git repository, an unknown
revision, no merge-base in a shallow clone, an unreadable report or baseline.
A setup problem never reads as a pass.

**Formats.** `--format table` (the default) for a terminal, `json` for a
script, `markdown` for a comment or a job artifact, and `github` for GitHub
Actions: up to ten annotations on the changed lines, a notice counting the
rest, and the markdown report appended to the job summary. Doc drift and
security also write `sarif` for code scanning.

**The change being judged.** Coverage and security take a revision range:
`origin/main...HEAD` (three dots: what the branch did since it forked, the
pull request's view), `base..head`, or one commit. Without one they read the
target branch from the CI's own variables (`GITHUB_BASE_REF`,
`CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `CHANGE_TARGET`,
`BITBUCKET_PR_DESTINATION_BRANCH`), else the remote's default branch. Doc
drift has no range: it checks the whole working tree, and a baseline limits
it to new findings.

**History.** CI checkouts are usually shallow, which leaves no merge-base.
Fetch full history (`fetch-depth: 0` on GitHub Actions, `GIT_DEPTH: 0` on
GitLab) and make sure the target branch exists as `origin/<branch>`. The
gates exit `2` rather than guess.

## Adopting the gates on an existing repository

Coverage and security judge only what a change adds, so an old untested file
or an old finding never fails a new pull request. Doc drift checks the whole
tree, so on a repository whose documentation has already drifted it fails from
the first run. Record what is there once and fail only on new drift:

```bash
repowise doc-drift --check --write-baseline .doc-drift-baseline.json
git add .doc-drift-baseline.json
```

Then pass `--baseline .doc-drift-baseline.json` (or the action's
`doc-drift-baseline` input). Entries are keyed on the document, the reference
and its target, so an edit above a finding does not turn it back into a new
one.

Security has a baseline too, for accepting a finding a change adds on purpose
(a documented test key, say): `repowise security check --write-baseline
.security-baseline.json` on that change records it, and `--baseline` accepts it
from then on.

## GitHub Actions

The repository is itself an action. It installs Repowise, runs the gates you
pick, keeps going when one fails so the job reports all of them, and fails the
step at the end:

```yaml
on: pull_request
permissions:
  contents: read
jobs:
  repowise:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: pytest --cov=src --cov-report=lcov:coverage/lcov.info
      - uses: repowise-dev/repowise@main   # pin a release tag in production
        with:
          checks: coverage,doc-drift,security
          coverage-report: coverage/lcov.info
          coverage-fail-under: 80
```

Pin a release tag rather than `main`, and set `version:` to pin the Repowise
release the action installs.

| Input | Default | Meaning |
|-------|---------|---------|
| `checks` | `doc-drift,security` | Gates to run. Coverage needs a report, so it is opt-in. |
| `version` | latest | Repowise version to install. |
| `python-version` | `3.12` | Python to run it with (3.11 or newer). |
| `working-directory` | `.` | Where to run from. |
| `base` | from the pull request | Revision range for coverage and security. |
| `coverage-report` | config, else discovery | Report paths, one per line. |
| `coverage-fail-under` | `coverage.fail_under` | Minimum patch coverage percent. |
| `doc-drift-baseline` | none | Committed baseline file. |
| `security-fail-on` | `high` | Lowest severity that fails: `high`, `med`, `low`. |
| `security-baseline` | none | Committed baseline file. |
| `upload-sarif` | `false` | Upload doc drift and security findings to code scanning. |

Outputs: `coverage`, `doc-drift` and `security` hold each gate's exit code
(empty when not run), and `sarif-dir` the SARIF directory.

A shallow checkout still works when coverage or security runs: the action
fetches the missing history and the target branch first. Checking out with
`fetch-depth: 0` skips that step.

**Code scanning.** With `upload-sarif: true` the findings appear in the
repository's Security tab and on the pull request's diff. The job then needs
`security-events: write` (and `actions: read` in a private repository). A pull
request from a fork runs with a read-only token, so the upload is skipped
there with a warning; the gates still run and still decide the check.

**Without the action,** each gate is one step:

```yaml
- run: pip install repowise
- run: repowise coverage check --report coverage/lcov.info --fail-under 80 --format github
- run: repowise doc-drift --check --format github
- run: repowise security check --format github
```

On a pull request the gates read the target branch from `GITHUB_BASE_REF`
themselves. On a `push` event there is none; pass the range explicitly, for
example `"$BEFORE..$GITHUB_SHA"` with `BEFORE: ${{ github.event.before }}` set
under `env:`.

## GitLab

Include the template and set variables for what you use:

```yaml
include:
  - remote: https://raw.githubusercontent.com/repowise-dev/repowise/main/ci/gitlab/repowise.gitlab-ci.yml

variables:
  REPOWISE_COVERAGE_REPORT: coverage/lcov.info
  REPOWISE_COVERAGE_FAIL_UNDER: "80"

repowise-coverage:
  needs: [test]   # the job that writes the report as an artifact
```

It adds one job per gate on merge request pipelines: `repowise-coverage` (only
when `REPOWISE_COVERAGE_REPORT` is set), `repowise-doc-drift` and
`repowise-security`. Each prints its markdown report to the log and keeps it as
an artifact. Other variables: `REPOWISE_VERSION`, `REPOWISE_DOC_DRIFT_BASELINE`,
`REPOWISE_SECURITY_BASELINE`, `REPOWISE_SECURITY_FAIL_ON`. Override any job's
`image`, `rules` or `needs` in your own file as usual.

## Other CI systems

The gates are plain commands. Fetch the target branch, run them, and let the
exit code fail the build. Jenkins multibranch:

```bash
git fetch --no-tags origin "+refs/heads/$CHANGE_TARGET:refs/remotes/origin/$CHANGE_TARGET"
pip install repowise
repowise coverage check "origin/$CHANGE_TARGET...HEAD" --report coverage/lcov.info --fail-under 80
repowise doc-drift --check
repowise security check "origin/$CHANGE_TARGET...HEAD"
```

Bitbucket Pipelines and others work the same way with their own branch
variable; `--format markdown` or `json` gives you something to post.

## Coverage reports per language

The coverage gate needs the lines each file could have run, not only the lines
it did, and a new file no test loads must still appear in the report or it is
listed as "not in report" and left out of the figure. These commands do both:

| Stack | Produce the report | Pass |
|-------|--------------------|------|
| Python (pytest) | `pytest --cov=src --cov-report=lcov:coverage/lcov.info` (lcov paths are relative to the working directory) | `coverage/lcov.info` |
| Python (coverage.py) | `coverage lcov` or `coverage xml` after the run | `coverage.lcov`, `coverage.xml` |
| Go | `go test -coverprofile=coverage.out -coverpkg=./... ./...` (`-coverpkg` includes packages with no tests) | `coverage.out` |
| Java or Kotlin (Gradle) | `gradle test jacocoTestReport` with the XML report on | `build/reports/jacoco/test/jacocoTestReport.xml` |
| Java (Maven) | `mvn test jacoco:report` (`report-aggregate` for a multi-module build) | `target/site/jacoco/jacoco.xml` |
| JavaScript or TypeScript | `c8 --all --reporter=lcov`, or a test runner's `lcov` reporter with every source file included | `coverage/lcov.info` |
| Rust | `cargo llvm-cov --lcov --output-path coverage/lcov.info` | `coverage/lcov.info` |

Several reports merge: a line any of them ran counts as run. A coverage.py
`.coverage` database is not a text report; export it first. When report paths
do not line up with the repository (a build directory, a container path), set
`coverage.strip_prefix` or `coverage.path_prefix` in `.repowise/config.yaml`.
The gate reports how many report paths matched the repository, and exits `2`
when none did, so a wrong prefix never passes as zero.

The threshold can live in config instead of the workflow:

```yaml
# .repowise/config.yaml
coverage:
  paths: [coverage/lcov.info]
  fail_under: 80
```

## When a gate exits 2

| Message | Fix |
|---------|-----|
| no merge-base, or a commit cut off by a shallow clone | fetch full history |
| unknown revision `origin/<branch>` | fetch the target branch |
| no coverage report found | pass `--report`, or set `coverage.paths` |
| report paths match no file in the repository | set `coverage.strip_prefix` or `coverage.path_prefix` |
| baseline unreadable | commit the file, or drop `--baseline` |
