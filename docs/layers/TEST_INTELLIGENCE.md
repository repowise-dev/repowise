# Test Intelligence

Test intelligence answers two questions your CI cannot: which files are risky
*and* untested, and which tests a given change exercises. The second turns a
4,000-test suite into the few dozen tests that guard the change you just made.

It works at two levels of evidence. Ingest a coverage report and the answers are
**measured**: this test ran these lines. Without one, repowise still answers
from the dependency graph, which records which tests reach which files; those
answers are **inferred**, always labelled as such, and never turned into a
percentage.

No LLM key, no network. Ingesting a report is a parse and a database write;
every question after that is an index lookup. The patch-coverage gate for pull
requests needs only git and a report (see [Patch coverage in CI](#patch-coverage-in-ci)).

## Quick start

```bash
# 1. Produce a report. Any supported format works.
pytest --cov --cov-report=lcov:coverage.lcov
coverage run --contexts=test -m pytest      # also records which test ran each line

# 2. Ingest it.
repowise coverage add coverage.lcov
repowise coverage add .coverage             # per-file coverage + per-test map
repowise coverage status

# 3. Use it.
repowise health                             # untested hotspots now light up
repowise impacted-tests main...HEAD         # the tests guarding this branch
repowise impacted-tests main...HEAD --format list | xargs pytest
```

From an agent: `get_change_risk(revspec="main...HEAD")` returns `impacted_tests`
and `patch_coverage`; `get_risk(changed_files=[...])` returns test
recommendations. In the dashboard, coverage is the **Tests** tab of a
repository's health page.

```
repowise coverage status

  Coverage (lcov)
    Files:  412
    Lines:  71.4%
    Branch: 63.9%

  Test-to-code map (coverage.py)
    Tests:   1,204
    Files:   388
    Records: 19,551
```

Longer walkthrough: [examples/health-coverage/](../../examples/health-coverage/).

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

The two kinds are shown side by side and never averaged. Inference errs in one
known direction: a call edge says control *can* reach a file, not that a run
did. So it is safe as a floor ("something reaches this, do not call it
untested") and unsafe as a quantity.

Each ingest replaces the previous per-file and per-test rows. Ingest at the
commit you intend to query so line numbers match. The repo-wide totals of the
newest 50 ingests are kept as history and drawn as a trend on the Tests tab
once there are three; partial ingests are left out of the trend.

## Supported formats

| Format | Detected by | Per-test map |
|--------|-------------|--------------|
| **LCOV** | `TN:` / `SF:` / `DA:` style lines | Yes, when each record carries a non-blank `TN:` test name |
| **Cobertura** XML | `<coverage` plus `<packages` or `line-rate` | No |
| **Clover** XML | `<coverage` plus `<project` | No |
| **JaCoCo** XML | The JaCoCo doctype, or a `<report` root | No |
| **Go coverprofile** (`go test -coverprofile`) | Leading `mode:` line | No |
| **coverage.py `.coverage`** | SQLite file | Yes, when written with `--contexts` |
| **Normalized JSON** (`repowise-coverage-v1`) | Leading `{` plus `repowise-coverage` or `line_coverage_pct` | No |

Force a parser with `--format lcov|cobertura|clover|repowise-json|go-coverprofile|jacoco`.
Go percentages are line-based, not the statement percentage `go tool cover`
prints. Any other runner can be fed through the normalized JSON:

```json
{ "format": "repowise-coverage-v1",
  "files": { "src/foo.py": { "line_coverage_pct": 87.5,
                             "total_coverable_lines": 40,
                             "covered_lines": [1, 2, 5],
                             "coverable_lines": [1, 2, 3, 5] } } }
```

`covered_lines` and `coverable_lines` are optional. Patch coverage needs
`coverable_lines`; without it a file reads as having no line data, never as 0%.

**Discovery.** With no path, `coverage add` looks for the usual locations:
`coverage/lcov.info`, `lcov.info`, `coverage.lcov`, `coverage.xml`,
`**/cobertura.xml`, `**/clover.xml`, `target/llvm-cov/**/*.lcov`,
`coverage.out`, `cover.out`, `**/target/site/jacoco*/jacoco.xml`,
`build/reports/jacoco/**/*.xml`, a repo-root `.coverage`, and a few more.
Multiple reports merge: covered and coverable lines are each unioned.

**Path matching.** Report paths are matched to indexed files by exact path,
then by the longest matching tail. A tie is reported as ambiguous, never
guessed. A relative path is tried under the report's own directory first, so
`packages/web/coverage/lcov.info` naming `src/index.ts` maps to
`packages/web/src/index.ts`. Cobertura paths are joined to each `<source>`
root. If a whole report comes back unmatched, set `coverage.strip_prefix`.
`coverage add --strict` fails when any report file did not map. The coverage
summary (`get_health`, the REST coverage route) reports how each report mapped
and whether it was measured at the indexed commit.

### Building a per-test map

The per-test map needs a report that records which test covered each line.

**coverage.py dynamic contexts** (the main path):

```bash
coverage run --contexts=test -m pytest
repowise coverage add .coverage
```

Repowise reads the `.coverage` SQLite file directly and has no runtime
dependency on coverage.py. Contexts like
`tests/test_auth.py::TestLogin::test_ok|run` become test ids.

**Per-test LCOV**: each record carries a distinct `TN:` name. Blank `TN:`
records are skipped.

If the report has no contexts, `coverage add` says so; the per-file aggregate
is still stored. The map is capped at 250,000 rows and the CLI reports how many
were dropped.

## Impacted tests

`repowise impacted-tests` diffs a change, looks up the changed lines in the
per-test map, and returns the tests whose recorded coverage touches them.

```bash
repowise impacted-tests                        # staged changes (the default outside CI)
repowise impacted-tests main...HEAD            # a branch or PR, from the merge-base
repowise impacted-tests main..HEAD             # a plain range
repowise impacted-tests abc123                 # a single commit
repowise impacted-tests main...HEAD --format args --runner pytest   # for CI
```

Each result says how it was found, and a guess never passes for evidence:

| Situation | Reported as |
|-----------|-------------|
| Per-test coverage on the changed lines | The covering tests, `via: coverage` |
| The changed file is itself a test | Itself, `via: changed-test` |
| No coverage rows, but a test reaches the file in the graph | Those test files, `via: call-graph` or `via: import-graph`, in a separate "NOT coverage-backed" table |
| No coverage and no graph edge, but a name-shaped match | That file, `via: filename-pattern`, in the same table |
| None of the above | "unknown, run the full suite to be safe" |
| No map ingested at all | A prompt to run `coverage add` on a report with contexts |

The graph also carries test wiring no import states: runner setup files
(`setupFiles`, `globalSetup` and the like) link to the tests their config runs,
a helper a test starts by a path relative to itself links to that test, and a
`conftest.py` links to the files pytest collects.

With `--format list` the caveats go to stderr so the pipe stays clean. The
command exits `0` in every case: it reports, it does not gate. `--format args`
prints runner arguments, or `:all` whenever any part of the answer is unknown.
Rules, `tests.*` config keys and CI recipes:
[Selecting the tests a change needs](../start/CI.md#selecting-the-tests-a-change-needs).

`base...head` diffs from the merge-base, so it is what a pull request changed.
`base..head` is the plain range. Change risk follows the same rule.

## Patch coverage in CI

`repowise coverage check [REVSPEC]` gates a change on patch coverage: of the
changed lines the report marks executable, what share did the tests run?

```bash
repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
```

It needs git and a report, nothing else: no index, no ingest, no LLM key. The
same figure appears in `get_change_risk`'s `patch_coverage` block and in the
REST patch-coverage route the editor reads.

| File status | Meaning | Counts in the % |
|-------------|---------|-----------------|
| `measured` | The report names the file and some changed lines are executable | Yes |
| `no_coverable_changes` | The report names the file, but no changed line is executable | No |
| `not_in_report` | The report does not name the file (e.g. a new file no test loaded) | No; listed, never 0% |
| `no_line_data` | The report names the file but gives no executable-line set | No |

Exit `0` passes or had nothing to judge, `1` is below a gate, `2` means the
check could not run (no usable report, unknown revision, shallow clone without
a merge-base, bad config). With an index, each uncovered range also names the
test file to extend.

Everything else lives in [Repowise in CI](../start/CI.md): the GitHub Action
and GitLab template, the per-language report commands,
[path-scoped gates](../start/CI.md#path-scoped-gates),
[risk-weighted gates](../start/CI.md#risk-weighted-patch-coverage),
[branches on changed lines](../start/CI.md#branches-on-changed-lines),
[project coverage and coverage outside the change](../start/CI.md#project-coverage-and-coverage-outside-the-change),
and [monorepos and matrix jobs](../start/CI.md#monorepos-and-matrix-jobs). All
flags and JSON fields: the [`coverage check` reference](../reference/CLI_REFERENCE.md#repowise-coverage-check-revspec).

## Untested hotspots

Coverage feeds the [code health](CODE_HEALTH.md) layer. The sharpest finding is
`untested_hotspot`, the "write tests before you refactor" case. It fires only
when a file is all three of:

1. **A hotspot**: flagged by the git layer, 8+ commits in 90 days, or a
   temporal hotspot score of 0.8 or more.
2. **Depended on**: at least 4 dependents.
3. **Under-tested**: line coverage below 40%. With no coverage ingested, it
   fires only when *nothing* says a test touches the file: no paired test file
   by name and no test reaching it in the graph.

Severity is `CRITICAL` at 15% coverage or less with 10+ dependents, `HIGH` at
one of those two, `MEDIUM` otherwise. `coverage_gap` covers thin-but-present
coverage, and `coverage_gradient` deducts in proportion to the uncovered share.

Why the graph matters here: a filename convention fails both ways. On this
repository, five of the six worst bug-magnet files have no test named for them
yet are reached by 3 to 23 test files in the graph, and the sixth was paired
with a different subsystem's `test_engine.py` by basename alone.

## From an agent

**`get_risk(changed_files=[...], include=["tests"])`** leads with a `directive`
whose `test_recommendations` names up to ten tests for the changed files. Each
row keeps its `basis`:

| `basis` | Holds | Means |
|---|---|---|
| `measured` | A test node id | The per-test map shows the test covering a changed file |
| `inferred` | A test file path | The graph shows the test reaching the change; a candidate, not proof |

`tests_to_run`, sent without the include, is the flat list with
`tests_to_run_basis`. When coverage exists it is the measured list; test files
in reverse-import reach that it lacks appear only as `inferred` rows with
`reason: structural_reach` under the include. Without coverage those files
join the list itself as `inferred`. Without a coverage map the directive carries
`coverage: {status, reason}`; with one, `coverage_analysis` says whether
coverage is available, partial, degraded or stale.

**`get_change_risk(revspec=...)`** returns `impacted_tests`, computed from the
changed *lines*, so it is narrower:

```json
{
  "status": "map_present",
  "map_present": true,
  "tests": ["tests/test_auth.py::test_login", "..."],
  "total": 23,
  "truncated": true,
  "line_coverage": {
    "untested_changes": [{"source_file": "...", "uncovered_lines": [...]}],
    "stale_test_candidates": [...],
    "covered": [...],
    "no_coverage_data": [...]
  },
  "summary": "23 test(s) cover the changed lines; showing first 10."
}
```

`untested_changes` is the strong signal: the file is in the map but nothing
covers the lines you touched. `stale_test_candidates` flags covered lines whose
guarding test is absent from the diff. `no_coverage_data` means the file is not
in the map. `get_change_risk` leaves out the CLI's filename-pattern guess.

**Self-checking before a push.** `get_change_risk` without a `revspec` measures
`patch_coverage` over everything a push would bring: the working tree
(untracked files included) or `HEAD`, diffed from the merge-base with the
branch CI compares against. Uncommitted code is `current` when the last ingest
came after the newest edit of the changed files, else `stale`. When changed
lines are uncovered, the directive adds one `next_actions` line naming the
tests to extend, or saying to re-run tests with coverage first.

**Re-ingest after a test run** (opt-in). Set `hooks.coverage_reingest: true`
in `.repowise/config.yaml` (or `REPOWISE_HOOK_COVERAGE_REINGEST=1` for one
session). After the agent runs a whole test suite (`pytest`, `go test ./...`,
`npm test`, `cargo test`, `mvn test`, `./gradlew test` and similar), the hook
re-ingests any watched report that is newer than the last ingest, in the
background, and says so in one transcript line. It acts only in a repository
that has ingested coverage before, never on a run that targets some tests (a
path, `::`, `-k`, `-run`, `--filter`, `-p` and the like), never on a
`.coverage` database alone, and never on a report only a `**` glob finds. It
costs a process start after each shell command in that repository. Claude Code
runs it from repo-local hook entries ([what gets written where](../agent/HOOKS.md#what-gets-written-where));
Codex only after a successful command.

## Empty means unknown

**An empty test list never means the change is untested.** Every surface
carries a discriminator beside the list:

- `get_change_risk` `impacted_tests.status`:

  | `status` | Meaning |
  |---|---|
  | `map_present` | A per-test map exists. An empty `tests` list here is a real finding: nothing in the map covers this change |
  | `inferred` | No map; the graph names candidate test files. Passing them does not clear the change |
  | `no_map` | No map and no graph answer. The summary says to run the full suite |
  | `no_index` | Nothing indexed yet |
  | `unknown` | The git read failed |
  | `no_source_line_changes` | No changed source lines to map |

- `get_risk` keeps coverage availability, freshness and map presence explicit
  in `test_impact.coverage`. An empty list under unavailable analysis is
  unknown, never "no tests needed".
- The CLI prints "unknown, run the full suite", and the per-file lookups report
  a file with no data as `no_coverage_data`, not as uncovered.

## Configuration

The `coverage:` block in `.repowise/config.yaml`:

```yaml
coverage:
  auto_discover: true
  artifacts:                     # override the discovery globs
    - "coverage/lcov.info"
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

Coverage is also auto-discovered and ingested during `init` and `update`, and
`repowise init --coverage-report <path>` takes explicit reports (repeatable).
`--coverage-report` is test coverage; `--coverage` controls documentation
breadth. Full block: [CONFIG.md](../reference/CONFIG.md).

## Accuracy and limits

Inferred tier, dogfooded on this repository against a real
`coverage run --contexts=test` over a slice with complete per-test attribution
(37 test files, 159 production files provably executed):

- **"What reaches this file"** (suppresses `untested_hotspot`): **95.7%
  precision**, 27.7% recall (46 claims, 44 correct).
- **"Which tests do I run"** (`tests_to_run`, `impacted_tests`): **97.5%
  precision**, 100% of targets answered (47 targets).
- Recall reads low because coverage counts a file as run when it was merely
  imported. Among files where over three quarters of what ran was inside
  function bodies, recall is 77%.
- Framework-invoked code with no static caller (migrations run by naming
  convention, for example) is not reached by the graph.
- Inferred answers are file-level; only measured data is line-level.
- Measured data is only as current as the last ingest; rows are tied to their
  commit and read `stale` once the index moves past it.

How these were measured and why the walk is shaped this way:
[architecture/test-intelligence.md](../architecture/test-intelligence.md).
Benchmark method across layers: [BENCHMARKS.md](../BENCHMARKS.md).

## Where it shows up

| Surface | What you get |
|---------|--------------|
| CLI | `repowise coverage add/status/check/suggest-gates`, `repowise impacted-tests` |
| MCP | `get_change_risk` (`impacted_tests`, `patch_coverage`), `get_risk` (`test_recommendations`, `tests_to_run`), `get_health` (coverage summary) |
| Dashboard | The Tests tab on the health page; coverage findings in code health |
| CI | The GitHub Action and GitLab template run `coverage check`; `impacted-tests --format args` selects tests |
| Editor | The branch-risk view reads patch coverage |
| Hooks | Optional re-ingest after an agent's full test run |

## Reference

| Command | What it does | Flags |
|---------|--------------|-------|
| `repowise coverage add [PATHS...]` | Ingest reports; auto-discovers with no path; builds the per-test map when contexts are present | `--path`, `--format`, `--strict`, `--verbose` |
| `repowise coverage status` | Coverage summary plus per-test map counts | `--path` |
| `repowise coverage check [REVSPEC]` | Patch-coverage gate, no index needed | `--report`, `--report-format`, `--fail-under`, `--min-coverable-lines`, `--fail-under-risky`, `--fail-under-branches`, `--base-report`, `--max-drop`, `--path`, `--format` |
| `repowise coverage suggest-gates` | Propose `coverage.gates` YAML; writes nothing | `--path`, `--format` |
| `repowise impacted-tests [REVSPEC]` | The tests a change exercises, or runner arguments | `--path`, `--staged`, `--format table\|json\|list\|args`, `--runner auto\|pytest\|go\|jest\|files` |

Full reference: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-coverage).
MCP: [`get_change_risk`](../agent/MCP_TOOLS.md#get_change_risk),
[`get_risk`](../agent/MCP_TOOLS.md#get_risk).

## See also

- [architecture/test-intelligence.md](../architecture/test-intelligence.md): the inferred tier's walks, measurements and storage choice.
- [Repowise in CI](../start/CI.md): gates, the GitHub Action, per-language reports.
- [CODE_HEALTH.md](CODE_HEALTH.md): coverage findings and how they deduct.
- [CHANGE_RISK.md](CHANGE_RISK.md): the review signal `impacted_tests` rides alongside.
- [CONFIG.md](../reference/CONFIG.md): the `coverage:` block.
