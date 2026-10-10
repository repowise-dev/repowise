# Repowise in CI

Four gates judge a pull request, each on what the change adds rather than on
the whole repository's past:

| Gate | Question | Needs | History |
|------|----------|-------|---------|
| `repowise coverage check` | Did the tests run the lines this change touched? | git and a coverage report | the merge-base with the target branch |
| `repowise coverage check --fail-under-branches P` | Did the tests take the branches of the lines this change touched? | git and a report with per-line branch data | the merge-base with the target branch |
| `repowise coverage check --max-drop P` | Did this change lower project coverage by more than P points? | git, a coverage report, and one measured at the change's base | the merge-base with the target branch |
| `repowise doc-drift --check` | Does the documentation still describe files, links and commands that exist? | git | none (full history only improves rename suggestions) |
| `repowise security check` | Did this change add a secret or a risky call? | git | every commit of the change |
| `repowise risk --fail-above-percentile P` | Is this change bigger and more spread out than P% of this repository's recent commits? | git | recent commits to rank against (at least 8) |

None of them needs an index, a database, a model or an API key, and none
stores anything. They share one set of exit codes, output formats and base
resolution, described once below. Each layer's own page has the detail:
[test intelligence](../layers/TEST_INTELLIGENCE.md#patch-coverage-in-ci),
[documentation drift](../layers/DOC_DRIFT.md#in-ci),
[security](../layers/SECURITY.md#in-ci-repowise-security-check),
[change risk](../layers/CHANGE_RISK.md#in-ci).

Doc drift's `--check` needs no index. `--kind symbol` adds symbol references
(names of functions and classes a document mentions) and needs an index, so it
exits `2` without one. It also reads `git blame`: in a shallow clone every line
blames to the boundary commit, so give that job `fetch-depth: 0`.

Not a gate, but in the same pipeline:
[selecting the tests a change needs](#selecting-the-tests-a-change-needs).

## What every gate does the same way

**Exit codes.** `0` passed, or had nothing to judge. `1` the change failed the
gate. `2` the gate could not evaluate: not a git repository, an unknown
revision, no merge-base in a shallow clone, an unreadable report or baseline,
too few recent commits to rank a change against. A setup problem never reads
as a pass.

**Formats.** `--format table` (the default) for a terminal, `json` for a
script, `markdown` for a comment or a job artifact, and `github` for GitHub
Actions: up to ten annotations on the changed lines, a notice counting the
rest, and the markdown report appended to the job summary. Doc drift and
security also write `sarif` for code scanning and `gitlab`, a GitLab Code
Quality report (a JSON issue list; findings a baseline accepted are left out).
When a gate cannot evaluate, `gitlab` still prints a valid list, `[]`, and the
GitLab template writes `[]` whenever the output does not parse, so the report
artifact is never an invalid file.

**The change being judged.** Coverage, security and risk take a revision range:
`origin/main...HEAD` (three dots: what the branch did since it forked, the
pull request's view), `base..head`, or one commit. Without one they read the
target branch from the CI's own variables (`GITHUB_BASE_REF`,
`CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `CHANGE_TARGET`,
`BITBUCKET_PR_DESTINATION_BRANCH`), else the remote's default branch. `risk`
does so with `--fail-above-percentile` or `--format markdown` / `github`;
otherwise an omitted range still means your uncommitted work, else `HEAD`. Doc
drift has no range: it checks the whole working tree, and a baseline limits it
to new findings.

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
one. Alternatively, `repowise doc-drift --check --since auto` judges only what
the change is answerable for (documents it edits, and documents naming files it
deletes or renames), which needs no baseline but does need `fetch-depth: 0`.

Security has a baseline too, for accepting a finding a change adds on purpose
(a documented test key, say): `repowise security check --write-baseline
.security-baseline.json` on that change records it, and `--baseline` accepts it
from then on. A `repowise-security-ignore` comment silences one false positive
([details](../layers/SECURITY.md#silencing-one-finding-repowise-security-ignore)).

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
          checks: coverage,doc-drift,security,risk
          coverage-report: coverage/lcov.info
          coverage-fail-under: 80
          risk-fail-above-percentile: 95
```

Pin a release tag rather than `main`, and set `version:` to pin the Repowise
release the action installs.

| Input | Default | Meaning |
|-------|---------|---------|
| `checks` | `doc-drift,security` | Gates to run. Coverage needs a report and risk is a policy choice, so both are opt-in. |
| `version` | latest | Repowise version to install. |
| `python-version` | `3.12` | Python to run it with (3.11 or newer). |
| `working-directory` | `.` | Where to run from. |
| `base` | from the pull request | Revision range for coverage, security and risk. |
| `coverage-report` | config, else discovery | Reports, one per line: a path or a glob (`artifacts/**/lcov.info`), optionally `path=prefix`. |
| `coverage-fail-under` | `coverage.fail_under` | Minimum patch coverage percent. |
| `coverage-min-coverable-lines` | `coverage.min_coverable_lines` | Small-change tolerance: a change with fewer changed executable lines than this never fails. |
| `coverage-fail-under-risky` | `coverage.fail_under_risky` | Minimum patch coverage percent over risky files only: hotspots or bug magnets from the index; on git alone, the top quartile of files with bug-fix history. |
| `coverage-fail-under-branches` | `coverage.fail_under_branches` | Minimum percent of branches taken on changed lines. |
| `coverage-base-report` | none | Reports measured at the change's base commit, one per line. See [Project coverage](#project-coverage-and-coverage-outside-the-change). |
| `coverage-max-drop` | `coverage.max_drop` | Most project coverage may fall from the base, in percentage points. |
| `doc-drift-baseline` | none | Committed baseline file. |
| `security-fail-on` | `high` | Lowest severity that fails: `high`, `med`, `low`. |
| `security-baseline` | none | Committed baseline file. |
| `risk-fail-above-percentile` | none | With `risk` in `checks`, fail when the change ranks above this percentile of recent commits. Empty reports the rank without gating. |
| `upload-sarif` | `false` | Upload doc drift and security findings to code scanning. |
| `impacted-tests` | `false` | Select the tests the change needs (needs a cached `.repowise` index); see [below](#selecting-the-tests-a-change-needs). |
| `impacted-tests-runner` | `auto` | `auto`, `pytest`, `go`, `jest` or `files`. |

Outputs: `coverage`, `doc-drift`, `security` and `risk` hold each gate's exit
code (empty when not run), and `sarif-dir` the SARIF directory.
`impacted-tests` holds the runner arguments (or `:all`), and `run-all-tests` is
`false` only when that subset is safe to run alone.

The risk gate ranks the change against the repository's own recent commits,
so at a percentile P roughly (100 - P)% of changes fail it by construction.
A failure asks for a split or a second reviewer, not a code fix, and there is
no baseline to accept it with.

A shallow checkout still works when coverage, security or risk runs: the action
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
- run: repowise risk --fail-above-percentile 95 --format github
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
when `REPOWISE_COVERAGE_REPORT` is set), `repowise-doc-drift`,
`repowise-security` and `repowise-risk` (only when
`REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` is set). Each prints its markdown report
to the log and keeps it as an artifact. The doc drift and security jobs also
write a Code Quality report (`gl-code-quality-doc-drift.json`,
`gl-code-quality-security.json`), so their findings show in the merge
request's Code Quality widget, even when the gate fails the job. One more job,
`repowise-code-quality`, runs on the default branch and never fails the
pipeline: it publishes the report the widget compares a merge request against,
so only issues the merge request introduces show as new. It publishes doc
drift only: security judges only what a change adds, and the default branch
has no change to judge.
`REPOWISE_COVERAGE_REPORT` is space separated and may hold globs and
`path=prefix` entries; Repowise expands the globs, not the shell. Other
variables: `REPOWISE_VERSION`, `REPOWISE_COVERAGE_MIN_COVERABLE_LINES`,
`REPOWISE_COVERAGE_FAIL_UNDER_RISKY`, `REPOWISE_COVERAGE_BASE_REPORT` (space
separated, like the report list), `REPOWISE_COVERAGE_MAX_DROP`,
`REPOWISE_DOC_DRIFT_BASELINE`,
`REPOWISE_SECURITY_BASELINE`, `REPOWISE_SECURITY_FAIL_ON`. Override any job's
`image`, `rules` or `needs` in your own file as usual.

GitLab can also draw line coverage in the merge request diff. That comes from
your own test job, not from Repowise: have your test runner write a Cobertura
report (for example with pytest-cov) and declare it there. The same
`coverage.xml` can be your `REPOWISE_COVERAGE_REPORT`.

```yaml
test:
  script:
    - pytest --cov --cov-report=xml:coverage.xml
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

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

## Selecting the tests a change needs

`repowise impacted-tests --format args` prints one line of arguments for a test
runner, or `:all` whenever any part of the answer is not known, with the
reasons on stderr. It needs an index (cached from the default branch, below);
a per-test coverage map (`repowise coverage add`) makes it more precise.

- It exits `0` for a subset or for everything, and `2` only when it cannot read the change or the `tests.*` config.
- Branch on the run-all flag, never on `:all`: jest and vitest read it as a pattern and run nothing.
- It runs everything for: an empty change; a lockfile, manifest, build, test or CI config change; `.repowise/config.yaml`; a production package's `__init__.py`.
- It also runs everything for: non-code files in a test tree; a changed file with no known test or only a filename guess; a route through a test helper no test imports; any Python test helper while a conftest or pytest config loads plugins by name.
- It also runs everything for: a missing or unreadable index; a per-test map at its row cap; a deleted file no known test used.
- An index built at an older commit than the change's base is used as is. Files changed in between whose imports or module-name strings moved (any change beyond whitespace, outside Python and JavaScript/TypeScript) add the tests reaching them. It runs everything instead when that commit is not in the clone, or more than 1,000 files changed in between, or one of them is a lockfile, manifest, build or test config or `.repowise/config.yaml`. CI config and test data changed in between no longer force a full run: neither is imported.
- Only `docs/` and root README-like files (README, CHANGELOG, LICENSE, CONTRIBUTING) are skipped, and only when no code names them (a test that reads `README.md` names it); an empty line means only those changed.
- Tests the graph cannot see into run with every subset, and the reasons say how many and why: a test not indexed or importing nothing it resolves; a Python, JavaScript or TypeScript test that lists and reads files under a source directory (the working directory, a module's location, or its own location climbed out of the test directory; never its fixtures, sibling data or a temporary directory); one that runs the project's own command, `python -m` module, code importing the project, or package `bin` in a child process. The indexer finds them once from each test's source and stores the reason, so selection reads it without opening test files. An index built before this check runs everything until `repowise update` checks its tests. A Python module named like a test but outside every test directory is not a test when the nearest pytest config's `testpaths` or `python_files` leaves it out, as a bare `pytest` does: it is selected through like any other code. The rule follows a bare `pytest` run, so a test that CI only names explicitly on the command line outside `testpaths` is not seen as one: list it in `tests.always_run`.
- A changed `conftest.py`, test package `__init__.py` or imported test helper selects the tests under or importing it, not everything.
- Without a range, in CI it reads the pull request's change; locally, the staged changes.
- `tests.full_run_on` (gitignore patterns) adds run-everything paths; `tests.always_run` is appended to every selection as written, for the runner whose language each entry is in.
- With `--runner pytest`, `go` or `jest`, the arguments hold only that runner's tests, `tests.always_run` included. A repository with tests in several languages needs one job per runner: the tests a job leaves out are counted on stderr, and listed per runner under `left_out` with `--format json`. `--runner files` gets every test.
- `--explain <test>` says why one test file was or was not selected: the first changed file that reached it, the evidence (coverage, call graph, import graph) and the import route from the test to that file when there is one, or the rule that runs it. With `--format json` the same answer is added as `explain`, and `selected.why` holds it for every selected test.
- Ceiling: on the JVM and .NET, a same-package test linked only by an unresolved call is missed. So is a test that reaches the change through a plugin or registry loaded by a string name, a tree walk or child process done inside a shared helper it calls, or one in another language: list such tests in `tests.always_run`, and run the full suite on the default branch.

**What to expect.** The subset is only as small as the code is loosely coupled.
On tightly coupled code the graph alone picks most tests (over 80% of test
files on this repository), because most tests import something that imports
the changed file. Real savings need a per-test map measured at the change's
base. `--format json` shows what drove each file's selection in
`selected.basis`.

On GitHub Actions, keep the index cached from the default branch and branch
on the outputs in the pull request job:

```yaml
# .github/workflows/repowise-index.yml
on: {push: {branches: [main]}}
jobs:
  index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: actions/cache/restore@v4
        with: {path: .repowise, key: "repowise-${{ github.sha }}", restore-keys: "repowise-"}
      - run: pip install repowise
      - run: if [ -f .repowise/state.json ]; then repowise update --index-only; else repowise init --yes --index-only; fi
      - uses: actions/cache/save@v4
        with: {path: .repowise, key: "repowise-${{ github.sha }}"}
```

```yaml
# in the pull request workflow
- uses: actions/checkout@v4
  with: {fetch-depth: 0}
- uses: actions/cache/restore@v4
  with: {path: .repowise, key: "repowise-${{ github.event.pull_request.base.sha }}", restore-keys: "repowise-"}
- id: select
  uses: repowise-dev/repowise@main
  with: {checks: "", impacted-tests: true, impacted-tests-runner: pytest}
- if: steps.select.outputs.run-all-tests != 'false'
  run: pytest
- if: steps.select.outputs.run-all-tests == 'false' && steps.select.outputs.impacted-tests != ''
  env: {TESTS: "${{ steps.select.outputs.impacted-tests }}"}
  run: eval "pytest $TESTS"
```

A skipped or failed step leaves `run-all-tests` empty, so `!= 'false'` runs
everything.

On GitLab, set `REPOWISE_IMPACTED_TESTS: "true"` (and optionally
`REPOWISE_IMPACTED_TESTS_RUNNER`). The `repowise-impacted-tests` job writes the
arguments to `impacted-tests.txt` and `RUN_ALL_TESTS` to a dotenv report:

```yaml
repowise-index:
  image: python:3.12
  variables: {GIT_DEPTH: "0"}
  script:
    - pip install repowise
    - if [ -f .repowise/state.json ]; then repowise update --index-only; else repowise init --yes --index-only; fi
  cache: {key: repowise-index, paths: [.repowise/], policy: pull-push}
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

repowise-impacted-tests:
  cache: {key: repowise-index, paths: [.repowise/], policy: pull}

test:
  needs: [{job: repowise-impacted-tests, optional: true}]
  script:
    - |
      if [ "${RUN_ALL_TESTS:-}" != "false" ]; then pytest
      elif [ -n "$(cat impacted-tests.txt 2>/dev/null)" ]; then eval "pytest $(cat impacted-tests.txt)"
      fi
```

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
  min_coverable_lines: 5   # small-change tolerance
  fail_under_risky: 90     # optional, see Risk-weighted patch coverage
  fail_under_branches: 70  # optional, see Branches on changed lines
```

With `min_coverable_lines` set, a change touching fewer changed executable
lines than that is still reported against the threshold, but a miss reads
"not applied" (a notice on GitHub) and exits `0`: one uncovered line in a
two-line fix should not block a merge. Like the percentage, the count is of
lines the report measures; a file the report does not name adds nothing to it.

### Monorepos and matrix jobs

A matrix of test jobs leaves one report per shard. `--report` (and each line
of `coverage-report`) takes a glob, relative to the working directory, so
`artifacts/**/lcov.info` reads them all; they merge hit-wins. `**` walks the
tree the way discovery does, skipping dependency, cache and build directories
(`node_modules`, `.venv`, `dist`, ...) and nested repositories. A job whose
reports never arrived exits `2`: a literal path that does not exist, or a
glob that matches nothing, cannot be evaluated. A glob still matches when one
shard of several is missing, so to catch that, list each shard's report on
its own line. `coverage.paths` entries that match nothing are skipped.

On GitHub Actions, each shard uploads its report and the gate job downloads
them all:

```yaml
jobs:
  test:
    strategy:
      matrix: {shard: [1, 2, 3]}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest tests/shard${{ matrix.shard }} --cov=src --cov-report=lcov:coverage/lcov.info
      - uses: actions/upload-artifact@v4
        with: {name: "coverage-${{ matrix.shard }}", path: coverage/lcov.info}
  coverage:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: actions/download-artifact@v4
        with: {pattern: "coverage-*", path: artifacts}
      - uses: repowise-dev/repowise@main
        with:
          checks: coverage
          coverage-report: artifacts/**/lcov.info
```

On GitLab, give each parallel shard its own path and let the gate job need
them:

```yaml
test:
  parallel: 3
  script: pytest tests/shard$CI_NODE_INDEX --cov=src --cov-report=lcov:coverage/$CI_NODE_INDEX/lcov.info
  artifacts:
    paths: [coverage/]

variables:
  REPOWISE_COVERAGE_REPORT: "coverage/**/lcov.info"

repowise-coverage:
  needs: [test]
```

When one package's report names paths relative to that package, give it a
per-report prefix: `--report web/coverage/lcov.info=web`. The whole argument
is tried as a path or glob first, so `artifacts/shard=1/*.info` stays one;
only when it matches nothing is it split on the last `=`. In config the same
is a mapping entry, and `paths` entries may be globs, relative to the
repository root:

```yaml
coverage:
  paths:
    - {path: "web/coverage/lcov.info", path_prefix: web}
    - "services/*/coverage.xml"
  ignore: ["**/*_pb2.py", "gen/"]   # gitignore syntax
```

`coverage.ignore` leaves generated or vendored files out on both sides:
changed files it matches are dropped before measuring (the summary counts them
as ignored, here and in the editor and agent views), and report entries it
matches are not stored by `repowise coverage add` or indexing. When it leaves
out every entry of the reports, the gate exits `2` and says so.

Go coverprofiles name files by import path. When a module lives in a
subdirectory (`backend/go.mod` declaring `module example.com/m`), paths under
`example.com/m/` are mapped to `backend/` using the `go.mod` files in the
checkout, so files in the module's top-level package match with no prefix.
A per-report prefix or `coverage.path_prefix` turns the mapping off.

### Path-scoped gates

One threshold over the whole change lets a well-tested package carry an
untested one. `coverage.gates` adds a gate per part of the repository: each is
judged on the changed files its globs match, with the same rules as the
whole-change gate. Globs use gitignore syntax, like `coverage.ignore`, and are
relative to the repository root; a leading `/` anchors one there (`/api/` is
the top-level `api` directory, `api/` any directory of that name).

```yaml
# .repowise/config.yaml
coverage:
  fail_under: 70                # the whole change, as before
  gates:
    - name: api
      paths: ["/services/api/"]
      fail_under: 85
    - name: web
      paths: ["/apps/web/", "!/apps/web/generated/"]
      fail_under: 60
    - name: scripts
      paths: ["/scripts/"]
      fail_under: 50
      informational: true       # reported, never fails
```

A gate without `fail_under` is reported but judges nothing. A file can match
several gates; each counts it. The change fails when the whole-change gate
fails or when any gate that is not `informational` fails. The headline then
leads with the failing gate and its counts (`Fails: path-scoped gate api 60.0%
(6 of 10 changed executable lines), below its 85.0% gate.`) before the
whole-change verdict, the summary adds a table of gates (failing ones first),
and on GitHub each failing gate is its own `::error::`; an informational gate
below its threshold is a `::notice::`. The small-change tolerance is the whole
change's: when the change has fewer changed executable lines than
`min_coverable_lines`, every gate reads `too_small`, but a small slice of a
big change is judged. An entry the config cannot use (no name, a repeated
name, no paths or only `!` exclusions, a `fail_under` outside 0-100, an
unknown key) makes the check exit `2` naming it.

Like the whole-change figure, a gate counts only changed lines the report
measures. A changed file it matches that the report does not measure (a new
file no test loads, a report without line data) is counted apart, as
`unmeasured_file_count`, and left out of the percentage: a gate over only such
files reads "no measured changed lines (2 changed files not measured)" and
fails nothing.

With one report per job, each job would judge the gates on a fragment of the
change: run `coverage check` once over the combined reports instead, as in
[Monorepos and matrix jobs](#monorepos-and-matrix-jobs).

The gates live in the committed config, so the Action and the GitLab template
take no input for them: every job reads the same gates. The editor, REST and
agent views report the same verdicts when the stored coverage was measured at
the change's head and the config is valid; on stale coverage, or beside an
invalid entry (which they list in `scope.config_errors`), their gates read
`no_data`. To start, let Repowise propose some:

```bash
repowise coverage suggest-gates                # YAML to paste below coverage:
repowise coverage suggest-gates --format json
```

It reads CODEOWNERS (one gate per owner), the top-level packages git tracks,
and, when the repository is indexed, the graph's communities, labelling each
source in a comment. The block is indented to paste directly below your
`coverage:` line. It writes nothing and sets no `fail_under`: keep the gates
you want and choose their thresholds.

### Risk-weighted patch coverage

An uncovered line in a file that keeps breaking matters more than one in a
file nothing depends on. Every changed file in the coverage report carries its
risk, and the table, the annotations and the summary list the riskiest files
first, with a "Risk" column in plain words ("hotspot, bug-fix weight 3.2, 14
dependents", "none known", or "unknown" when it could not be read). A file is
risky when the index flags it a hotspot or a bug magnet; without an index, or
for a file the change adds, when its recency-weighted bug-fix weight from git
is in the top quartile of the files with bug-fix history. The report says
which basis each row used when not every file had index data. When an index is
present, each annotation also names the test file to extend when one is found
("Extend tests/test_auth.py (inferred: calls reach `login`)").

`--fail-under-risky` (the action's `coverage-fail-under-risky`) adds one more
gate, stricter, over the risky files' changed lines alone, and the check fails
when any gate fails. It follows the same rules as the others, the whole
change's small-change tolerance included. A change that touches no risky file
leaves it not applied (`no_data`, exit `0`). With the gate set, a shallow
clone, or a measured file whose risk could not be read (no git history and no
index row), exits `2`, unless the whole-change, a path-scoped or the branch
gate already failed: that failure is reported.

### Branches on changed lines

When the report has per-line branch data (lcov `BRDA`, Cobertura
`condition-coverage`, JaCoCo branch counters, Clover `cond`), the check also
reports "Branches on changed lines 70.0% (7 of 10 taken; 2 lines partly
taken)". A partly taken line ran with a branch never taken: it gets a "Partly
taken" column and, on GitHub, a "Partly taken branch" warning.
`--fail-under-branches P` (`coverage-fail-under-branches`,
`coverage.fail_under_branches`) gates the share.

- Line and branch coverage are reported side by side, never blended.
- The small-change tolerance applies, and counts changed lines, not branches.
- No changed line with branches: "not measured", never 0%, and the gate is not
  applied (exit `0`).
- Coverage with no per-line branch data (a Go coverprofile, coverage.py without
  `--branch`, stored coverage ingested before branches were kept) exits `2`
  under the flag, unless another gate already failed, and is a note with exit
  `0` from the config key alone.
- Several reports merge per line by the most branches taken and seen, so the
  share is a lower bound.

### Project coverage and coverage outside the change

Patch coverage cannot see a change that deletes a test or a code path other
files relied on. Given a report measured at the change's base commit, the check
adds project coverage at the base and the head, and lists files whose coverage
changed outside the change with the changed test or dependent that explains it.

```bash
repowise coverage check origin/main...HEAD --report coverage/lcov.info \
  --base-report base/lcov.info --max-drop 0.5
```

- `--max-drop P` fails when project coverage falls more than `P` points. The
  small-change tolerance does not apply.
- The base report must be measured at the merge-base (`A` for `A..B`). Files
  whose lines do not line up are left out, and a note says how many.
- Both sides must be measured the same way: the same reports of each format,
  the same `coverage.ignore`, neither mapping fewer than half its files.
- A base the check cannot use exits `2` under the `--max-drop` or
  `--base-report` flag (the action's inputs), and is a note with exit `0` when
  the gate comes from `coverage.max_drop` alone.

On GitHub Actions, in a job that runs on pushes to the default branch and on
pull requests, checked out with `fetch-depth: 0`:

```yaml
- run: pytest --cov=src --cov-report=lcov:coverage/lcov.info
- if: github.event_name == 'push'
  run: mkdir -p base-coverage && cp coverage/lcov.info base-coverage/
- if: github.event_name == 'push'
  uses: actions/cache/save@v4
  with: {path: base-coverage, key: "coverage-${{ github.sha }}"}
- if: github.event_name == 'pull_request'
  id: merge-base
  run: echo "sha=$(git merge-base "origin/$GITHUB_BASE_REF" HEAD)" >> "$GITHUB_OUTPUT"
- if: github.event_name == 'pull_request'
  id: base
  uses: actions/cache/restore@v4
  with: {path: base-coverage, key: "coverage-${{ steps.merge-base.outputs.sha }}"}
- if: github.event_name == 'pull_request'
  uses: repowise-dev/repowise@main
  with:
    checks: coverage
    coverage-report: coverage/lcov.info
    # Only when the base's report was found; a missing file exits 2.
    coverage-base-report: ${{ steps.base.outputs.cache-hit == 'true' && 'base-coverage/lcov.info' || '' }}
    coverage-max-drop: ${{ steps.base.outputs.cache-hit == 'true' && '0.5' || '' }}
```

On GitLab, the merge request job uses the target branch's latest `test`
artifact only when it was measured at the merge-base (a rebased merge request,
or merged-results pipelines); otherwise the check runs without a base:

```yaml
test:
  script:
    - pytest --cov=src --cov-report=lcov:coverage/lcov.info
    - echo "$CI_COMMIT_SHA" > coverage/commit.txt
  artifacts: {paths: [coverage/]}

repowise-coverage:
  needs: [test]
  before_script:
    - !reference [.repowise, before_script]
    - |
      url="$CI_API_V4_URL/projects/$CI_PROJECT_ID/jobs/artifacts/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME/download?job=test"
      if curl -sfL --header "JOB-TOKEN: $CI_JOB_TOKEN" -o base.zip "$url" \
        && python -m zipfile -e base.zip base \
        && [ "$(cat base/coverage/commit.txt)" = \
             "$(git merge-base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" HEAD)" ]; then
        export REPOWISE_COVERAGE_BASE_REPORT=base/coverage/lcov.info
        export REPOWISE_COVERAGE_MAX_DROP=0.5
      fi
```

## When a gate exits 2

| Message | Fix |
|---------|-----|
| "Could not diff ...: ... A shallow CI clone needs the base branch and enough history for a merge-base" | fetch full history |
| "Could not tell which branch this change targets. Pass REVSPEC, e.g. origin/main...HEAD." | fetch the target branch, or pass the range |
| "No coverage report found. Pass one with --report ..." | pass `--report`, or set `coverage.paths` |
| "--report ...: no coverage report matches ..." | fix the path or glob, or upload the report from the test job |
| "coverage.gates[N] ...: ..." | fix that entry of `coverage.gates` |
| "All N report entries match coverage.ignore, so nothing is left to measure." | narrow `coverage.ignore` |
| "No report path matched a file in this repository." | set `coverage.strip_prefix` or `coverage.path_prefix` |
| "cannot read baseline ..." or "baseline ... is not valid JSON" | commit the file, or drop `--baseline` |
| "Only N recent commits could be sampled; ranking a change needs at least 8." (risk) | fetch full history |
| "The change-risk gate ranks the change against recent commits, and --baseline 0 turns that off." | drop `--baseline 0` |
| "The risky-file gate reads bug-fix history, and this clone is shallow." | fetch full history |
| "Could not read the risk of N changed files, so the risky-file gate cannot run." | fetch full history, or index the repository |
| "The coverage report has no per-line branch data, so --fail-under-branches cannot be judged." | turn on branch coverage in the test run, or drop the flag |
| "The stored coverage predates per-line branch data ..." | re-run `repowise coverage add` with the report |
| "The max-drop gate needs coverage measured at the change's base ..." | pass `--base-report`, or ingest a report at the base commit |
| "Project coverage cannot be compared ..." | measure the base and the head the same way (same full reports, same `coverage.ignore`) |
| "Base report: No report path matched a file in this repository." | the base report must name files the base commit tracked; check its prefix |
