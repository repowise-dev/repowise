# Repowise in CI

Repowise gates judge a pull request on what the change adds, not on the whole
repository's past:

| Gate | Question | Needs | History |
|------|----------|-------|---------|
| `repowise coverage check` | Did the tests run the lines this change touched? | git and a coverage report | the merge-base with the target branch |
| `repowise coverage check --fail-under-branches P` | Did the tests take the branches of the lines this change touched? | git and a report with per-line branch data | the merge-base with the target branch |
| `repowise coverage check --max-drop P` | Did this change lower project coverage by more than P points? | git, a coverage report, and one measured at the change's base | the merge-base with the target branch |
| `repowise doc-drift --check` | Does the documentation still describe files, links and commands that exist? | git | none (full history only improves rename suggestions) |
| `repowise security check` | Did this change add a secret or a risky call? | git | every commit of the change |
| `repowise health check` | Did this change write new complex, deeply nested, oversized or duplicated code? | git | the merge-base with the target branch |
| `repowise risk --fail-above-percentile P` | Is this change bigger and more spread out than P% of this repository's recent commits? | git | recent commits to rank against (at least 8) |

- No gate needs an index, a database, a model or an API key, and none stores
  anything.
- All gates share one set of exit codes, output formats and base resolution
  ([below](#what-every-gate-does-the-same-way)).
- Each layer's page has the detail:
  [test intelligence](../layers/TEST_INTELLIGENCE.md#patch-coverage-in-ci),
  [documentation drift](../layers/DOC_DRIFT.md#in-ci),
  [security](../layers/SECURITY.md#in-ci-repowise-security-check),
  [code health](../layers/CODE_HEALTH.md#in-ci-repowise-health-check),
  [change risk](../layers/CHANGE_RISK.md#in-ci).
- Not a gate, but in the same pipeline:
  [selecting the tests a change needs](#selecting-the-tests-a-change-needs).

## What every gate does the same way

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Passed, or had nothing to judge. |
| `1` | The change failed the gate. |
| `2` | The gate could not evaluate: not a git repository, an unknown revision, no merge-base in a shallow clone, an unreadable report or baseline, too few recent commits to rank a change against. |

A setup problem never reads as a pass. Fixes for each exit `2` message are in
[When a gate exits 2](#when-a-gate-exits-2).

### Output formats

| `--format` | For |
|------------|-----|
| `table` (default) | A terminal. |
| `json` | A script. |
| `markdown` | A comment or a job artifact. |
| `github` | GitHub Actions: up to ten annotations on the changed lines, a notice counting the rest, and the markdown report appended to the job summary. |
| `sarif` | Code scanning. Doc drift, security and health only. |
| `gitlab` | A GitLab Code Quality report: a JSON issue list, leaving out findings a baseline accepted. Doc drift, security and health only. |

When a gate cannot evaluate, `gitlab` still prints a valid list, `[]`. The
GitLab template writes `[]` whenever the output does not parse, so the report
artifact is never an invalid file.

### The change being judged

- Coverage, security, health and risk take a revision range:
  - `origin/main...HEAD` (three dots: what the branch did since it forked, the
    pull request's view);
  - `base..head`;
  - or one commit.
- Without a range they read the target branch from the CI's own variables
  (`GITHUB_BASE_REF`, `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `CHANGE_TARGET`,
  `BITBUCKET_PR_DESTINATION_BRANCH`), else the remote's default branch.
- `risk` does so only with `--fail-above-percentile` or `--format markdown` /
  `github`. Otherwise an omitted range still means your uncommitted work, else
  `HEAD`.
- Doc drift takes no range: it checks the whole working tree, and a baseline
  limits it to new findings.
- The variables each CI is detected by and read from, Azure Pipelines
  included, are in [GIT_HOSTS.md](GIT_HOSTS.md#ci-systems).

### History

CI checkouts are usually shallow, which leaves no merge-base. The gates exit
`2` rather than guess.

- Fetch full history: `fetch-depth: 0` on GitHub Actions, `GIT_DEPTH: 0` on
  GitLab.
- Make sure the target branch exists as `origin/<branch>`.

## Adopting the gates on an existing repository

Coverage, security and health judge only what a change adds, so an old
untested file or an old finding never fails a new pull request. Doc drift
checks the whole tree, so a repository whose documentation already drifted
needs a [baseline](#gate-on-documentation-drift) before its first run.

- **Baselines are shrink-only on request.** `--fail-on-baseline-growth` (the
  action's `fail-on-baseline-growth` input) fails doc drift or security when
  the baseline lists a finding the same file at the change's base does not,
  so a change cannot hide new findings by re-saving it. A baseline the change
  adds for the first time passes.
- **Health rules come from the base.** The health gate reads
  `.repowise/health-rules.json` as the change's base has it, so a rule a change
  adds or loosens applies from the next change on.

## GitHub Actions

The repository is itself an action. It installs Repowise, runs the gates you
pick, keeps going when one fails so the job reports all of them, and fails the
step at the end: exit `1` when any gate failed, else `2` when any could not
evaluate.

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
          checks: coverage,doc-drift,security,health,risk
          coverage-report: coverage/lcov.info
          coverage-fail-under: 80
          risk-fail-above-percentile: 95
```

- Pin a release tag rather than `main`, and set `version:` to pin the
  Repowise release the action installs.
- A shallow checkout still works when coverage, security, health or risk runs:
  the action fetches the missing history and the target branch first.
  Checking out with `fetch-depth: 0` skips that step.

### Inputs

| Input | Default | What it does |
|-------|---------|--------------|
| `checks` | `doc-drift,security` | Gates to run: `coverage`, `doc-drift`, `security`, `health`, `risk`, separated by commas, spaces or newlines. Coverage needs a report, and health and risk are policy choices, so all three are opt-in. An unknown name exits `2`. |
| `version` | latest | Repowise version to install from PyPI. |
| `python-version` | `3.12` | Python to run it with (3.11 or newer). |
| `working-directory` | `.` | Where to run from. |
| `base` | from the pull request | Revision range for coverage, security, health, risk and test selection. Empty reads the target branch, else the default branch. |
| `upload-sarif` | `false` | Upload doc drift, security and health findings to [code scanning](#code-scanning-sarif). |

Each gate's own inputs are in its section:
[coverage](#gate-on-coverage),
[doc drift](#gate-on-documentation-drift),
[security](#gate-on-security),
[health](#gate-on-code-health),
[risk](#gate-on-change-risk),
[test selection](#selecting-the-tests-a-change-needs).

### Outputs

| Output | Holds |
|--------|-------|
| `coverage`, `doc-drift`, `security`, `health`, `risk` | Each gate's exit code; empty when not run. |
| `sarif-dir` | The SARIF directory, when `upload-sarif` is on and a gate wrote findings. |
| `impacted-tests` | The runner arguments for the selected tests, or `:all`. |
| `run-all-tests` | `false` only when that subset is safe to run alone; `true` when every test must run, also when selection could not run. |

### Without the action

Each gate is one step:

```yaml
- run: pip install repowise
- run: repowise coverage check --report coverage/lcov.info --fail-under 80 --format github
- run: repowise doc-drift --check --format github
- run: repowise security check --format github
- run: repowise health check --format github
- run: repowise risk --fail-above-percentile 95 --format github
```

On a pull request the gates read the target branch from `GITHUB_BASE_REF`
themselves. A `push` event has none, so pass the range explicitly, for example
`"$BEFORE..$GITHUB_SHA"` with `BEFORE: ${{ github.event.before }}` set under
`env:`.

## Gate on coverage

Fails when the tests did not run enough of the lines the change touched.

```yaml
- run: pytest --cov=src --cov-report=lcov:coverage/lcov.info
- uses: repowise-dev/repowise@main
  with:
    checks: coverage
    coverage-report: coverage/lcov.info
    coverage-fail-under: 80
```

| Action input | GitLab variable | CLI flag | Config key | What it does |
|--------------|-----------------|----------|------------|--------------|
| `coverage-report` | `REPOWISE_COVERAGE_REPORT` | `--report` | `coverage.paths` | Reports: a path or a glob (`artifacts/**/lcov.info`), optionally `path=prefix`. One per line in the action, space separated on GitLab. Unset: config, else discovery. |
| `coverage-fail-under` | `REPOWISE_COVERAGE_FAIL_UNDER` | `--fail-under` | `coverage.fail_under` | Minimum patch coverage percent. Unset reports without gating. |
| `coverage-min-coverable-lines` | `REPOWISE_COVERAGE_MIN_COVERABLE_LINES` | `--min-coverable-lines` | `coverage.min_coverable_lines` | Small-change tolerance: a change with fewer changed executable lines than this never fails. |
| `coverage-fail-under-risky` | `REPOWISE_COVERAGE_FAIL_UNDER_RISKY` | `--fail-under-risky` | `coverage.fail_under_risky` | Minimum patch coverage percent over [risky files](#risk-weighted-patch-coverage) only. |
| `coverage-fail-under-branches` | `REPOWISE_COVERAGE_FAIL_UNDER_BRANCHES` | `--fail-under-branches` | `coverage.fail_under_branches` | Minimum percent of [branches taken](#branches-on-changed-lines) on changed lines. |
| `coverage-base-report` | `REPOWISE_COVERAGE_BASE_REPORT` | `--base-report` | none | Reports measured at the change's base commit, read like the report list. See [Project coverage](#project-coverage-and-coverage-outside-the-change). |
| `coverage-max-drop` | `REPOWISE_COVERAGE_MAX_DROP` | `--max-drop` | `coverage.max_drop` | Most project coverage may fall from the base, in percentage points. Needs a base report, or an index that stored coverage at the base. |

An empty action input or unset GitLab variable falls back to the config key.

The thresholds can live in config instead of the workflow:

```yaml
# .repowise/config.yaml
coverage:
  paths: [coverage/lcov.info]
  fail_under: 80
  min_coverable_lines: 5   # small-change tolerance
  fail_under_risky: 90     # optional, see Risk-weighted patch coverage
  fail_under_branches: 70  # optional, see Branches on changed lines
```

**Small-change tolerance.** With `min_coverable_lines` set, a change touching
fewer changed executable lines than that is still reported against the
threshold, but a miss reads "not applied" (a notice on GitHub) and exits `0`:
one uncovered line in a two-line fix should not block a merge. Like the
percentage, the count is of lines the report measures; a file the report does
not name adds nothing to it.

### Coverage reports per language

The gate needs the lines each file could have run, not only the lines it did.
A new file no test loads must still appear in the report, or it is listed as
"not in report" and left out of the figure. These commands do both:

| Stack | Produce the report | Pass |
|-------|--------------------|------|
| Python (pytest) | `pytest --cov=src --cov-report=lcov:coverage/lcov.info` (lcov paths are relative to the working directory) | `coverage/lcov.info` |
| Python (coverage.py) | `coverage lcov` or `coverage xml` after the run | `coverage.lcov`, `coverage.xml` |
| Go | `go test -coverprofile=coverage.out -coverpkg=./... ./...` (`-coverpkg` includes packages with no tests) | `coverage.out` |
| Java or Kotlin (Gradle) | `gradle test jacocoTestReport` with the XML report on | `build/reports/jacoco/test/jacocoTestReport.xml` |
| Java (Maven) | `mvn test jacoco:report` (`report-aggregate` for a multi-module build) | `target/site/jacoco/jacoco.xml` |
| JavaScript or TypeScript | `c8 --all --reporter=lcov`, or a test runner's `lcov` reporter with every source file included | `coverage/lcov.info` |
| Rust | `cargo llvm-cov --lcov --output-path coverage/lcov.info` | `coverage/lcov.info` |

- Several reports merge: a line any of them ran counts as run.
- A coverage.py `.coverage` database is not a text report; export it first.
- When report paths do not line up with the repository (a build directory, a
  container path), set `coverage.strip_prefix` or `coverage.path_prefix` in
  `.repowise/config.yaml`.
- The gate reports how many report paths matched the repository, and exits
  `2` when none did, so a wrong prefix never passes as zero.
- When more than half did not match, it suggests the prefix to set, and
  `repowise coverage add` stores nothing unless `--allow-partial` is passed.

### Monorepos and matrix jobs

A matrix of test jobs leaves one report per shard. `--report` (and each line
of `coverage-report`) takes a glob, relative to the working directory, so
`artifacts/**/lcov.info` reads them all; they merge hit-wins.

- `**` walks the tree the way discovery does, skipping dependency, cache and
  build directories (`node_modules`, `.venv`, `dist`, ...) and nested
  repositories.
- A job whose reports never arrived exits `2`: a literal path that does not
  exist, or a glob that matches nothing, cannot be evaluated.
- A glob still matches when one shard of several is missing. To catch that,
  list each shard's report on its own line.
- `coverage.paths` entries that match nothing are skipped.

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

**Per-report prefixes.** When one package's report names paths relative to
that package, give it a prefix: `--report web/coverage/lcov.info=web`.

- The whole argument is tried as a path or glob first, so
  `artifacts/shard=1/*.info` stays one. Only when it matches nothing is it
  split on the last `=`.
- In config the same is a mapping entry, and `paths` entries may be globs,
  relative to the repository root:

```yaml
coverage:
  paths:
    - {path: "web/coverage/lcov.info", path_prefix: web}
    - "services/*/coverage.xml"
  ignore: ["**/*_pb2.py", "gen/"]   # gitignore syntax
```

**`coverage.ignore`** leaves generated or vendored files out on both sides:

- Changed files it matches are dropped before measuring. The summary counts
  them as ignored, here and in the editor and agent views.
- Report entries it matches are not stored by `repowise coverage add` or
  indexing.
- When it leaves out every entry of the reports, the gate exits `2` and says
  so.

**Go modules in a subdirectory.** Go coverprofiles name files by import path.
When a module lives in a subdirectory (`backend/go.mod` declaring
`module example.com/m`), paths under `example.com/m/` map to `backend/` using
the `go.mod` files in the checkout, so files in the module's top-level package
match with no prefix. A per-report prefix or `coverage.path_prefix` turns the
mapping off.

### Path-scoped gates

One threshold over the whole change lets a well-tested package carry an
untested one. `coverage.gates` adds a gate per part of the repository, judged
on the changed files its globs match, with the same rules as the whole-change
gate.

```yaml
# .repowise/config.yaml
coverage:
  fail_under: 70                # the whole change
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

| Key | What it does |
|-----|--------------|
| `name` | Required and unique. |
| `paths` | Gitignore-syntax globs relative to the repository root. A leading `/` anchors one there (`/api/` is the top-level `api` directory, `api/` any directory of that name). `!` excludes. |
| `fail_under` | Minimum percent, 0-100. Without it the gate is reported but judges nothing. |
| `informational` | `true` reports the gate but never fails on it. |

**Verdict**

- A file can match several gates; each counts it.
- The change fails when the whole-change gate fails or any gate that is not
  `informational` fails.
- The headline then leads with the failing gate and its counts (`Fails:
  path-scoped gate api 60.0% (6 of 10 changed executable lines), below its
  85.0% gate.`) before the whole-change verdict.
- The summary adds a table of gates, failing ones first.
- On GitHub each failing gate is its own `::error::`; an informational gate
  below its threshold is a `::notice::`.
- The small-change tolerance is the whole change's: when the change has fewer
  changed executable lines than `min_coverable_lines`, every gate reads
  `too_small`, but a small slice of a big change is judged.
- An entry the config cannot use (no name, a repeated name, no paths or only
  `!` exclusions, a `fail_under` outside 0-100, an unknown key) makes the check
  exit `2` naming it.

**Unmeasured files.** Like the whole-change figure, a gate counts only changed
lines the report measures. A changed file it matches that the report does not
measure (a new file no test loads, a report without line data) is counted
apart, as `unmeasured_file_count`, and left out of the percentage. A gate over
only such files reads "no measured changed lines (2 changed files not
measured)" and fails nothing.

**Where the gates run**

- With one report per job, each job would judge the gates on a fragment of the
  change: run `coverage check` once over the combined reports instead, as in
  [Monorepos and matrix jobs](#monorepos-and-matrix-jobs).
- The gates live in the committed config, so the Action and the GitLab
  template take no input for them: every job reads the same gates.
- The editor, REST and agent views report the same verdicts when the stored
  coverage was measured at the change's head and the config is valid. On stale
  coverage, or beside an invalid entry (listed in `scope.config_errors`), their
  gates read `no_data`.

**Proposing gates.** To start, let Repowise propose some:

```bash
repowise coverage suggest-gates                # YAML to paste below coverage:
repowise coverage suggest-gates --format json
```

- It reads CODEOWNERS (one gate per owner), the top-level packages git tracks,
  and, when the repository is indexed, the graph's communities, labelling each
  source in a comment.
- The block is indented to paste directly below your `coverage:` line.
- It writes nothing and sets no `fail_under`: keep the gates you want and
  choose their thresholds.

### Risk-weighted patch coverage

An uncovered line in a file that keeps breaking matters more than one in a
file nothing depends on, so the report ranks changed files by risk.

- The table, the annotations and the summary list the riskiest files first,
  with a "Risk" column in plain words ("hotspot, bug-fix weight 3.2, 14
  dependents", "none known", or "unknown" when it could not be read).
- A file is risky when the index flags it a hotspot or a bug magnet.
- Without an index, or for a file the change adds, a file is risky when its
  recency-weighted bug-fix weight from git is in the top quartile of the files
  with bug-fix history.
- The report says which basis each row used when not every file had index
  data.
- With an index, each annotation also names the test file to extend when one
  is found ("Extend tests/test_auth.py (inferred: calls reach `login`)").

`--fail-under-risky` adds one more, stricter gate over the risky files' changed
lines alone, and the check fails when any gate fails.

- It follows the same rules as the others, the whole change's small-change
  tolerance included.
- A change that touches no risky file leaves it not applied (`no_data`, exit
  `0`).
- With the gate set, a shallow clone, or a measured file whose risk could not
  be read (no git history and no index row), exits `2`, unless the
  whole-change, a path-scoped or the branch gate already failed: that failure
  is reported.

### Branches on changed lines

When the report has per-line branch data (lcov `BRDA`, Cobertura
`condition-coverage`, JaCoCo branch counters, Clover `cond`), the check also
reports "Branches on changed lines 70.0% (7 of 10 taken; 2 lines partly
taken)". `--fail-under-branches P` gates the share.

- A partly taken line ran with a branch never taken: it gets a "Partly taken"
  column and, on GitHub, a "Partly taken branch" warning.
- Line and branch coverage are reported side by side, never blended.
- The small-change tolerance applies, and counts changed lines, not branches.
- No changed line with branches: "not measured", never 0%, and the gate is not
  applied (exit `0`).
- Coverage with no per-line branch data (a Go coverprofile, coverage.py without
  `--branch`, stored coverage ingested before branches were kept) exits `2`
  under the flag or action input, unless another gate already failed. From the
  config key alone it is a note with exit `0`.
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

## Gate on documentation drift

Fails when the documentation names files, links or commands that do not exist.

```yaml
- uses: repowise-dev/repowise@main
  with:
    checks: doc-drift
    doc-drift-baseline: .doc-drift-baseline.json
```

| Action input | GitLab variable | CLI flag | Default | What it does |
|--------------|-----------------|----------|---------|--------------|
| `doc-drift-baseline` | `REPOWISE_DOC_DRIFT_BASELINE` | `--baseline` | none | Committed baseline file; only findings it does not list fail. |
| `fail-on-baseline-growth` | none | `--fail-on-baseline-growth` | `false` | Fail doc drift and security when their baseline gains an entry over the change's base. |

- `--check` needs no index.
- `--kind symbol` adds symbol references (names of functions and classes a
  document mentions). It needs an index, so it exits `2` without one.
- Doc drift reads `git blame`. In a shallow clone every line blames to the
  boundary commit, so give that job `fetch-depth: 0`.

**Baseline.** On a repository whose documentation already drifted, the gate
fails from the first run. Record what is there once and fail only on new
drift:

```bash
repowise doc-drift --check --write-baseline .doc-drift-baseline.json
git add .doc-drift-baseline.json
```

Then pass `--baseline .doc-drift-baseline.json`. Entries are keyed on the
document, the reference and its target, so an edit above a finding does not
turn it back into a new one.

**Without a baseline.** `repowise doc-drift --check --since auto` judges only
what the change is answerable for: documents it edits, and documents naming
files it deletes or renames. It needs `fetch-depth: 0`.

## Gate on security

Fails when the change adds a secret or a risky call at or above a severity.

```yaml
- uses: repowise-dev/repowise@main
  with:
    checks: security
    security-fail-on: med
```

| Action input | GitLab variable | CLI flag | Default | What it does |
|--------------|-----------------|----------|---------|--------------|
| `security-fail-on` | `REPOWISE_SECURITY_FAIL_ON` | `--fail-on` | `high` | Lowest severity that fails: `high`, `med`, `low`. |
| `security-baseline` | `REPOWISE_SECURITY_BASELINE` | `--baseline` | none | Committed baseline file; only findings it does not list fail. |

- **Accepting a finding on purpose** (a documented test key, say): run
  `repowise security check --write-baseline .security-baseline.json` on that
  change to record it; `--baseline` accepts it from then on.
- **One false positive:** a `repowise-security-ignore` comment silences it
  ([details](../layers/SECURITY.md#silencing-one-finding-repowise-security-ignore)).
- `fail-on-baseline-growth` applies here too
  ([adopting the gates](#adopting-the-gates-on-an-existing-repository)).

## Gate on code health

Fails when the change writes new complex, deeply nested, oversized or
duplicated code.

```yaml
- uses: repowise-dev/repowise@main
  with:
    checks: health
    health-fail-at: medium
```

| Action input | GitLab variable | CLI flag | Default | What it does |
|--------------|-----------------|----------|---------|--------------|
| `health-fail-on` | `REPOWISE_HEALTH_FAIL_ON` | `--fail-on` | `introduced` | What fails: `introduced` (findings the change wrote), or `worsened` to fail on findings the change made worse too. |
| `health-fail-at` | `REPOWISE_HEALTH_FAIL_AT` | `--fail-at` | `high` | Lowest severity that fails: `medium`, `high`, `critical`. |

- Findings that do not fail the gate warn.
- On GitLab the health job is opt-in: set `REPOWISE_HEALTH: "true"`.
- Rules come from `.repowise/health-rules.json` at the change's base
  ([adopting the gates](#adopting-the-gates-on-an-existing-repository)).

## Gate on change risk

Fails when the change is bigger and more spread out than a percentile of the
repository's own recent commits.

```yaml
- uses: repowise-dev/repowise@main
  with:
    checks: risk
    risk-fail-above-percentile: 95
```

| Action input | GitLab variable | CLI flag | Default | What it does |
|--------------|-----------------|----------|---------|--------------|
| `risk-fail-above-percentile` | `REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` | `--fail-above-percentile` | none | With `risk` in `checks`, fail when the change ranks above this percentile of recent commits. Empty reports the rank without gating. On GitLab the risk job runs only when it is set. |

- It ranks by size and spread: a size gate, not a bug predictor.
- At a percentile P, roughly (100 - P)% of changes fail it by construction.
- A failure asks for a split or a second reviewer, not a code fix.
- There is no baseline to accept a failure with.
- Ranking needs at least 8 recent commits; fewer exits `2`.

## Code scanning (SARIF)

With `upload-sarif: true`, doc drift, security and health findings appear in
the repository's Security tab and on the pull request's diff.

```yaml
permissions:
  contents: read
  security-events: write
  actions: read          # private repositories only
steps:
  - uses: actions/checkout@v4
    with: {fetch-depth: 0}
  - uses: repowise-dev/repowise@main
    with:
      checks: doc-drift,security,health
      upload-sarif: true
```

- The job needs `security-events: write`, and `actions: read` in a private
  repository.
- A pull request from a fork runs with a read-only token, so the upload is
  skipped there with a warning. The gates still run and still decide the
  check.
- A gate that could not evaluate writes no SARIF file.
- Outside the action, `--format sarif` writes the same report.

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

It adds one job per gate on merge request pipelines. Each prints its markdown
report to the log and keeps it as an artifact. Override any job's `image`,
`rules` or `needs` in your own file as usual.

| Job | Runs when | Code Quality report |
|-----|-----------|---------------------|
| `repowise-coverage` | `REPOWISE_COVERAGE_REPORT` is set | none |
| `repowise-doc-drift` | every merge request pipeline | `gl-code-quality-doc-drift.json` |
| `repowise-security` | every merge request pipeline | `gl-code-quality-security.json` |
| `repowise-health` | `REPOWISE_HEALTH: "true"` | `gl-code-quality-health.json` |
| `repowise-risk` | `REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` is set | none |
| `repowise-impacted-tests` | `REPOWISE_IMPACTED_TESTS: "true"` ([test selection](#selecting-the-tests-a-change-needs)) | none |
| `repowise-code-quality` | on the default branch; never fails the pipeline | `gl-code-quality-doc-drift.json` |

- **Variables.** `REPOWISE_VERSION` pins the release. Each gate's variables are
  in its section's table: [coverage](#gate-on-coverage),
  [doc drift](#gate-on-documentation-drift), [security](#gate-on-security),
  [health](#gate-on-code-health), [risk](#gate-on-change-risk).
- `REPOWISE_COVERAGE_REPORT` and `REPOWISE_COVERAGE_BASE_REPORT` are space
  separated and may hold globs and `path=prefix` entries; Repowise expands the
  globs, not the shell.
- **Code Quality widget.** Findings show in the merge request's Code Quality
  widget, even when the gate fails the job.
- `repowise-code-quality` publishes the report the widget compares a merge
  request against, so only issues the merge request introduces show as new.
- It publishes doc drift only: security judges only what a change adds, and
  the default branch has no change to judge.

**Line coverage in the merge request diff.** That comes from your own test job,
not from Repowise: have your test runner write a Cobertura report (for example
with pytest-cov) and declare it there. The same `coverage.xml` can be your
`REPOWISE_COVERAGE_REPORT`.

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
repowise health check "origin/$CHANGE_TARGET...HEAD"
```

Bitbucket Pipelines and others work the same way with their own branch
variable; `--format markdown` or `json` gives you something to post.

## Selecting the tests a change needs

`repowise impacted-tests --format args` prints one line of arguments for a test
runner, or `:all` whenever any part of the answer is not known, with the
reasons on stderr.

- It needs an index, cached from the default branch (below).
- A per-test coverage map (`repowise coverage add`) makes it more precise.
- It exits `0` for a subset or for everything, and `2` only when it cannot read
  the change or the `tests.*` config.
- **Branch on the run-all flag, never on `:all`:** jest and vitest read `:all`
  as a pattern and run nothing.

| Action input | GitLab variable | CLI flag | Default | What it does |
|--------------|-----------------|----------|---------|--------------|
| `impacted-tests` | `REPOWISE_IMPACTED_TESTS` | none | `false` | Select the tests the change needs and set the `impacted-tests` and `run-all-tests` outputs. Needs a cached `.repowise` index. |
| `impacted-tests-runner` | `REPOWISE_IMPACTED_TESTS_RUNNER` | `--runner` | `auto` | `auto`, `pytest`, `go`, `jest` or `files`. |

**Fails closed.** In the action and the GitLab job, selection first brings a
restored index up to date with `repowise update --index-only`. When there is
no index, the update fails, or selection cannot run, the answer is run
everything, with the reason, and the step or job still passes.

### On GitHub Actions

Keep the index cached from the default branch and branch on the outputs in the
pull request job. With `checks: ""` and `impacted-tests: true` the action runs
no gate.

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
      - run: if [ -f .repowise/state.json ]; then repowise update --index-only; else repowise init --yes --no-prose; fi
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

### On GitLab

Set `REPOWISE_IMPACTED_TESTS: "true"` (and optionally
`REPOWISE_IMPACTED_TESTS_RUNNER`). The `repowise-impacted-tests` job writes the
arguments to `impacted-tests.txt` and `RUN_ALL_TESTS` to a dotenv report:

```yaml
repowise-index:
  image: python:3.12
  variables: {GIT_DEPTH: "0"}
  script:
    - pip install repowise
    - if [ -f .repowise/state.json ]; then repowise update --index-only; else repowise init --yes --no-prose; fi
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

### Options

| Option | What it does |
|--------|--------------|
| no range | In CI, reads the pull request's change; locally, the staged changes. |
| `--runner pytest`, `go` or `jest` | The arguments hold only that runner's tests, `tests.always_run` included. |
| `--runner files` | Every selected test, whatever its runner. |
| `--prioritize` | Prints the whole suite with the selection first, so a job can run the head with `-x` and the rest after. It skips nothing. |
| `--explain <test>` | Says why one test file was or was not selected: the first changed file that reached it, the evidence (coverage, call graph, import graph), the import route from the test to that file when there is one, or the rule that runs it. With `--format json` the answer is added as `explain`. |
| `tests.full_run_on` | Gitignore patterns that add run-everything paths. A path it matches is never scoped. |
| `tests.always_run` | Appended to every selection as written, for the runner whose language each entry is in. |

- Selected tests come likeliest to fail first
  ([run order](../reference/CLI_REFERENCE.md#repowise-impacted-tests-revspec)).
- A repository with tests in several languages needs one job per runner. The
  tests a job leaves out are counted on stderr, and listed per runner under
  `left_out` with `--format json`.

### What runs everything

Each of these makes the answer `:all`, with the cause in the reasons:

- An empty change.
- A Cargo, Make, CMake, Docker or CI file.
- Tool and runtime config: dotfiles, `.tool-versions`, root-level `*.toml`,
  `*.yaml` and `*.json`, shell scripts.
- `.repowise/config.yaml`.
- A path `tests.full_run_on` matches.
- A non-code file in a test tree.
- A changed file with no known test, or only a filename guess.
- A route through a test helper no test imports, unless every known test is
  selected anyway (the reasons then say so).
- Any Python test helper while a conftest or pytest config loads plugins by
  name.
- A missing or unreadable index.
- A per-test map at its row cap.
- A deleted file no known test used.
- A query or data file no code names.
- A [manifest](#what-a-changed-file-selects) named by more than 200 non-test
  files outside its scope, or named from a fixture tree no test code names.
- An index older than the change's base in the cases
  [below](#an-index-older-than-the-changes-base).
- A conftest case listed under [conftest changes](#conftest-changes).
- An index built before Repowise recorded which tests it cannot see into, until
  `repowise update` checks its tests.

### What a changed file selects

| Changed file | Selects |
|--------------|---------|
| A nested `package.json`, lockfile or `go.mod` | The tests under its directory and those reaching code there. |
| A root lockfile, manifest or test runner config; Python, Ruby, PHP, Gradle or Maven dependency files (pip requirements included); `tsconfig`; jest or vitest configs | Every test of that ecosystem. |
| A production package's `__init__.py` | The tests reaching any module under it. |
| A doc, template, query, stylesheet, image or nested data file | The tests reaching the code that names it, outside comments and docstrings. |
| A doc, template, stylesheet or image no code names | Its package's tests. Markdown, reST or AsciiDoc in no package, in a top-level directory without code, needs none. |
| A `conftest.py`, test package `__init__.py` or imported test helper | The tests under or importing it, not everything. |
| `docs/` or a root README-like file (README, CHANGELOG, LICENSE, CONTRIBUTING) no code names | Nothing. An empty line means only such files changed. |

**Manifests named from outside their scope.**

- Tests whose code names the file from outside that scope run too (a Python
  test reading `package.json`).
- A mention written with a directory names only the files its path can mean:
  `packages/web/package.json` does not name `packages/ui/package.json`.
- A bare name, `../package.json` or a path built from parts names every file of
  that name.
- A file under a fixture tree (`tests/fixtures/<name>/`) that names the
  changed file stands for the test code that names that tree or the fixtures directory. When none
  does, or code outside the test trees names the tree's path, everything runs.
- If more than 200 non-test files outside the scope name it, everything runs.

**Skipped files.** Only `docs/` and root README-like files are skipped, and
only when no code names them: a test that reads `README.md` names it, and then
runs.

**Wiring no import states counts as a use.**

- A JS/TS setup file named under `setupFiles`, `setupFilesAfterEnv`,
  `globalSetup` or `globalTeardown` in a `vitest.*`, `jest.*`, `vite.*` or
  `playwright.*` config or a `package.json` stands for the tests in that
  config's package.
- A helper a test names by a path relative to itself
  (`Path(__file__).parent / "server.py"`, `path.join(__dirname, "server.ts")`)
  stands for that test.
- A `conftest.py` links only the files pytest collects (its `python_files`), so
  a helper beside the tests is not on every route the conftest reaches.

### Tests that run with every subset

Tests the graph cannot see into run with every subset, and the reasons say how
many and why:

- A test not indexed, or importing nothing it resolves.
- A Python, JavaScript or TypeScript test that lists and reads files under a
  source directory: the working directory, a module's location, or its own
  location climbed out of the test directory. Never its fixtures, sibling data
  or a temporary directory.
- A test that runs the project's own command, `python -m` module, code
  importing the project, or package `bin` in a child process.
- A Java or Kotlin `@SpringBootTest`, which loads the whole application
  context. A slice such as `@WebMvcTest` is selected through its imports.

Notes:

- A test that inherits `@SpringBootTest` from a base class or carries it
  through a custom meta-annotation is not detected, and is selected through
  its imports only.
- The indexer finds these tests once from each test's source and stores the
  reason, so selection reads it without opening test files.
- An index built before this check runs everything until `repowise update`
  checks its tests.

### What counts as a test

- A Python module named like a test but outside every test directory is not a
  test when the nearest pytest config's `testpaths` or `python_files` leaves it
  out, as a bare `pytest` does. It is selected through like any other code.
- The rule follows a bare `pytest` run, so a test that CI only names explicitly on
  the command line outside `testpaths` is not seen as one: list it in
  `tests.always_run`.
- Inside a JavaScript or TypeScript `src` tree a `tests/` folder is not a test
  directory, since it often holds a product feature. A file there is a test
  when its name says so (`.test.`, `.spec.`) or a test directory such as
  `__tests__/` holds it.

### Tests counted apart

Some selected tests run but are listed and counted apart in the summary, with
`--format json` fields:

| Field | Holds |
|-------|-------|
| `selected.import_closure` | Tests the change reaches only through imports, more than one file away, with no call path, coverage or paired name. An import error breaks them, though nothing they call runs the change. |
| `selected.broadcast` | Tests a conftest adds that nothing else reaches (see [conftest changes](#conftest-changes)). |
| `selected.basis` | What drove each file's selection. |
| `selected.why` | The `--explain` answer for every selected test. |

### Conftest changes

A change that reaches a `conftest.py` only through its imports selects the
tests that ask for a fixture using that change (directly, through other
fixtures, `usefixtures` or `getfixturevalue`), plus one test under the conftest
as an import check.

- **Every test under the conftest runs** when an autouse fixture, a `pytest_*`
  hook or module-level code uses the change, or when the conftest imports it
  only for side effects. The tests this adds that nothing else reaches go in
  `selected.broadcast`.
- **Left to the import check:** patching an attribute of an imported module or
  of a class in it (`monkeypatch.setattr(mod.Cls, "name", ...)`) in an autouse
  fixture.
- **Reaches only its own module:** reading a literal constant of an imported
  module, or calling one of its functions that uses nothing the module imports
  beyond standard modules that cannot load code by name (emptying a
  `functools` cache or a standard container counts). Only a change in that
  module itself reaches it.
- A module re-exporting such a name (`from .console import NAMES as NAMES`)
  passes this through to the module defining it.
- **Runs everything** when a fixture request cannot be read from the code (a
  computed name, a mark kept in a variable, a config `usefixtures`), when the
  conftest loads plugins or cannot be read, and on an index built before this
  was recorded. The reasons name the cause.
- Ceiling: a function judged by the rule above that reads or runs repo files by
  path (`open`, `Path.read_text`, `os.system`) is judged by its imports only.
- Ceiling: a change that only alters what the changed module does at import
  time, seen by a test that does not import it, is caught by the import check
  alone.

### An index older than the change's base

An index built at an older commit than the change's base is used as is.

- Files changed in between whose imports or module-name strings moved add the
  tests reaching them. Outside Python and JavaScript/TypeScript, any change
  beyond whitespace counts as moved.
- CI config and test data changed in between do not force a full run: neither
  is imported.
- Everything runs instead when:
  - that commit is not in the clone;
  - more than 1,000 files changed in between;
  - one of them is a lockfile, manifest, build or test config, or
    `.repowise/config.yaml`.

### Ceilings

- A test that globs docs or templates in another package is not seen.
- On the JVM and .NET, a same-package test linked only by an unresolved call is
  missed.
- So is a test that reaches the change through a plugin or registry loaded by a
  string name, a tree walk or child process done inside a shared helper it
  calls, or one in another language.
- List such tests in `tests.always_run`, and run the full suite on the default
  branch.

### What to expect

The subset is only as small as the code is loosely coupled.

- On tightly coupled code the graph alone picks most tests (over 80% of test
  files on this repository), because most tests import something that imports
  the changed file.
- Real savings need a per-test map measured at the change's base.
- `selected.basis` in `--format json` shows what drove each file's selection.

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
