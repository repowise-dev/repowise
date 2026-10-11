# Test intelligence: the inferred tier

How repowise answers "which tests reach this file" and "which tests should I
run" with no coverage report. For the user-facing layer, see
[layers/TEST_INTELLIGENCE.md](../layers/TEST_INTELLIGENCE.md).

Code: `packages/core/src/repowise/core/analysis/test_reachability.py` (the
walks), `analysis/test_selection.py` (runner arguments for `impacted-tests`),
`analysis/health/coverage/` (report parsers, discovery, path matching) and
`persistence/crud/analysis/coverage_map.py` (the measured per-test map).

## Why a graph signal

The measured map (`test_coverage`) is filled only by a coverage report. Most
repositories never ingest one, so without a second signal `tests_to_run` was
empty, `impacted_tests` said "run the full suite", and `untested_hotspot` fell
back to matching filenames.

Filename matching fails both ways. On this repository, five of the six worst
bug-magnet files have no test named for them, while the graph names the tests
that reach them:

| File | Filename convention | Graph |
|---|---|---|
| `ingestion/call_resolver.py` | nothing | 7 test files |
| `analysis/dead_code/analyzer.py` | nothing | 9 |
| `pipeline/persist.py` | nothing | 23 |
| `mcp_server/tool_answer/answer.py` | nothing | 18 |
| `analysis/pr_blast.py` | nothing | 3 |
| `analysis/health/engine.py` | `tests/unit/distill/test_engine.py` | 6, all under `tests/unit/health/` |

The last row is the worse failure: a basename match paired the health engine
with the distill engine's tests.

## Two graphs, two directions

The **call graph** leads: seed the symbols a test file declares, walk
`EXECUTION_EDGE_TYPES` forward, and every symbol reached is one the test can
run. `EXECUTION_EDGE_TYPES` (in `ingestion/models.py`) is
`SYMBOL_USE_EDGE_TYPES` minus `references`, because a name sitting in a
dispatch table is a mention, not a transfer of control. Dead code asks "is this
used", which a mention answers; this asks "is this run".

The **import graph** is the weaker tier: importing a module pulls in every
symbol it defines while the test may touch one function.

Measured against a real `coverage run --contexts=test` on this repository, over
the slice where per-test attribution is complete, both sides seeing the same
37 test files and 159 provably executed production files.

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

The tiers combine differently per direction. Unioning costs the forward walk
16.8 points of precision for 0.6 of recall, and a false "something reaches
this" hides a real gap, so forward is call edges only. Reverse falls back: the
import tier is spent only on targets the call graph left silent, which answers
one more target at identical precision.

Both tables compare signals uncapped over the same test set. End to end, with
the walk seeing the whole test set and `MAX_TESTS_PER_TARGET` applied, the
reverse walk scores 100.0% hit and 95.5% precision on the same slice; the gap
is the cap truncating a long list.

## Depth

`DEFAULT_CALL_DEPTH = 3`: 3, 4 and 5 hops return the same 48 claims and 44
confirmations, so the recall ceiling is the call graph's capture rate, not the
depth. `DEFAULT_MAX_DEPTH = 1` for imports: a blanket second hop recovered 20
of 128 misses while adding 50 wrong claims; a facade-only second hop through
`__init__.py` (3 recovered, 5 added) and `conftest.py` transitivity (none
recovered) were also tested and rejected.

## The resolution-origin filter

"Filtered" drops call edges whose `resolution_origin` is `global_unique`: a
name bound to the only symbol carrying it anywhere in the repository, which the
origin vocabulary itself scores at 0.50. Dropping it costs no recall and buys
4.0 points of forward precision (91.7% to 95.7%) and 1.1 of reverse (96.4% to
97.5%). Filtering `receiver_global` too changes no number. A confidence floor
of 0.90 was tried instead and cut recall from 27.7% to 23.3%.

## Why recall reads low

Coverage records a line as run whether a test called into it or Python merely
evaluated the module body on import, so the truth set cannot tell an imported
file from an exercised one. Splitting the 159 truth files by how much of what
ran was inside a function body:

| What ran inside function bodies | Files | Recall |
|---|---:|---:|
| Nothing | 39 | 0% |
| Under a quarter | 19 | 0% |
| A quarter to three quarters | 45 | 7% |
| Over three quarters | 56 | 77% |

Of the 13 missed in the last group, 11 are alembic migrations the framework
invokes by naming convention with no static caller.

## Nothing is stored

The inferred map is read from `graph_edges` when asked and never written to
`test_coverage`:

1. **A consumer cannot mistake it for measured data.** Sharing the table behind
   a marker column would make every existing reader start returning inferred
   rows the day someone forgot to check the marker.
2. **It would be a transitive closure.** Tests reach most production files;
   materialising that is tests x sources rows that go stale whenever the graph
   moves, to answer a bounded breadth-first search over rows already in the
   database.

The health pass computes the whole-repo answer in one multi-source walk, once
per run, and caches it. The per-change walk reads the same edge table
`pr_blast` reads.

## Measured map internals

- `.coverage` is opened read-only as SQLite. Repowise decodes coverage.py's
  `numbits` line bitmaps itself and falls back to the `arc` table when line
  bits are absent, so there is no runtime dependency on coverage.py.
- Rows are indexed both ways: repo plus source file (reverse lookup) and repo
  plus test id (forward). `MAX_TEST_COVERAGE_ROWS = 250_000`.
- Ingest records (formats merged, matched, unmatched and tied paths, a sample of
  misses, the commit measured) are kept as history,
  `COVERAGE_HISTORY_RETENTION = 50`. A local index created before the history
  table changed keeps one record per repository until rebuilt.
- Path matching needs more than the basename when the report names a
  directory: `other/pkg/utils.py` never maps to `src/utils.py`.
