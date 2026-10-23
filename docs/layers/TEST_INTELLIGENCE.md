# Test Intelligence

Test intelligence answers two questions your CI cannot: which files are risky *and*
untested, and which tests a given change exercises. The second turns a 4,000-test suite
into the few dozen tests that guard the change you just made. **Measured** answers come
from an ingested coverage report; **inferred** ones from the dependency graph, always
labelled and never a percentage. No LLM key, no network: ingesting is a parse and a
database write, every later question an index lookup, and the pull-request gate needs
only git and a report ([Patch coverage in CI](#patch-coverage-in-ci)).

How the inferred tier works: [architecture/test-intelligence.md](../architecture/test-intelligence.md).

## Quick start

```bash
# 1. Produce a report. Any supported format works.
pytest --cov --cov-report=lcov:coverage.lcov
pytest --cov=<package> --cov-context=test   # also records which test ran each line (needs pytest-cov)
# 2. Ingest it.
repowise coverage add coverage.lcov
repowise coverage add .coverage             # per-test map
repowise coverage status                    # files, line and branch %, map counts
# 3. Use it.
repowise health                             # untested hotspots now light up
repowise impacted-tests main...HEAD         # the tests guarding this branch
repowise impacted-tests main...HEAD --format list | xargs pytest
```

Agents read the same answers through MCP ([From an agent](#from-an-agent)); the dashboard
shows coverage on a repository's **Tests** page. Walkthrough:
[examples/health-coverage/](../../examples/health-coverage/).

## Measured and inferred

The two kinds are shown side by side and never averaged.

| | Per-file aggregate | Per-test map | Inferred map |
|---|---|---|---|
| Comes from | Any coverage report | A report that records which test ran each line | The call and import graph, already indexed |
| Says | `src/auth/service.py` is 71% covered | `tests/test_auth.py::test_login` ran lines 40-58 of `src/auth/service.py` | `tests/test_auth.py` reaches `src/auth/service.py` |
| Granularity | File | Line | File |
| May produce a percentage | Yes | Yes | **Never** |
| Goes stale | Yes: tied to the commit it was measured at | Yes | No: read from the current graph |
| Labelled | `measured` | `measured` | `inferred` |
| Powers | Code-health coverage findings, the Tests page | `impacted-tests`, `impacted_tests`, `tests_to_run` | The fallback under every row, when nothing measured answers |

A call edge says control *can* reach a file, not that a run did, so inference is safe as
a floor ("something reaches this, do not call it untested") and unsafe as a quantity.
Refactoring plans also name the test functions nearest each function as runnable
selectors ([how](../architecture/test-intelligence.md#nearest-tests-per-function)).

### Reach states

Each inferred answer about a file is one of six states
([how each is decided](../architecture/test-intelligence.md#one-walk-two-graph-sources)):

| State | Meaning |
|---|---|
| `reached` | Tests reach the file; the answer names them and how |
| `unreached` | No walk reaches the file |
| `new` | The index has not seen the file |
| `no_signal` | The index holds no tests |
| `type_only` | Declares only types: nothing a test could run, so never a gap |
| `mocked_only` | Every reaching test replaces the file with a double |

## Supported formats

**LCOV**, **Cobertura**, **Clover** and **JaCoCo** XML, **Go coverprofile**
(`go test -coverprofile`), **Salesforce Apex JSON** (`sf apex run test
--code-coverage --result-format json`), **coverage.py `.coverage`** and **normalized JSON**
(`repowise-coverage-v1`). Three carry a per-test map: LCOV when each record has
a non-blank `TN:` test name, `.coverage` when recorded with per-test contexts,
and the Apex run result (`test-result-<run id>.json`).

- The format is sniffed and found in the usual locations
  ([rules](../architecture/test-intelligence.md#report-formats-and-discovery)); force
  one with `--format lcov|cobertura|clover|repowise-json|go-coverprofile|jacoco|apex`. Go
  percentages are line-based, not `go tool cover`'s statement percentage.
- Paths map by suffix, a tie is reported, never guessed
  ([rules](../architecture/test-intelligence.md#report-path-matching)); `--strict` fails
  on any unmapped report file. The coverage summary (`get_health`, the REST coverage
  route) says how each report mapped and whether it matches the indexed commit.

Feed any other runner through the normalized JSON:

```json
{ "format": "repowise-coverage-v1",
  "files": { "src/foo.py": { "line_coverage_pct": 87.5, "total_coverable_lines": 40,
    "covered_lines": [1, 2, 5], "coverable_lines": [1, 2, 3, 5] } } }
```

`covered_lines` and `coverable_lines` are optional. Patch coverage needs
`coverable_lines`; without it a file reads as having no line data, never as 0%.

### Per-test map

coverage.py dynamic contexts ([Quick start](#quick-start)) like
`tests/test_auth.py::TestLogin::test_ok|run` become test ids, with no runtime dependency
on coverage.py; per-test LCOV uses distinct `TN:` names and skips blank ones. A report
with no contexts still stores the per-file aggregate, and `coverage add` says so. The
map is capped at 250,000 rows, and the CLI reports how many were dropped.

## Impacted tests

`repowise impacted-tests` diffs a change, looks up the changed lines in the per-test map,
and returns the tests whose recorded coverage touches them.

```bash
repowise impacted-tests                        # staged changes (the default outside CI)
repowise impacted-tests main...HEAD            # a branch or PR, from the merge-base
repowise impacted-tests main...HEAD --format args --runner pytest   # for CI
```

`base...head` diffs from the merge-base (what a pull request changed), `base..head` is
the plain range, and a single SHA is one commit; change risk follows the same rule.

- Each result says how it was found (`via: coverage`, `changed-test`, then in a separate
  "NOT coverage-backed" table `call-graph`, `import-graph`, `filename-pattern`), and a
  guess never passes for evidence: otherwise it says "unknown, run the full suite to be
  safe", and with no map it prompts for `coverage add` on a report with contexts
  ([every case](../reference/CLI_REFERENCE.md#repowise-impacted-tests-revspec)).
- The graph also links tests to wiring no import states
  ([details](../architecture/test-intelligence.md#test-wiring-edges)).
- The headline counts the tests the change reaches. Two groups run but are counted
  apart: `selected.broadcast` (a `conftest.py` autouse fixture, hook or module-level code
  uses the change) and `selected.import_closure` (reached only through imports, more
  than one file away, no call path).
- `--format list` sends caveats to stderr; `--format args` prints runner arguments, or
  `:all` when any part is unknown. It reports, never gates: exit `0` either way, `2` only
  when the change or config cannot be read.

Rules, `tests.*` config keys and CI recipes:
[Selecting the tests a change needs](../start/CI.md#selecting-the-tests-a-change-needs).

## Patch coverage in CI

`repowise coverage check [REVSPEC]` gates a change on patch coverage: of the changed
lines the report marks executable, what share did the tests run?

```bash
repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
```

It needs only git and a report (no index, ingest or LLM key); the same figure is in
`get_change_risk`'s `patch_coverage` block and the REST route the editor reads. Only
`measured` files count; `not_in_report` is listed, never 0%, and stored coverage marks
files changed since measurement `stale_file`. Exit `0` passes or had nothing to judge,
`1` is below a gate, `2` could not run (no usable report, unknown revision, shallow
clone without a merge-base, bad config). With an index, each uncovered range names the
test file to extend. Statuses, flags and JSON:
[`coverage check` reference](../reference/CLI_REFERENCE.md#repowise-coverage-check-revspec);
the GitHub Action, GitLab template, per-language reports, path-scoped and risk-weighted
gates, branches, project coverage and monorepos: [Repowise in CI](../start/CI.md).

## Coverage in code health

Coverage feeds the [code health](CODE_HEALTH.md) layer through `untested_hotspot`,
`coverage_gap` (thin-but-present coverage), `coverage_gradient` (deducts in proportion to
the uncovered share) and the advisory `untested_complexity`. Every threshold:
[the full roster](../architecture/code-health.md#the-full-roster).

### Untested hotspots

`untested_hotspot` is the "write tests before you refactor" case. It fires only when a
file is all three of:

| Condition | Threshold |
|---|---|
| A hotspot | Flagged by the git layer: top-quartile churn in this repository plus absolute activity floors |
| Depended on | At least 4 dependents |
| Under-tested | Line coverage below 40%. With no coverage ingested, only when *nothing* says a test touches the file: no paired test file by name and no test reaching it in the graph |

The file must be code with at least one function, not a test. The graph check matters
because a filename convention misses tests in both directions
([examples](../architecture/test-intelligence.md#why-a-graph-signal)).

### Untested complexity

`untested_complexity` (advisory) reads each function's CRAP score,
`CC² × (1 − coverage)³ + CC`. Coverage is the share a report measured as run; without
one it is a tier, never a percentage: `direct` (a test calls the function), `reached`
(tests reach it or its file) or `none`, read as `coverage_basis: estimated`. Only
measured findings are listed by default; estimated ones appear when you name the type
or opt into unverified findings.

### Fix actions

`coverage_gap`, and `untested_complexity` on measured coverage or a direct test, carry
`actions`: a prompt to add tests (or split the function at complexity 30+) naming the
nearest test file, and the `.repowise/health-rules.json` rule that silences it for the
file. They show in the file drawer and `get_health(finding_id=...)`.

## From an agent

`get_risk(changed_files=[...], include=["tests"])` leads with a `directive` whose
`test_recommendations` names up to ten tests for the changed files. A `measured` row holds
a test node id the per-test map shows covering a changed file; an `inferred` row holds a
test file path the graph shows reaching the change, a candidate, not proof.

- `tests_to_run`, sent without the include, is the run list `repowise impacted-tests`
  makes ([fields](CHANGE_RISK.md#pr-mode-get_risk-with-changed_files)).
- `coverage_analysis` says whether coverage is available, partial, degraded or stale;
  without a coverage map the directive carries `coverage: {status, reason}`.
- `get_change_risk(revspec=...)` returns `impacted_tests`, the same selection over the
  diff, matching a measured map at either end by line
  ([payload](../architecture/test-intelligence.md#the-impacted_tests-payload)). Its
  `line_coverage` holds `untested_changes` (the strong signal: in the map, but nothing
  covers the touched lines), `stale_test_candidates` (covered lines whose guarding test
  is absent from the diff) and `no_coverage_data` (not in the map).
- **Self-check before a push:** `get_change_risk` without a `revspec` measures
  `patch_coverage` over what a push would bring (the working tree with untracked files,
  or `HEAD`) from the merge-base with CI's branch. Uncommitted code is `current` when the
  last ingest is newer than the changed files' newest edit, else `stale`; uncovered
  lines add one `next_actions` line naming tests to extend or to rerun with coverage.
- **Re-ingest after a test run** (opt-in: `hooks.coverage_reingest: true`, or
  `REPOWISE_HOOK_COVERAGE_REINGEST=1` for one session): a whole-suite run re-ingests any
  watched report newer than the last ingest, in the background, with one transcript line
  ([triggers](../architecture/test-intelligence.md#coverage-re-ingest-hook)). It costs a
  process start per shell command; Claude Code runs it from
  [repo-local hook entries](../agent/HOOKS.md#what-gets-written-where), Codex only after
  a successful command.

## Empty means unknown

**An empty test list never means the change is untested.** Every surface carries a
discriminator beside the list.

Only `impacted_tests.status: selected` vouches for `tests_to_run` (empty there means no
test is needed, as for docs or a deleted test); every other status carries
`run_all: true` ([statuses](../architecture/test-intelligence.md#the-impacted_tests-payload)).
`get_risk` keeps coverage availability, freshness and map presence in
`test_impact.coverage`, the CLI prints "unknown, run the full suite", and per-file
lookups report a file with no data as `no_coverage_data`, not uncovered.

## Configuration

The `coverage:` block in `.repowise/config.yaml`:

```yaml
coverage:
  auto_discover: true
  artifacts: ["coverage/lcov.info"]          # override the discovery globs
  paths:                         # explicit reports or globs (skip discovery)
    - "coverage/lcov.info"
    - {path: "web/coverage/*.info", path_prefix: web}   # per-report prefix
  format: lcov                   # skip format sniffing
  strip_prefix: "/build/src/"    # trim an absolute prefix from report paths
  ignore: ["**/*_pb2.py"]        # gitignore-style globs coverage leaves out
  reingest_on_update: false     # never re-read on update (default: when a report is newer)
  fail_under: 80                 # patch-coverage gate for `coverage check` (0-100)
  min_coverable_lines: 5         # small-change tolerance for that gate
  max_drop: 0.5                  # most project coverage may fall from the base, in points
  gates:                         # path-scoped gates
    - {name: api, paths: ["/services/api/"], fail_under: 85}
```

`init` and `update` also discover and ingest coverage, and `update` re-reads a report
written after the last ingest ([when](../reference/CONFIG.md#the-coverage-block)).
`repowise init --coverage-report <path>` takes explicit reports (repeatable);
`--coverage-report` is test coverage, while `--coverage` controls documentation breadth.

## Accuracy and limits

The inferred tier is measured on the Repowise repository against the line coverage of
its whole unit suite: 2,223 Python production files, 1,935 of them with a function body
run. A claim is checked twice, against any line of the file running and against a
function body running; recall counts the 1,935.

| Question | Claims | A line ran | A function body ran | Recall |
|---|---:|---:|---:|---:|
| "A test calls into this file" (suppresses `untested_hotspot`) | 1,515 | 100% | 97.7% | 76.5% |
| "A test reaches this file" (the Tests page and a file's page) | 2,191 | 99.9% | 88.3% | 99.9% |

A slice with complete per-test attribution (37 test files, 159 production files) puts
`tests_to_run` at 97.5% precision.

- Files a test reaches only by importing them run their module code but often no
  function, which is most of the gap between the two precision columns of the second row.
- Framework-invoked code with no static caller (migrations run by naming convention, for
  example) is not reached by the graph.
- The Tests page and a file's page share one definition of reached: a test calls into the
  file or imports it, directly or through a chain of imports. The page's unreached list
  is a floor, so routes, CLI commands and plugin registries a framework invokes can
  appear there with tests behind them.
- Inferred answers are file-level; only measured data is line-level.
- Measured data is only as current as the last ingest: each ingest replaces the previous
  rows, so ingest at the commit you intend to query. Rows read `stale` once the index
  moves past their commit.
- The Tests page trends the repo-wide totals of the newest 50 ingests once there are
  three, leaving partial ingests out.

Method: [architecture/test-intelligence.md](../architecture/test-intelligence.md) and,
across layers, [BENCHMARKS.md](../BENCHMARKS.md).

## Reference

`repowise coverage add [PATHS...]` (ingest; auto-discovers with no path),
`coverage status` (summary plus per-test map counts), `coverage check [REVSPEC]`
(patch-coverage gate, no index needed), `coverage suggest-gates` (proposes
`coverage.gates` YAML, writes nothing) and `repowise impacted-tests [REVSPEC]` (the tests
a change exercises, or runner arguments). Every flag: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-coverage). MCP:
[`get_change_risk`](../agent/MCP_TOOLS.md#get_change_risk),
[`get_risk`](../agent/MCP_TOOLS.md#get_risk).
