# Test Intelligence

Test intelligence answers two questions your CI cannot: which files are risky *and*
untested, and which tests a given change exercises. The second turns a 4,000-test suite
into the few dozen tests that guard the change you just made.

It works at two levels of evidence. Ingest a coverage report and the answers are
**measured**: this test ran these lines. Without one, Repowise still answers from the
dependency graph, which records which tests reach which files; those answers are
**inferred**, always labelled as such, and never turned into a percentage. No LLM key,
no network. Ingesting a report is a parse and a database write; every question after
that is an index lookup. The patch-coverage gate for pull requests needs only git and a
report (see [Patch coverage in CI](#patch-coverage-in-ci)).

## Quick start

```bash
# 1. Produce a report. Any supported format works.
pytest --cov --cov-report=lcov:coverage.lcov
coverage run --contexts=test -m pytest      # also records which test ran each line
# 2. Ingest it.
repowise coverage add coverage.lcov
repowise coverage add .coverage             # per-file coverage + per-test map
repowise coverage status                    # files, line and branch %, map counts
# 3. Use it.
repowise health                             # untested hotspots now light up
repowise impacted-tests main...HEAD         # the tests guarding this branch
repowise impacted-tests main...HEAD --format list | xargs pytest
```

Agents read the same answers through MCP ([From an agent](#from-an-agent)); the
dashboard shows coverage on the **Tests** tab of a repository's health page. Longer
walkthrough: [examples/health-coverage/](../../examples/health-coverage/).

## Measured and inferred

| | Per-file aggregate | Per-test map | Inferred map |
|---|---|---|---|
| Comes from | Any coverage report | A report that records which test ran each line | The call and import graph, already indexed |
| Says | `src/auth/service.py` is 71% covered | `tests/test_auth.py::test_login` ran lines 40-58 of `src/auth/service.py` | `tests/test_auth.py` reaches `src/auth/service.py` |
| Granularity | File | Line | File |
| May produce a percentage | Yes | Yes | **Never** |
| Goes stale | Yes: tied to the commit it was measured at | Yes | No: read from the current graph |
| Labelled | `measured` | `measured` | `inferred` |
| Powers | Code-health coverage findings, the Tests tab | `impacted-tests`, `impacted_tests`, `tests_to_run` | The fallback under every row, when nothing measured answers |

The two kinds are shown side by side and never averaged. A call edge says control *can*
reach a file, not that a run did, so inference is safe as a floor ("something reaches
this, do not call it untested") and unsafe as a quantity. Refactoring plans name the
test functions nearest each function in the call graph as runnable selectors
([how](../architecture/test-intelligence.md#nearest-tests-per-function)).

## Supported formats

| Format | Per-test map |
|--------|--------------|
| **LCOV** | Yes, when each record carries a non-blank `TN:` test name |
| **Cobertura**, **Clover** and **JaCoCo** XML | No |
| **Go coverprofile** (`go test -coverprofile`) | No |
| **coverage.py `.coverage`** | Yes, when written with `--contexts` |
| **Normalized JSON** (`repowise-coverage-v1`) | No |

The format is sniffed from the file
([detection rules](../architecture/test-intelligence.md#report-formats-and-discovery));
force one with `--format lcov|cobertura|clover|repowise-json|go-coverprofile|jacoco`. Go
percentages are line-based, not the statement percentage `go tool cover` prints. Any
other runner can be fed through the normalized JSON:

```json
{ "format": "repowise-coverage-v1",
  "files": { "src/foo.py": { "line_coverage_pct": 87.5, "total_coverable_lines": 40,
    "covered_lines": [1, 2, 5], "coverable_lines": [1, 2, 3, 5] } } }
```

`covered_lines` and `coverable_lines` are optional. Patch coverage needs
`coverable_lines`; without it a file reads as having no line data, never as 0%.

With no path, `coverage add` finds reports in the
[usual locations](../architecture/test-intelligence.md#report-formats-and-discovery);
multiple reports merge. Report paths map to indexed files by path suffix, and a tie is
reported, never guessed
([rules](../architecture/test-intelligence.md#report-path-matching)). If a whole report
comes back unmatched, set `coverage.strip_prefix`; `--strict` fails when any report file
did not map. The coverage summary (`get_health`, the REST coverage route) reports how
each report mapped and whether it was measured at the indexed commit.

**Building a per-test map.** The main path is coverage.py dynamic contexts:
`coverage run --contexts=test -m pytest`, then `repowise coverage add .coverage`.
Contexts like `tests/test_auth.py::TestLogin::test_ok|run` become test ids, with no
runtime dependency on coverage.py. Per-test LCOV works too: each record carries a
distinct `TN:` name, and blank `TN:` records are skipped. If the report has no contexts,
`coverage add` says so and still stores the per-file aggregate. The map is capped at
250,000 rows and the CLI reports how many were dropped.

## Impacted tests

`repowise impacted-tests` diffs a change, looks up the changed lines in the per-test
map, and returns the tests whose recorded coverage touches them.

```bash
repowise impacted-tests                        # staged changes (the default outside CI)
repowise impacted-tests main...HEAD            # a branch or PR, from the merge-base
repowise impacted-tests main...HEAD --format args --runner pytest   # for CI
```

`base...head` diffs from the merge-base, so it is what a pull request changed;
`base..head` is the plain range, and a single SHA is one commit. Change risk follows the
same rule. Each result says how it was found, and a guess never passes for evidence:

| Situation | Reported as |
|-----------|-------------|
| Per-test coverage on the changed lines | The covering tests, `via: coverage` |
| The changed file is itself a test | Itself, `via: changed-test` |
| No coverage rows, but a test reaches the file in the graph | Those test files, `via: call-graph` or `via: import-graph`, in a separate "NOT coverage-backed" table |
| No coverage and no graph edge, but a name-shaped match | That file, `via: filename-pattern`, in the same table |
| None of the above | "unknown, run the full suite to be safe" |
| No map ingested at all | A prompt to run `coverage add` on a report with contexts |

The graph also links tests to wiring no import states: runner setup files, helpers a
test starts by path, `conftest.py` files and the fixtures a test requests
([details](../architecture/test-intelligence.md#test-wiring-edges)).

With `--format list` the caveats go to stderr so the pipe stays clean. The command exits
`0` in every case: it reports, it does not gate. `--format args` prints runner
arguments, or `:all` whenever any part of the answer is unknown. Rules, `tests.*` config
keys and CI recipes:
[Selecting the tests a change needs](../start/CI.md#selecting-the-tests-a-change-needs).

## Patch coverage in CI

`repowise coverage check [REVSPEC]` gates a change on patch coverage: of the changed
lines the report marks executable, what share did the tests run?

```bash
repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
```

It needs git and a report, nothing else: no index, no ingest, no LLM key. The same
figure appears in `get_change_risk`'s `patch_coverage` block and in the REST
patch-coverage route the editor reads.

| File status | Meaning | Counts in the % |
|-------------|---------|-----------------|
| `measured` | The report names the file and some changed lines are executable | Yes |
| `no_coverable_changes` | The report names the file, but no changed line is executable | No |
| `not_in_report` | The report does not name the file (e.g. a new file no test loaded) | No; listed, never 0% |
| `no_line_data` | The report names the file but gives no executable-line set | No |
| `stale_file` | Stored coverage only: the file changed since the commit the coverage was measured at | No; listed, no figures |

Exit `0` passes or had nothing to judge, `1` is below a gate, `2` means the check could
not run (no usable report, unknown revision, shallow clone without a merge-base, bad
config). With an index, each uncovered range also names the test file to extend. The
GitHub Action, GitLab template, per-language reports, path-scoped and risk-weighted
gates, branches, project coverage and monorepos: [Repowise in CI](../start/CI.md). All
flags and JSON fields: the
[`coverage check` reference](../reference/CLI_REFERENCE.md#repowise-coverage-check-revspec).

## Untested hotspots

Coverage feeds the [code health](CODE_HEALTH.md) layer. The sharpest finding is
`untested_hotspot`, the "write tests before you refactor" case. It fires only when a
file is all three of:

| Condition | Threshold |
|---|---|
| A hotspot | Flagged by the git layer, 8+ commits in 90 days, or a temporal hotspot score of 0.8 or more |
| Depended on | At least 4 dependents |
| Under-tested | Line coverage below 40%. With no coverage ingested, only when *nothing* says a test touches the file: no paired test file by name and no test reaching it in the graph |

Severity is `CRITICAL` at 15% coverage or less with 10+ dependents, `HIGH` at one of
those two, `MEDIUM` otherwise. `coverage_gap` covers thin-but-present coverage, and
`coverage_gradient` deducts in proportion to the uncovered share. The graph check
matters because a filename convention misses tests in both directions
([examples](../architecture/test-intelligence.md#why-a-graph-signal)).

## From an agent

`get_risk(changed_files=[...], include=["tests"])` leads with a `directive` whose
`test_recommendations` names up to ten tests for the changed files. Each row keeps its
`basis`:

| `basis` | Holds | Means |
|---|---|---|
| `measured` | A test node id | The per-test map shows the test covering a changed file |
| `inferred` | A test file path | The graph shows the test reaching the change; a candidate, not proof |

`tests_to_run`, sent without the include, is the run list `repowise impacted-tests`
makes, with `tests_to_run_basis`, `tests_to_run_why`, `tests_run_all` and
`tests_run_all_reasons` ([PR mode](CHANGE_RISK.md#pr-mode-get_risk-with-changed_files)).
`coverage_analysis` says whether coverage is available, partial, degraded or stale;
without a coverage map the directive carries `coverage: {status, reason}`.

`get_change_risk(revspec=...)` returns `impacted_tests`, the same selection over the
change's diff, so a measured map at either end of it matches by line
([payload](../architecture/test-intelligence.md#the-impacted_tests-payload)). In its
`line_coverage` block, `untested_changes` is the strong signal: the file is in the map
but nothing covers the lines you touched. `stale_test_candidates` flags covered lines
whose guarding test is absent from the diff. `no_coverage_data`: the file is not in the
map.

**Self-check before a push.** `get_change_risk` without a `revspec` measures
`patch_coverage` over everything a push would bring: the working tree (untracked files
included) or `HEAD`, diffed from the merge-base with the branch CI compares against.
Uncommitted code is `current` when the last ingest came after the newest edit of the
changed files, else `stale`. When changed lines are uncovered, the directive adds one
`next_actions` line naming the tests to extend, or saying to rerun tests with coverage.

**Re-ingest after a test run** (opt-in). With `hooks.coverage_reingest: true` in
`.repowise/config.yaml` (or `REPOWISE_HOOK_COVERAGE_REINGEST=1` for one session), a
whole-suite test run by the agent re-ingests any watched report newer than the last
ingest, in the background, with one transcript line
([trigger rules](../architecture/test-intelligence.md#coverage-re-ingest-hook)). It
costs a process start after each shell command in that repository. Claude Code runs it
from [repo-local hook entries](../agent/HOOKS.md#what-gets-written-where); Codex only
after a successful command.

## Empty means unknown

**An empty test list never means the change is untested.** Every surface carries a
discriminator beside the list. For `get_change_risk`, `impacted_tests.status` is one of:

| `status` | Meaning |
|---|---|
| `selected` | The selection vouches for `tests_to_run`. Empty here means the change needs no test (documentation, a deleted test) |
| `run_all` | Every test must run; `reasons` says why, and `tests_to_run` is what to run first |
| `no_index` | Nothing indexed yet |
| `unknown` | The git or index read failed |
| `config_invalid` | `tests.*` in `.repowise/config.yaml` does not parse |
| `timeout` | The selection took over 30 seconds; `repowise impacted-tests` gives the same answer |
| `busy` | A selection for this repository is still running |
| `no_source_line_changes` | No changed source lines to map |

Every status but `selected` carries `run_all: true`: nothing vouches for a subset.
`get_risk` keeps coverage availability, freshness and map presence explicit in
`test_impact.coverage`; an empty list under unavailable analysis is unknown, never "no
tests needed". The CLI prints "unknown, run the full suite", and the per-file lookups
report a file with no data as `no_coverage_data`, not as uncovered.

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
  reingest_on_update: false
  fail_under: 80                 # patch-coverage gate for `coverage check` (0-100)
  min_coverable_lines: 5         # small-change tolerance for that gate
  max_drop: 0.5                  # most project coverage may fall from the base, in points
  gates:                         # path-scoped gates
    - {name: api, paths: ["/services/api/"], fail_under: 85}
```

`init` and `update` also discover and ingest coverage;
`repowise init --coverage-report <path>` takes explicit reports (repeatable).
`--coverage-report` is test coverage; `--coverage` controls documentation breadth. Full
block: [CONFIG.md](../reference/CONFIG.md).

## Accuracy and limits

The inferred tier is measured on the Repowise repository against a real
`coverage run --contexts=test`, over a slice with complete per-test attribution (37 test
files, 159 production files provably executed):

| Question | Precision | Reach |
|---|---:|---:|
| "What reaches this file" (suppresses `untested_hotspot`) | 95.7% (44 of 46 claims) | 27.7% recall |
| "Which tests do I run" (`tests_to_run`, `impacted_tests`) | 97.5% | 100% of 47 targets answered |

- Recall reads low because coverage counts a file as run when it was merely imported.
  Among files where over three quarters of what ran was inside function bodies, recall
  is 77%.
- Framework-invoked code with no static caller (migrations run by naming convention, for
  example) is not reached by the graph.
- Inferred answers are file-level; only measured data is line-level.
- Measured data is only as current as the last ingest: each ingest replaces the previous
  rows, so ingest at the commit you intend to query. Rows read `stale` once the index
  moves past their commit. The Tests tab trends the repo-wide totals of the newest 50
  ingests once there are three, leaving partial ingests out.

Method: [architecture/test-intelligence.md](../architecture/test-intelligence.md) and,
across layers, [BENCHMARKS.md](../BENCHMARKS.md).

## Reference

Commands: `repowise coverage add [PATHS...]` (ingest; auto-discovers with no path),
`coverage status` (summary plus per-test map counts), `coverage check [REVSPEC]`
(patch-coverage gate, no index needed), `coverage suggest-gates` (proposes
`coverage.gates` YAML, writes nothing) and `repowise impacted-tests [REVSPEC]` (the
tests a change exercises, or runner arguments). Every flag:
[CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-coverage). MCP:
[`get_change_risk`](../agent/MCP_TOOLS.md#get_change_risk),
[`get_risk`](../agent/MCP_TOOLS.md#get_risk).
