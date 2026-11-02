# Test intelligence: the inferred tier

How repowise answers "which tests reach this file" and "which tests should I run" with
no coverage report. User guide: [layers/TEST_INTELLIGENCE.md](../layers/TEST_INTELLIGENCE.md).

Code: `packages/core/src/repowise/core/analysis/test_reach/` (the walks),
`analysis/test_selection.py` (runner arguments for `impacted-tests`),
`analysis/health/coverage/` (parsers, discovery, path matching) and
`persistence/crud/analysis/coverage_map.py` (the measured per-test map).

## Why a graph signal

Only a coverage report fills the measured map (`test_coverage`), and most repositories
never ingest one; without a second signal `tests_to_run` would be empty,
`impacted_tests` would say "run the full suite", and `untested_hotspot` would match
filenames. Filename matching fails both ways: five of this repository's six worst
bug-magnet files have no test named for them, while the graph finds the tests:

| File | Filename convention | Graph |
|---|---|---|
| `ingestion/call_resolver.py` | nothing | 7 test files |
| `analysis/dead_code/analyzer.py` | nothing | 9 |
| `pipeline/persist.py` | nothing | 23 |
| `mcp_server/tool_answer/answer.py` | nothing | 18 |
| `analysis/pr_blast.py` | nothing | 3 |
| `analysis/health/engine.py` | `tests/unit/distill/test_engine.py` (the distill engine's tests) | 6, all under `tests/unit/health/` |

## Two graphs, two directions

The **call graph** leads: seed a test file's symbols and walk `EXECUTION_EDGE_TYPES`
(`ingestion/models.py`: `SYMBOL_USE_EDGE_TYPES` minus `references`, since a name in a
dispatch table is a mention, not a transfer of control; dead code asks "is this used",
this asks "is this run"). The **import graph** is weaker: an import pulls in every
symbol while the test may touch one. Measured against real per-test coverage on this
repository, over the slice with complete attribution (37 test files, 159 provably
executed production files):

**Forward, "what reaches this file"** (suppresses `untested_hotspot`):

| Walk | Claims | Correct | Precision | Recall |
|---|---:|---:|---:|---:|
| Import graph, 1 hop | 43 | 31 | 72.1% | 19.5% |
| Call graph, 3 hops | 48 | 44 | 91.7% | 27.7% |
| **Call graph, 3 hops, filtered** (ships) | 46 | 44 | **95.7%** | **27.7%** |
| Both unioned | 57 | 45 | 78.9% | 28.3% |

**Reverse, "which tests do I run"** (`tests_to_run`, `impacted_tests`):

| Walk | Targets | Hit rate | Precision |
|---|---:|---:|---:|
| Import graph, 1 hop | 32 | 96.9% | 94.8% |
| Call graph, 3 hops, filtered | 46 | 100.0% | 97.5% |
| Both unioned | 47 | 100.0% | 95.8% |
| **Call graph, else import graph** (ships) | 47 | **100.0%** | **97.5%** |

- **Forward is call edges only:** unioning costs 16.8 points of precision for 0.6 of
  recall, and a false "something reaches this" hides a real gap.
- **Reverse falls back:** the import tier answers only targets the call graph left
  silent, one more target at identical precision.
- The tables are uncapped. End to end, with `MAX_TESTS_PER_TARGET` truncating long
  lists, the reverse walk scores 100.0% hit and 95.5% precision on the slice.

## Depth

`DEFAULT_CALL_DEPTH = 3`: 3, 4 and 5 hops give the same 48 claims and 44 confirmations,
so recall is capped by the call graph's capture rate, not depth. `DEFAULT_MAX_DEPTH = 1`
for imports: a blanket second hop recovered 20 of 128 misses but added 50 wrong claims;
a facade-only hop through `__init__.py` (3 recovered, 5 added) and `conftest.py`
transitivity (none recovered) were rejected too.

## The resolution-origin filter

"Filtered" drops call edges whose `resolution_origin` is `global_unique` (a name bound to
the only symbol carrying it in the repository, scored 0.50 by the origin vocabulary). It
costs no recall and buys 4.0 points of forward precision (91.7% to 95.7%) and 1.1 of
reverse (96.4% to 97.5%). Also filtering `receiver_global` changes nothing; a 0.90
confidence floor instead cut recall from 27.7% to 23.3%.

## Why recall reads low

Coverage marks a line run whether a test called it or Python evaluated the module body on
import, so the truth set cannot tell an imported file from an exercised one. The 159
truth files by how much of what ran was inside function bodies:

| What ran inside function bodies | Files | Recall |
|---|---:|---:|
| Nothing | 39 | 0% |
| Under a quarter | 19 | 0% |
| A quarter to three quarters | 45 | 7% |
| Over three quarters | 56 | 77% |

Of the 13 missed in the last group, 11 are alembic migrations the framework invokes by
naming convention with no static caller. Repo-wide figures:
[Accuracy and limits](../layers/TEST_INTELLIGENCE.md#accuracy-and-limits).

## Nothing is stored

The inferred map is read from `graph_edges` on demand, never written to `test_coverage`:
a shared table behind a marker column would leak inferred rows to any reader that forgot
the marker, and the tests x sources closure would go stale whenever the graph moves, to
answer a bounded breadth-first search over rows already stored. The health pass computes
the whole-repo answer in one multi-source walk per run and caches it; the per-change walk
reads the edge table `pr_blast` reads.

## One walk, two graph sources

The reverse walks are written once and run over the stored graph or an in-memory one
(`build_walk_maps` on the NetworkX graph or its node-link JSON), with the same rows.

### Reach states

`reach_states` types each file ([the six states](../layers/TEST_INTELLIGENCE.md#reach-states)).
A file is `unreached` only when the call walk, the helper and barrel hop and the whole
import closure all miss it. A test the change adds or edits that is named for the file or
imports it counts as reaching it, as do the file's inline tests (Rust `#[cfg(test)]`, a
Go `_test.go` beside it).

### Mocked-only reach

A test that swaps a module out for a double does not reach it through the swap; a file
only such tests reach is `mocked_only`, read from the test text at walk time.

<details>
<summary>What counts as a swap</summary>

- **Python** swaps: `patch("pkg.helpers")`, `patch.object(pkg, "helpers")`,
  `monkeypatch.setattr`, a `sys.modules` entry. Patching a name inside a module, a
  run-time target, or a patch with `wraps`, `side_effect` or `autospec` keeps the reach.
- **JavaScript, TypeScript** swaps: `vi.mock` / `jest.mock` whose factory takes no
  parameter and returns only literals and `vi.fn()` / `jest.fn()`. An automock,
  `doMock`, a factory that reads the original, or a non-relative specifier keeps it.

</details>

## Measured map internals

`.coverage` is opened read-only as SQLite; Repowise decodes coverage.py's `numbits`
itself, falling back to the `arc` table without line bits. Rows are indexed by repo plus
source file (reverse) and repo plus test id (forward), capped by
`MAX_TEST_COVERAGE_ROWS`. Ingest records (formats merged, matched, unmatched and tied
paths, a sample of misses, the commit measured) are kept as history
(`COVERAGE_HISTORY_RETENTION = 50`); an index created before the history table changed
keeps one per repository until rebuilt.

## Report formats and discovery

The file is sniffed unless `--format` or `coverage.format` names a parser:

| Format | Detected by |
|--------|-------------|
| LCOV | `TN:` / `SF:` / `DA:` style lines |
| Cobertura XML | `<coverage` plus `<packages` or `line-rate` |
| Clover XML | `<coverage` plus `<project` |
| JaCoCo XML | The JaCoCo doctype, or a `<report` root |
| Go coverprofile | Leading `mode:` line |
| Salesforce Apex JSON | `{` naming `orgWideCoverage`, `testRunCoverage` or `ApexClassOrTrigger`, or a `[` list with `totalLines` and `lines` |
| coverage.py `.coverage` | SQLite file |
| Normalized JSON (`repowise-coverage-v1`) | Leading `{` plus `repowise-coverage` or `line_coverage_pct` |

With no path, `coverage add` looks for `coverage/lcov.info`, `lcov.info`,
`coverage.lcov`, `coverage.xml`, `**/cobertura.xml`, `**/clover.xml`,
`target/llvm-cov/**/*.lcov`, `coverage.out`, `cover.out`,
`**/target/site/jacoco*/jacoco.xml`, `build/reports/jacoco/**/*.xml`,
`**/test-result-codecoverage.json` (Salesforce CLI and VS Code), a repo-root
`.coverage` and a few more (`coverage.artifacts` overrides them). Multiple reports merge
by unioning covered and coverable lines.

## Report path matching

Exact path first, then the longest matching tail; a tie is reported as ambiguous, never
guessed. A relative path is tried under the report's own directory first
(`packages/web/coverage/lcov.info` naming `src/index.ts` maps to
`packages/web/src/index.ts`); Cobertura paths join each `<source>` root; a report naming
a directory needs more than the basename (`other/pkg/utils.py` never maps to
`src/utils.py`). When a whole report is unmatched, `coverage.strip_prefix` trims the
runner's absolute prefix.

## Test wiring edges

The graph links tests to wiring no import states: runner setup files (`setupFiles`,
`globalSetup` and the like) to the tests their config runs, a helper a test starts by a
relative path to that test, a `conftest.py` to the files pytest collects, and each
fixture request to its fixture, so a change a conftest reaches only through imports
selects the tests whose fixtures use it, plus one import check.

## Nearest tests per function

Each index keeps the few test functions nearest every function in the call graph, which
refactoring plans quote as runnable selectors; they are inferred like the rest. A
function with no resolved caller, or in a language whose tests cannot be told apart by
name (Java's `@Test`, Rust's `#[test]`), is unknown, not untested.

## The `impacted_tests` payload

`get_change_risk(revspec=...)` returns:

```json
{
  "status": "selected",
  "run_all": false,
  "reasons": [],
  "basis": "measured",
  "map_present": true,
  "tests_to_run": ["tests/test_auth.py::test_login", "..."],
  "why": {"tests/test_auth.py": "src/auth.py changed (coverage)"},
  "total": 23,
  "truncated": true,
  "line_coverage": {
    "untested_changes": [{"source_file": "...", "uncovered_lines": [...]}],
    "stale_test_candidates": [...],
    "covered": [...],
    "no_coverage_data": [...]
  },
  "summary": "23 test(s) to run for this change (9 of 412 test files); showing first 10; the rest can be skipped."
}
```

Statuses other than `selected` carry `run_all: true`: `run_all` (`reasons` says why;
`tests_to_run` is what to run first), `no_index`, `unknown` (git or index read failed),
`config_invalid` (`tests.*` does not parse), `timeout` (over 30 seconds;
`repowise impacted-tests` gives the same answer), `busy` (a selection is still running),
`no_source_line_changes`.

## Coverage re-ingest hook

`hooks.coverage_reingest: true` fires after a whole-suite run (`pytest`, `go test ./...`,
`npm test`, `cargo test`, `mvn test`, `./gradlew test` and similar). It does not act in a
repository that never ingested coverage, on a run targeting some tests (a path, `::`,
`-k`, `-run`, `--filter`, `-p` and the like), on a `.coverage` database alone, or on a
report only a `**` glob finds.
