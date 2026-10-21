# Repowise in CI

Repowise gates judge a pull request on what the change adds, not on the
repository's past.

| Gate | Fails when | History needed |
|------|------------|----------------|
| `repowise coverage check` | tests did not run the changed lines (needs a coverage report) | merge-base with the target branch |
| `repowise coverage check --fail-under-branches P` | tests did not take the changed lines' branches (needs per-line branch data) | merge-base |
| `repowise coverage check --max-drop P` | project coverage fell more than P points (needs a report measured at the change's base) | merge-base |
| `repowise doc-drift --check` | docs name missing files, links or commands | none (full history only improves rename suggestions) |
| `repowise security check` | the change adds a secret or risky call | every commit of the change |
| `repowise health check` | the change adds complex, deeply nested, oversized or duplicated code | merge-base |
| `repowise risk --fail-above-percentile P` | the change is bigger and more spread out than P% of recent commits | recent commits to rank against (at least 8) |

- Gates need only git (no index, database, model or API key) and store nothing.
- Detail per layer: [test intelligence](../layers/TEST_INTELLIGENCE.md#patch-coverage-in-ci),
  [documentation drift](../layers/DOC_DRIFT.md#in-ci),
  [security](../layers/SECURITY.md#in-ci-repowise-security-check),
  [code health](../layers/CODE_HEALTH.md#in-ci-repowise-health-check),
  [change risk](../layers/CHANGE_RISK.md#in-ci).
- Not a gate, same pipeline: [selecting the tests a change needs](#selecting-the-tests-a-change-needs).

## What every gate does the same way

**Exit codes**

- `0`: passed, or nothing to judge.
- `1`: the change failed the gate.
- `2`: could not evaluate (not a git repository, unknown revision, no
  merge-base in a shallow clone, unreadable report or baseline, too few recent
  commits to rank against). A setup problem never reads as a pass; fixes are in
  [When a gate exits 2](#when-a-gate-exits-2).

**Formats** (`--format`)

- `table` (default) for a terminal, `json` for scripts, `markdown` for a
  comment or artifact.
- `github`: up to ten annotations on changed lines, a notice counting the
  rest, and the markdown report in the job summary.
- Doc drift, security and health only: `sarif` for code scanning, and `gitlab`,
  a Code Quality JSON issue list without baseline-accepted findings. A gate
  that cannot evaluate still prints `[]`, and the GitLab template writes `[]`
  when output does not parse.

**The change being judged**

- Coverage, security, health and risk take a range: `origin/main...HEAD`
  (three dots: what the branch did since it forked, the pull request's view),
  `base..head`, or one commit.
- Without one they read the target branch from `GITHUB_BASE_REF`,
  `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `CHANGE_TARGET` or
  `BITBUCKET_PR_DESTINATION_BRANCH`, else the remote's default branch.
- `risk` does that only with `--fail-above-percentile` or `--format markdown` /
  `github`; otherwise no range means your uncommitted work, else `HEAD`.
- Doc drift takes no range: it checks the whole working tree.
- Per-CI variables, Azure Pipelines included: [GIT_HOSTS.md](GIT_HOSTS.md#ci-systems).

**History.** A shallow checkout has no merge-base, so gates exit `2` rather
than guess: fetch full history (`fetch-depth: 0` on GitHub, `GIT_DEPTH: 0` on GitLab) and the
target branch as `origin/<branch>`.

## Adopting the gates on an existing repository

Coverage, security and health judge only what a change adds, so old untested
files and old findings never fail a new pull request. Doc drift checks the
whole tree, so a repository with drift needs a [baseline](#gate-on-documentation-drift) first.

- `--fail-on-baseline-growth` (input `fail-on-baseline-growth`) keeps a
  baseline shrink-only: doc drift or security fails when the baseline lists a
  finding the same file at the change's base does not, so re-saving it cannot
  hide new findings. A baseline new in the change passes.
- The health gate reads `.repowise/health-rules.json` as the change's base has
  it, so a rule a change adds or loosens applies from the next change on.

## GitHub Actions

The repository is an action: it installs Repowise, runs every chosen gate, then
fails the step with `1` if any failed, else `2` if any could not evaluate.
Snippets below are `with:` lines for this workflow:

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

- Pin a release tag, not `main`; `version:` pins the Repowise release.
- With a shallow checkout, the action fetches the history and target branch
  that coverage, security, health and risk need; `fetch-depth: 0` skips that.

| Input | Default | What it does |
|-------|---------|--------------|
| `checks` | `doc-drift,security` | Gates to run, separated by commas, spaces or newlines. Coverage needs a report, and health and risk are policy choices, so all three are opt-in. An unknown name exits `2`. |
| `version` | latest | Repowise version from PyPI. |
| `python-version` | `3.12` | Python to run it (3.11 or newer). |
| `working-directory` | `.` | Where to run from. |
| `base` | from the pull request | Range for coverage, security, health, risk and test selection; empty reads the target branch, else the default branch. |
| `upload-sarif` | `false` | Upload findings to [code scanning](#code-scanning-sarif). |
| `comment` | `false` | Post one pull-request comment summarizing every gate, updated in place on each run. |
| `github-token` | `github.token` | Token the summary comment is posted with; a later run edits only the comment this token's own account posted. |

Gate inputs are in each gate's section.

| Output | Holds |
|--------|-------|
| `coverage`, `doc-drift`, `security`, `health`, `risk` | each gate's exit code; empty when not run |
| `verdict` | `fail` when any gate failed or could not evaluate, `warn` when any passed with warnings, else `pass`; a gate that exits 0 without leaving a result (an older Repowise) counts as `warn` |
| `sarif-dir` | the SARIF directory, when `upload-sarif` is on and a gate wrote findings |
| `impacted-tests` | runner arguments for the selected tests, or `:all` |
| `run-all-tests` | `false` only when that subset is safe alone; `true` otherwise, also when selection could not run |

**Summary comment.** The job summary ends with a table of every gate and its
result. With `comment: true` the same table, plus each gate's findings, goes
into one pull-request comment that later runs edit rather than repeat; the job
needs `pull-requests: write`. Each working directory keeps its own comment.
Each finding carries a hidden
`repowise-fingerprint:v1` marker with its line-free id, so the comment counts
what a push resolved.

**Without the action**, run each gate as a step after `pip install repowise`,
with `--format github` (commands as in [Other CI systems](#other-ci-systems)).
On a pull request the gates read `GITHUB_BASE_REF` themselves; a `push`
event has none: pass the range, e.g.
`"$BEFORE..$GITHUB_SHA"` with `BEFORE: ${{ github.event.before }}` under `env:`.

## GitLab

Include the template and set variables for the gates you use:

```yaml
include:
  - remote: https://raw.githubusercontent.com/repowise-dev/repowise/main/ci/gitlab/repowise.gitlab-ci.yml

variables:
  REPOWISE_COVERAGE_REPORT: coverage/lcov.info
  REPOWISE_COVERAGE_FAIL_UNDER: "80"

repowise-coverage:
  needs: [test]   # the job that writes the report as an artifact
```

Each job logs its markdown report and keeps it as an artifact; override
`image`, `rules` or `needs` in your own file.

| Job | Runs on | Code Quality report |
|-----|---------|---------------------|
| `repowise-coverage` | merge requests, when `REPOWISE_COVERAGE_REPORT` is set | none |
| `repowise-doc-drift` | every merge request | `gl-code-quality-doc-drift.json` |
| `repowise-security` | every merge request | `gl-code-quality-security.json` |
| `repowise-health` | merge requests, when `REPOWISE_HEALTH: "true"` | `gl-code-quality-health.json` |
| `repowise-risk` | merge requests, when `REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` is set | none |
| `repowise-impacted-tests` | merge requests, when `REPOWISE_IMPACTED_TESTS: "true"` | none |
| `repowise-code-quality` | the default branch; never fails the pipeline | `gl-code-quality-doc-drift.json` |

- `REPOWISE_VERSION` pins the release; gate variables are in each gate's table.
- Report lists (`REPOWISE_COVERAGE_REPORT`, `REPOWISE_COVERAGE_BASE_REPORT`)
  are space separated and may hold globs and `path=prefix`; Repowise expands
  the globs, not the shell.
- Findings show in the merge request's Code Quality widget, even when the gate
  fails the job.
- `repowise-code-quality` publishes the report the widget compares against, so
  only new issues show as new. It covers doc drift only: security judges only
  what a change adds, and the default branch has no change.

<details><summary>Line coverage in the merge request diff</summary>

That comes from your test job, not Repowise: write a Cobertura report (e.g.
with pytest-cov) and declare it. The same `coverage.xml` can be your
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

</details>

## Other CI systems

Fetch the target branch, run the commands, and let the exit code fail the
build. Jenkins multibranch:

```bash
git fetch --no-tags origin "+refs/heads/$CHANGE_TARGET:refs/remotes/origin/$CHANGE_TARGET"
pip install repowise
repowise coverage check "origin/$CHANGE_TARGET...HEAD" --report coverage/lcov.info --fail-under 80
repowise doc-drift --check
repowise security check "origin/$CHANGE_TARGET...HEAD"
repowise health check "origin/$CHANGE_TARGET...HEAD"
repowise risk "origin/$CHANGE_TARGET...HEAD" --fail-above-percentile 95
```

Bitbucket Pipelines and others use their own branch variable; `--format
markdown` or `json` gives something to post.

## Gate on coverage

The [shared workflow](#github-actions) shows the inputs in use.

| Input | GitLab variable | Flag / config key | What it does |
|-------|-----------------|-------------------|--------------|
| `coverage-report` | `REPOWISE_COVERAGE_REPORT` | `--report` / `coverage.paths` | Reports: path or glob, optionally `path=prefix`; one per line in the action. Unset: config, else discovery. |
| `coverage-fail-under` | `REPOWISE_COVERAGE_FAIL_UNDER` | `--fail-under` / `coverage.fail_under` | Minimum patch coverage percent; unset reports without gating. |
| `coverage-min-coverable-lines` | `REPOWISE_COVERAGE_MIN_COVERABLE_LINES` | `--min-coverable-lines` / `coverage.min_coverable_lines` | [Small-change tolerance](#small-change-tolerance). |
| `coverage-fail-under-risky` | `REPOWISE_COVERAGE_FAIL_UNDER_RISKY` | `--fail-under-risky` / `coverage.fail_under_risky` | Minimum over [risky files](#risk-weighted-patch-coverage). |
| `coverage-fail-under-branches` | `REPOWISE_COVERAGE_FAIL_UNDER_BRANCHES` | `--fail-under-branches` / `coverage.fail_under_branches` | Minimum [branches taken](#branches-on-changed-lines) on changed lines. |
| `coverage-base-report` | `REPOWISE_COVERAGE_BASE_REPORT` | `--base-report` | Reports measured at the change's base ([project coverage](#project-coverage-and-coverage-outside-the-change)). |
| `coverage-max-drop` | `REPOWISE_COVERAGE_MAX_DROP` | `--max-drop` / `coverage.max_drop` | Most project coverage may fall, in points; needs a base report or an index that stored coverage at the base. |

An empty input or unset variable falls back to the config key in
`.repowise/config.yaml` ([keys](../reference/CONFIG.md#the-coverage-block)).

### Small-change tolerance

Below `min_coverable_lines` changed executable lines, a miss reads "not
applied" (a GitHub notice) and exits `0`: one uncovered line in a two-line fix
should not block a merge. It counts only lines the report measures.

### Coverage reports per language

Reports must list every runnable line, including new files no test loads
(otherwise "not in report" and left out):

| Stack | Produce the report | Pass |
|-------|--------------------|------|
| Python (pytest) | `pytest --cov=src --cov-report=lcov:coverage/lcov.info` (paths relative to the working directory) | `coverage/lcov.info` |
| Python (coverage.py) | `coverage lcov` or `coverage xml` after the run | `coverage.lcov`, `coverage.xml` |
| Go | `go test -coverprofile=coverage.out -coverpkg=./... ./...` (`-coverpkg` includes packages with no tests) | `coverage.out` |
| Java or Kotlin (Gradle) | `gradle test jacocoTestReport` with the XML report on | `build/reports/jacoco/test/jacocoTestReport.xml` |
| Java (Maven) | `mvn test jacoco:report` (`report-aggregate` for multi-module) | `target/site/jacoco/jacoco.xml` |
| JavaScript or TypeScript | `c8 --all --reporter=lcov`, or a runner's `lcov` reporter including every source file | `coverage/lcov.info` |
| Rust | `cargo llvm-cov --lcov --output-path coverage/lcov.info` | `coverage/lcov.info` |

- Several reports merge: a line any of them ran counts as run.
- Export a coverage.py `.coverage` database first; it is not a text report.
- Paths that do not line up (a build directory, a container path): set
  `coverage.strip_prefix` or `coverage.path_prefix`.
- The gate reports how many report paths matched. None: exit `2`. More than
  half unmatched: it suggests a prefix, and `repowise coverage add` stores
  nothing without `--allow-partial`.

### Monorepos and matrix jobs

`--report` and each `coverage-report` line take a glob relative to the working
directory (`artifacts/**/lcov.info`); matches merge hit-wins.

- `**` skips dependency, cache and build directories (`node_modules`, `.venv`,
  `dist`, ...) and nested repositories, as discovery does.
- A missing literal path, or a glob matching nothing, exits `2`. A glob still
  matches with one shard missing: list each shard on its own line to catch it.
- `coverage.paths` entries matching nothing are skipped.
- Per-report prefix: `--report web/coverage/lcov.info=web`. The whole argument
  is tried as a path first (`artifacts/shard=1/*.info` stays one), then split
  on the last `=`.
- In config, `paths` entries may be globs relative to the repo root, or
  mappings:

```yaml
coverage:
  paths:
    - {path: "web/coverage/lcov.info", path_prefix: web}
    - "services/*/coverage.xml"
  ignore: ["**/*_pb2.py", "gen/"]   # gitignore syntax
```

- `coverage.ignore` drops matching changed files before measuring (counted as
  ignored, here and in editor and agent views) and matching report entries
  from `repowise coverage add` and indexing. Ignoring every entry exits `2`.
- Go coverprofiles name import paths. A module in a subdirectory
  (`backend/go.mod` declaring `module example.com/m`) maps `example.com/m/` to
  `backend/` from the checkout's `go.mod` files; a per-report prefix or
  `coverage.path_prefix` turns that off.

<details><summary>Matrix examples (GitHub Actions and GitLab)</summary>

Each shard uploads its report; the gate job downloads them all:

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

On GitLab, give each parallel shard its own path:

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

</details>

### Path-scoped gates

`coverage.gates` judges parts of the repository separately, so a well-tested
package cannot carry an untested one. Each gate covers the changed files its
globs match, with the whole-change rules.

```yaml
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
| `name` | Required, unique. |
| `paths` | Gitignore globs from the repo root; a leading `/` anchors (`/api/` top-level only, `api/` anywhere); `!` excludes. |
| `fail_under` | Percent 0-100; without it the gate is reported but judges nothing. |
| `informational` | Reported, never fails. |

- A file matching several gates counts in each.
- The change fails if the whole-change gate or any non-informational gate
  fails. The headline leads with it (`Fails: path-scoped gate api 60.0% (6 of
  10 changed executable lines), below its 85.0% gate.`), the summary lists
  gates failing first, and GitHub gets an `::error::` per failing gate and a
  `::notice::` per informational one below threshold.
- The small-change tolerance is the whole change's: a small change reads
  `too_small` in every gate; a small slice of a big change is judged.
- An unusable entry (no or repeated name, no paths or only `!` ones,
  `fail_under` outside 0-100, an unknown key) exits `2` naming it.
- Changed files the report does not measure (new and unloaded, or no line data)
  count apart as `unmeasured_file_count`; a gate with only those reads "no
  measured changed lines (2 changed files not measured)" and fails nothing.
- Run `coverage check` once over combined reports, not per job, or each job
  judges a fragment ([matrix jobs](#monorepos-and-matrix-jobs)).
- Gates live in committed config, so the Action and GitLab template take no
  input for them.
- Editor, REST and agent views match when stored coverage was measured at the
  change's head and the config is valid; otherwise gates read `no_data` (invalid
  entries listed in `scope.config_errors`).

`repowise coverage suggest-gates` (or `--format json`) proposes gates from
CODEOWNERS (one per owner), top-level tracked packages and, when indexed, graph
communities, labelling each source. It prints YAML indented to paste below
`coverage:`, writes nothing and sets no `fail_under`.

### Risk-weighted patch coverage

The table, annotations and summary list the riskiest changed files first, with
a "Risk" column ("hotspot, bug-fix weight 3.2, 14 dependents", "none known",
or "unknown").

- Risky: an index hotspot or bug magnet; without an index, or for a new file,
  top quartile of recency-weighted bug-fix weight among files with bug-fix
  history. The report names the basis when rows differ.
- With an index, annotations name a test to extend ("Extend tests/test_auth.py
  (inferred: calls reach `login`)").
- `--fail-under-risky` gates the risky files' changed lines alone, with the
  same rules and small-change tolerance. No risky file: `no_data`, exit `0`.
- With it set, a shallow clone, or a measured file whose risk could not be read
  (no git history, no index row), exits `2` unless another gate already failed.

### Branches on changed lines

With per-line branch data (lcov `BRDA`, Cobertura `condition-coverage`, JaCoCo
branch counters, Clover `cond`) the check adds "Branches on changed lines 70.0%
(7 of 10 taken; 2 lines partly taken)".

- A partly taken line (a branch never taken) gets a "Partly taken" column and a
  GitHub "Partly taken branch" warning.
- Line and branch coverage are reported side by side, never blended.
- The small-change tolerance counts changed lines, not branches.
- No changed line with branches: "not measured", never 0%, exit `0`.
- No branch data (Go coverprofile, coverage.py without `--branch`, coverage
  stored before branches were kept): exit `2` under the flag or input unless
  another gate failed; only a note from the config key.
- Reports merge per line by most branches taken and seen, so the share is a
  lower bound.

### Project coverage and coverage outside the change

Patch coverage misses deleted tests or code paths others used. With a base
report, the check adds project coverage at base and head, and lists files whose
coverage changed outside the change, with the test or dependent that explains it.

```bash
repowise coverage check origin/main...HEAD --report coverage/lcov.info \
  --base-report base/lcov.info --max-drop 0.5
```

- The small-change tolerance does not apply to `--max-drop`.
- Measure the base report at the merge-base (`A` for `A..B`); files whose lines
  do not line up are left out, with a count.
- Measure both sides the same way: same reports of each format, same
  `coverage.ignore`, neither mapping under half its files.
- An unusable base exits `2` under `--max-drop` or `--base-report`; from
  `coverage.max_drop` alone it is a note.

<details><summary>Base reports on GitHub Actions and GitLab</summary>

GitHub Actions, one job on default-branch pushes and pull requests,
`fetch-depth: 0`:

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

GitLab uses the target branch's latest `test` artifact only when it was
measured at the merge-base (a rebased merge request, or merged-results
pipelines); otherwise the check runs without a base:

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

</details>

## Gate on documentation drift

```yaml
checks: doc-drift
doc-drift-baseline: .doc-drift-baseline.json
```

| Input | GitLab variable | Flag | What it does |
|-------|-----------------|------|--------------|
| `doc-drift-baseline` | `REPOWISE_DOC_DRIFT_BASELINE` | `--baseline` | Committed baseline; only findings it lacks fail. |
| `fail-on-baseline-growth` | none | `--fail-on-baseline-growth` | [Shrink-only baseline](#adopting-the-gates-on-an-existing-repository), for doc drift and security; default `false`. |

Record existing drift once, then pass `--baseline`:

```bash
repowise doc-drift --check --write-baseline .doc-drift-baseline.json
git add .doc-drift-baseline.json
```

- Entries key on document, reference and target, so an edit above a finding
  does not make it new.
- No baseline: `--since auto` judges only documents the change edits and
  documents naming files it deletes or renames (needs `fetch-depth: 0`).
- `--kind symbol` adds function and class names a document mentions; it needs
  an index and exits `2` without one.
- Doc drift reads `git blame`, which a shallow clone blames to the boundary
  commit: use `fetch-depth: 0`.

## Gate on security

```yaml
checks: security
security-fail-on: med
```

| Input | GitLab variable | Flag | What it does |
|-------|-----------------|------|--------------|
| `security-fail-on` | `REPOWISE_SECURITY_FAIL_ON` | `--fail-on` | Lowest failing severity: `high` (default), `med`, `low`. |
| `security-baseline` | `REPOWISE_SECURITY_BASELINE` | `--baseline` | Committed baseline; only findings it lacks fail. |

- Accept a deliberate finding (a documented test key): run
  `repowise security check --write-baseline .security-baseline.json` on that
  change, then pass `--baseline`.
- Silence one false positive with a `repowise-security-ignore` comment
  ([details](../layers/SECURITY.md#silencing-one-finding-repowise-security-ignore)).

## Gate on code health

```yaml
checks: health
health-fail-at: medium
```

| Input | GitLab variable | Flag | What it does |
|-------|-----------------|------|--------------|
| `health-fail-on` | `REPOWISE_HEALTH_FAIL_ON` | `--fail-on` | `introduced` (default; findings the change wrote) or `worsened` (also findings it made worse). |
| `health-fail-at` | `REPOWISE_HEALTH_FAIL_AT` | `--fail-at` | Lowest failing severity: `medium`, `high` (default), `critical`. |

Findings that do not fail warn. On GitLab, set `REPOWISE_HEALTH: "true"`.

### Before an agent commits or pushes

`repowise hook health-gate install` runs the same check when Claude Code runs
`git commit` (on the staged change) or `git push` (on `@{u}...HEAD`, else the
default branch's merge-base) in this repo, and blocks the command when the gate
fails. It is off by default, and `init` and `upgrade` never turn it on.
Anything that stops the check from running within 20 s lets the command
through; `repowise hook health-gate uninstall` turns it off.

## Gate on change risk

The [shared workflow](#github-actions) shows the input in use.

| Input | GitLab variable | Flag | What it does |
|-------|-----------------|------|--------------|
| `risk-fail-above-percentile` | `REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` | `--fail-above-percentile` | Fail above this percentile of recent commits; empty reports the rank only. The GitLab job runs only when set. |

- A size-and-spread gate, not a bug predictor: at percentile P roughly
  (100 - P)% of changes fail by construction.
- A failure asks for a split or a second reviewer, not a code fix; no baseline
  accepts it.

## Code scanning (SARIF)

```yaml
permissions:
  contents: read
  security-events: write
  actions: read          # private repositories only
# ...
        with:
          checks: doc-drift,security,health
          upload-sarif: true
```

- Doc drift, security and health findings appear in the Security tab and on the
  pull request diff.
- A fork's pull request has a read-only token: the upload is skipped with a
  warning, and the gates still decide the check.
- A gate that could not evaluate writes no SARIF file.

## Selecting the tests a change needs

`repowise impacted-tests --format args` prints one line of runner arguments,
or `:all` when any part of the answer is unknown, with reasons on stderr.

- It needs an index cached from the default branch; a per-test coverage map
  (`repowise coverage add`) makes it more precise.
- Exit `0` for a subset or everything; `2` only when it cannot read the change
  or the `tests.*` config.
- **Branch on the run-all flag, never on `:all`:** jest and vitest read `:all`
  as a pattern and run nothing.

| Input | GitLab variable | Flag | What it does |
|-------|-----------------|------|--------------|
| `impacted-tests` | `REPOWISE_IMPACTED_TESTS` | none | Default `false`. Select tests and set the `impacted-tests` and `run-all-tests` outputs. |
| `impacted-tests-runner` | `REPOWISE_IMPACTED_TESTS_RUNNER` | `--runner` | `auto` (default), `pytest`, `go`, `jest` or `files`. |

The action and GitLab job first run `repowise update --index-only` on a
restored index. If that fails, or selection cannot run, the answer is run
everything with the reason, and the step still passes.

<details><summary>Workflows: cache the index and branch on the outputs</summary>

GitHub Actions. With `checks: ""` the action runs no gate.

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

GitLab: `repowise-impacted-tests` writes the arguments to
`impacted-tests.txt` and `RUN_ALL_TESTS` to a dotenv report.

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

</details>

### Options

Full flag reference: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-impacted-tests-revspec).

| Option | What it does |
|--------|--------------|
| no range | CI: the pull request's change; locally: staged changes. |
| `--runner pytest`, `go`, `jest` | Only that runner's tests, `tests.always_run` included. Several languages need one job per runner; left-out tests are counted on stderr and listed per runner under `left_out` in JSON. |
| `--runner files` | Every test. |
| `--prioritize` | The whole suite, selection first, so a job runs the head with `-x` and the rest after; skips nothing. Selected tests always come likeliest-to-fail first. |
| `--explain <test>` | Why one test was or was not selected: first changed file reaching it, evidence (coverage, call graph, import graph), the import route, or the rule. JSON adds it as `explain`. |
| `tests.full_run_on` | Gitignore patterns that run everything; a matching path is never scoped. |
| `tests.always_run` | Appended to every selection as written, for the runner of each entry's language. |

### What runs everything

- An empty change.
- A Cargo, Make, CMake, Docker or CI file.
- Tool and runtime config: dotfiles, `.tool-versions`, root-level `*.toml`,
  `*.yaml`, `*.json`, shell scripts.
- `.repowise/config.yaml`, or a path `tests.full_run_on` matches.
- A non-code file in a test tree.
- A file with no known test, or only a filename guess.
- A route through a test helper no test imports, unless every known test is
  selected anyway (the reasons say so).
- Any Python test helper while a conftest or pytest config loads plugins by
  name.
- A missing or unreadable index, or a per-test map at its row cap.
- A deleted file no known test used.
- A query or data file no code names.
- A manifest named by over 200 non-test files outside its scope.
- The index and conftest cases below.

### What a changed file selects

| Changed file | Selects |
|--------------|---------|
| nested `package.json`, lockfile or `go.mod` | tests under its directory and those reaching code there |
| root lockfile, manifest or runner config; Python, Ruby, PHP, Gradle or Maven dependency files (pip requirements included); `tsconfig`; jest or vitest config | its whole ecosystem |
| production package `__init__.py` | tests reaching any module under it |
| doc, template, query, stylesheet, image or nested data file | tests reaching code that names it (outside comments and docstrings) |
| doc, template, stylesheet or image no code names | its package's tests; Markdown, reST or AsciiDoc in no package and a top-level directory without code needs none |
| `conftest.py`, test package `__init__.py`, imported test helper | tests under or importing it |
| `docs/` or root README, CHANGELOG, LICENSE, CONTRIBUTING no code names | nothing (an empty line when only these changed); a test reading `README.md` names it |

- Tests naming a manifest from outside its scope run too (a Python test reading
  `package.json`). A path with a directory names only the files it can mean
  (`packages/web/package.json` is not `packages/ui/package.json`); a bare name,
  `../package.json` or a built path names every file of that name.
- A namer under a fixture tree (`tests/fixtures/<name>/`) stands for test code
  naming that tree or the fixtures directory; when none does, or code outside
  the test trees names the tree's path, everything runs.
- Wiring counts as a use: a JS/TS file under `setupFiles`,
  `setupFilesAfterEnv`, `globalSetup` or `globalTeardown` in a `vitest.*`,
  `jest.*`, `vite.*`, `playwright.*` config or `package.json` stands for that
  package's tests; a helper a test names by relative path
  (`Path(__file__).parent / "server.py"`, `path.join(__dirname, "server.ts")`)
  stands for that test.
- A `conftest.py` links only files pytest collects (`python_files`), so a
  helper beside the tests is not on every conftest route.

### Tests that always run

Tests the graph cannot see into join every subset, with a count and reason
(recorded once per test at index time,
so selection opens no test file):

- not indexed, or importing nothing resolvable;
- Python, JavaScript or TypeScript tests listing and reading files under a
  source directory (working directory, a module's location, or their own
  location climbed out of the test directory; never fixtures, sibling data or
  temp directories);
- tests running the project's command, `python -m` module, project-importing
  code or package `bin` in a child process;
- Java or Kotlin `@SpringBootTest` (whole context); slices like `@WebMvcTest`
  go through imports.

<details><summary>Edge cases</summary>

- `@SpringBootTest` inherited from a base class or carried by a custom
  meta-annotation is not detected; such tests are selected through imports.
- An index built before this check runs everything until `repowise update`
  checks its tests.
- A Python module named like a test outside every test directory is not a test
  when the nearest pytest config's `testpaths` or `python_files` excludes it,
  as a bare `pytest` does. A test CI only names explicitly outside `testpaths`
  is therefore not seen: list it in `tests.always_run`.
- In a JavaScript or TypeScript `src` tree, `tests/` is not a test directory
  (it often holds a feature): a file there is a test only when named `.test.`
  or `.spec.`, or held by a test directory such as `__tests__/`.

</details>

### Counted apart

These run, but the summary counts them apart and JSON lists them:

| Field | Holds |
|-------|-------|
| `selected.import_closure` | tests reached only through imports, over one file away, with no call path, coverage or paired name: an import error breaks them, though nothing they call runs the change |
| `selected.broadcast` | tests a conftest adds that nothing else reaches |
| `selected.basis` | what drove each selection |
| `selected.why` | the `--explain` answer per selected test |

### Conftest changes

A change reaching a `conftest.py` only through imports selects tests asking for
a fixture that uses it (directly, via other fixtures, `usefixtures` or
`getfixturevalue`), plus one test under the conftest as an import check.

- Every test under it runs when an autouse fixture, a `pytest_*` hook or
  module-level code uses the change, or the conftest imports it only for side
  effects (`selected.broadcast`).
- Everything runs when a fixture request is unreadable (computed name, mark in
  a variable, config `usefixtures`), the conftest loads plugins or is
  unreadable, or the index predates this; the reasons name the cause.

<details><summary>Edge cases and ceilings</summary>

- `monkeypatch.setattr(mod.Cls, "name", ...)` on an imported module or its
  class in an autouse fixture is left to the import check.
- Reading an imported module's literal constant, or calling its function that
  uses nothing it imports beyond standard modules that cannot load code by name
  (emptying a `functools` cache or standard container counts), reaches only a
  change in that module. A re-export (`from .console import NAMES as NAMES`)
  passes this to the defining module.
- Ceiling: such a function reading or running repo files by path (`open`,
  `Path.read_text`, `os.system`) is judged by its imports only.
- Ceiling: a change altering only import-time behaviour, seen by a test not
  importing it, is caught by the import check alone.

</details>

### An index older than the change's base

It is used as is. Files changed in between whose imports or module-name
strings moved (outside Python and JavaScript/TypeScript, any non-whitespace
change) add the tests reaching them; CI config and test data do not, since
neither is imported. Everything runs when that commit is not in the clone,
over 1,000 files changed in between, or one is a lockfile, manifest, build or
test config, or `.repowise/config.yaml`.

### Ceilings and expectations

- Not seen: a test globbing docs or templates in another package; on the JVM
  and .NET, a same-package test linked only by an unresolved call; a test
  reaching the change through a string-loaded plugin or registry, a tree walk
  or child process in a shared helper, or another language. List those in
  `tests.always_run` and run the full suite on the default branch.
- The subset is only as small as the code is loosely coupled: on this
  repository the graph alone picks over 80% of test files, since most tests
  import something importing the change. Real savings need a per-test map
  measured at the change's base; `selected.basis` shows what drove each pick.

## When a gate exits 2

- "Could not diff ...: ... A shallow CI clone needs the base branch and enough history for a merge-base": fetch full history.
- "Could not tell which branch this change targets. Pass REVSPEC, e.g. origin/main...HEAD.": fetch the target branch, or pass the range.
- "No coverage report found. Pass one with --report ...": pass `--report`, or set `coverage.paths`.
- "--report ...: no coverage report matches ...": fix the path or glob, or upload the report from the test job.
- "coverage.gates[N] ...: ...": fix that entry of `coverage.gates`.
- "All N report entries match coverage.ignore, so nothing is left to measure.": narrow `coverage.ignore`.
- "No report path matched a file in this repository.": set `coverage.strip_prefix` or `coverage.path_prefix`.
- "cannot read baseline ..." or "baseline ... is not valid JSON": commit the file, or drop `--baseline`.
- "Only N recent commits could be sampled; ranking a change needs at least 8." (risk): fetch full history.
- "The change-risk gate ranks the change against recent commits, and --baseline 0 turns that off.": drop `--baseline 0`.
- "The risky-file gate reads bug-fix history, and this clone is shallow.": fetch full history.
- "Could not read the risk of N changed files, so the risky-file gate cannot run.": fetch full history, or index the repository.
- "The coverage report has no per-line branch data, so --fail-under-branches cannot be judged.": turn on branch coverage, or drop the flag.
- "The stored coverage predates per-line branch data ...": re-run `repowise coverage add` with the report.
- "The max-drop gate needs coverage measured at the change's base ...": pass `--base-report`, or ingest a report at the base commit.
- "Project coverage cannot be compared ...": measure base and head the same way (same full reports, same `coverage.ignore`).
- "Base report: No report path matched a file in this repository.": the base report must name files the base commit tracked; check its prefix.
