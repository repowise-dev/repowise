# Test Intelligence

Ingest a coverage report and repowise can answer two questions your CI cannot:
which files are risky *and* untested, and which tests a given diff actually
exercises. That second one turns a 4,000-test suite into the 40 tests that guard
the change you just made.

Without a coverage report there is still an answer, a weaker one. The dependency
graph records which test files import which source files, so repowise can say
*something reaches this* even where nothing measured it. That inferred tier needs
no setup and is always labelled as inferred. See
[The inferred tier](#the-inferred-tier-no-coverage-report-needed).

Everything here is an index lookup: no LLM, no network.

## Quick start

```bash
# 1. Produce a report. Any of these work.
pytest --cov --cov-report=lcov:coverage.lcov
coverage run --contexts=test -m pytest      # also builds the per-test map

# 2. Ingest it.
repowise coverage add coverage.lcov
repowise coverage add .coverage             # per-file coverage + per-test map
repowise coverage status

# 3. Use it.
repowise health                             # untested hotspots now light up
repowise impacted-tests main..HEAD          # the tests guarding this branch
repowise impacted-tests main..HEAD --format list | xargs pytest
```

Longer walkthrough: [examples/health-coverage/](../../examples/health-coverage/).

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

## Two dimensions, one command

`repowise coverage add` stores two different things, and the difference matters
for everything below.

| Dimension | What a row says | Powers |
|-----------|-----------------|--------|
| **Per-file aggregate** | This file is 71% covered, merged across every test. | `untested_hotspot`, `coverage_gap`, `coverage_gradient` in [code health](CODE_HEALTH.md), the coverage dashboard |
| **Per-test map** | Test `tests/test_auth.py::test_login` covered lines 40-58 of `src/auth/service.py`. | `repowise impacted-tests`, `get_change_risk`'s `impacted_tests`, `get_risk`'s `tests_to_run` |
| **Inferred map** (no ingest) | `tests/test_round_trips.py` imports `src/auth/service.py`, so it reaches it. | The fallback under every row above, always labelled `inferred` |

The aggregate always gets stored. The map is only built when the report carries
per-test contexts. A report without contexts still ingests fine, it just skips
the map. The inferred map is not stored at all: it is read off the graph when
asked.

Both are point-in-time: each ingest replaces the previous rows rather than
appending history. Ingest at the same commit you intend to query, so line numbers
line up.

## Supported formats

| Format | Detected by | Per-test map |
|--------|-------------|--------------|
| **LCOV** | Leading `TN:` / `SF:`, or any `TN\|SF\|DA\|BRDA\|LF\|LH\|BRF\|BRH:` line | Yes, when each record carries a non-blank `TN:` test name |
| **Cobertura** XML | `<coverage` plus `<packages` or `line-rate` | No |
| **Clover** XML | `<coverage` plus `<project` | No |
| **coverage.py `.coverage`** | SQLite magic bytes | Yes, when written with `--contexts` |
| **Normalized JSON** (`repowise-coverage-v1`) | Leading `{` plus `repowise-coverage` or `line_coverage_pct` | No |

Force a parser with `--format lcov|cobertura|clover|repowise-json`. The normalized
JSON shape lets you feed any runner once you map it:

```json
{ "format": "repowise-coverage-v1",
  "files": { "src/foo.py": { "line_coverage_pct": 87.5,
                             "total_coverable_lines": 40 } } }
```

With no path argument, `add` auto-discovers `coverage/lcov.info`, `lcov.info`,
`coverage.lcov`, `coverage.xml`, `**/cobertura.xml`, `**/clover.xml`,
`target/llvm-cov/**/*.lcov`, and a repo-root `.coverage`. Multiple reports merge
with hit-wins: covered lines union, coverable counts take the max.

Report paths are matched to indexed files by exact key first, then basename, then
the longest trailing-path overlap. A tie refuses to guess and is reported as
ambiguous rather than mapped to the wrong file. If a whole report comes back
unmatched, set `coverage.strip_prefix` in `.repowise/config.yaml`.

## Building a per-test map

The map needs a report that records *which test* covered each line. Two paths
exist today.

**coverage.py dynamic contexts** (the main one):

```bash
coverage run --contexts=test -m pytest
repowise coverage add .coverage
```

The `.coverage` file is read directly as read-only SQLite. Repowise decodes the
`numbits` line bitmaps itself, so it has no runtime dependency on coverage.py,
and falls back to the `arc` table when line bits are absent. Contexts look like
`tests/test_auth.py::TestLogin::test_ok|run`; the leading path becomes the test's
own file when it is resolvable.

**Per-test LCOV:** a report where each `end_of_record` block carries a distinct
`TN:` name. Blocks with a blank `TN:` are skipped. A bare suite label with no
path resolves to a test id with no test file, which is still usable for "run
these" but not for staleness reasoning.

If you ran without contexts, `coverage add` says so explicitly rather than
silently producing an empty map.

Rows land in a `test_coverage` table indexed both ways (repo plus source file for
the reverse lookup, repo plus test id for the forward one), capped at 250,000
rows. The CLI reports how many were dropped if you hit the cap.

## Impacted tests

`repowise impacted-tests` diffs a change, looks up the changed *lines* in the
map, and returns the tests whose recorded coverage intersects them.

```bash
repowise impacted-tests                        # staged changes (the default)
repowise impacted-tests main..HEAD             # a branch or PR range
repowise impacted-tests abc123                 # a single commit
repowise impacted-tests main..HEAD --format list | xargs pytest
```

| Flag | Values |
|------|--------|
| `--path` | Repo path (defaults to cwd, or the workspace primary) |
| `--staged` | Diff `git diff --cached`. Implied when no range is given |
| `--format` | `table` (default), `json` (full report), `list` (test ids, one per line) |

It always says which path fired, and it never lets a guess pass for evidence:

| Situation | Reported as |
|-----------|-------------|
| Changed file has per-test coverage on the changed lines | The exact covering tests, `via: coverage` |
| The changed file is itself a test | Itself, `via: changed-test` |
| Changed file has no coverage rows, but a test reaches it in the graph | Those test files, `via: import-graph`, in the "NOT coverage-backed" table |
| No coverage and no graph edge, but a name-shaped match | That file, `via: filename-pattern`, in the same table |
| None of the above | "unknown, run the full suite to be safe" |
| No map ingested at all | A prompt to run `coverage add` on a report with contexts |

Deletion-only files are dropped from the diff (there are no new lines to cover).
With `--format list` the caveats go to stderr so the stdout pipe into `pytest`
stays clean. The command exits `0` in every one of these cases, including "no
tests found": it is a reporting tool, not a gate.

## Untested hotspots

Coverage feeds the [code health](CODE_HEALTH.md) layer's test-coverage markers.
The sharpest of them is `untested_hotspot`, the textbook "write tests before you
refactor" case. It fires only when a file is all three of:

1. **A hotspot.** Flagged as one by the git layer, or 8+ commits in 90 days, or a
   temporal hotspot score at or above 0.8.
2. **Centrally depended on.** At least 4 dependents. Below that, a churning file
   is usually a leaf one author is iterating on, and flagging it is noise.
3. **Under-tested.** Line coverage below 40%. When no coverage has been ingested
   at all, it falls back to firing only when *nothing* says a test touches the
   file: no paired test file by name, and no test reaching it in the import
   graph. Either signal suppresses the finding, because this is the one place
   the layer asserts a negative and the bar for asserting it is no evidence at
   all. The graph half is what fixed the long-standing false positive on suites
   that name their tests for behaviour rather than for the file under test.

Severity is `CRITICAL` at 15% coverage or less with 10+ dependents, `HIGH` at one
of those two, `MEDIUM` otherwise. Its sibling `coverage_gap` handles the
has-coverage-but-thin case, and `coverage_gradient` applies a continuous
deduction proportional to the uncovered fraction, so a file is penalised in
proportion to how much of it is untested rather than only at a cliff.

## From an agent

Two MCP tools carry test information, at two different granularities.

**`get_risk(changed_files=[...])`** leads with a `directive` block whose
`test_recommendations` names tests for the changed *files*. It is file-level,
scoped to the diff, and capped at ten. Each row keeps its machine-readable
`basis`, repository identity, source files, and evidence:

| Row `basis` | Recommendation holds | Means |
|---|---|---|
| `measured` | test node id | the available per-test map found the test covering a changed file |
| `inferred` | test file path | the graph shows the test reaching the change; a candidate, not coverage proof |

`tests_to_run` is the compatibility id projection. It preserves the historical
measured-first fallback and its `measured` / `inferred` / `none` scalar domain;
new consumers use the adjacent typed rows for the additive evidence union.
Total/emitted/truncated/omitted fields cover each exact population before its
cap. `coverage_analysis` separately reports
available, unavailable, partial, degraded, and stale states; an empty list under
unavailable analysis is unknown and never means "no tests are needed".

Both forms are runnable arguments to pytest. The typed list may contain both,
but every row keeps its own basis; the legacy id list remains measured-first.

**`get_change_risk(revspec=...)`** returns `impacted_tests`, computed from the
changed *lines*, so it is a strictly narrower and more useful set:

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

The `line_coverage` buckets are the honest breakdown: `untested_changes` is the
strong signal (the file *is* in the map, but nothing covers the lines you
touched), `stale_test_candidates` flags covered lines whose guarding test file is
absent from the diff, and `no_coverage_data` means the file is simply not in the
map.

`get_change_risk` deliberately omits the CLI's filename-pattern guess. An agent
cannot tell a guess from real coverage, and `no_coverage_data` already reports
those files honestly.

## The inferred tier: no coverage report needed

Everything above needs an ingest. Most repositories never do one, and the layer
used to have nothing to say to them: `tests_to_run` came back empty,
`impacted_tests` said "run the full suite", and `untested_hotspot` fell back to
matching filenames.

Matching filenames fails both ways, and this repository is the proof. Of its six
worst bug-magnet files, five have no file named for them anywhere under `tests/`
and so read as untested, while the graph names the tests that reach them:

| File | Filename convention | Graph |
|---|---|---|
| `ingestion/call_resolver.py` | nothing | 7 test files |
| `analysis/dead_code/analyzer.py` | nothing | 9 |
| `pipeline/persist.py` | nothing | 23 |
| `mcp_server/tool_answer/answer.py` | nothing | 18 |
| `analysis/pr_blast.py` | nothing | 3 |
| `analysis/health/engine.py` | `tests/unit/distill/test_engine.py` | 6, all under `tests/unit/health/` |

The last row is the worse failure. The convention matches on basename alone, so
it paired the health engine with the *distill* engine's tests (a different
subsystem) and called the file tested on the strength of a name collision.

Across the whole index the effect is large. Against the **80** standing
`untested_hotspot` findings the filename convention leaves behind, the call graph
clears **32** of them, 22 high and 3 critical, where the one-hop import walk this
tier used to run clears 11.

The graph already holds the relation. A test whose **calls reach** a source file
executes it, and that is a recorded edge rather than a name-shaped guess. It needs
no setup at all.

### What it is allowed to claim

| | Measured map | Inferred map |
|---|---|---|
| Comes from | a coverage report you ingested | the call graph, already indexed |
| Granularity | lines | files |
| Proves | this test executed these lines | this test's calls reach this file |
| Decays | yes, see the coverage age report | no |
| May produce a percentage | yes | **never** |
| Labelled | `basis: "measured"` | `basis: "inferred"` |

The two are shown side by side and are never averaged into one number. The
canonical PR test-impact population evaluates both and retains their separate
evidence; compatibility surfaces remain measured-first. Other test-intelligence
surfaces may use inference only to fill measured silence. Inference's error is
one-sided and known, so it is sound as a floor ("something reaches this, do not
call it untested") and unsound as a quantity.

### The call graph leads, the import graph fills silence

The tier first shipped walking **import** edges one hop, which was the best of the
import-based options and still the wrong graph: a test that imports a module is
weak evidence it runs it. Release 0.44.0 made the call graph good enough to lead
with, so it does.

Dogfooded against a real `coverage run --contexts=test` over a slice where
per-test attribution is complete, both sides seeing the same 37 test files and 159
provably-executed production files:

**Forward, "what reaches this file" (suppresses `untested_hotspot`):**

| Walk | claims | correct | precision | recall |
|---|---:|---:|---:|---:|
| import graph, 1 hop (what shipped before) | 43 | 31 | 72.1% | 19.5% |
| call graph, 3 hops | 48 | 44 | 91.7% | 27.7% |
| **call graph, 3 hops, filtered** (ships) | 46 | 44 | **95.7%** | **27.7%** |
| both unioned | 57 | 45 | 78.9% | 28.3% |

**Reverse, "which tests do I run" (`tests_to_run`, `impacted_tests`):**

| Walk | targets | hit rate | precision |
|---|---:|---:|---:|
| import graph, 1 hop (what shipped before) | 32 | 96.9% | 94.8% |
| call graph, 3 hops, filtered | 46 | 100.0% | 97.5% |
| both unioned | 47 | 100.0% | 95.8% |
| **call graph, else import graph** (ships) | 47 | **100.0%** | **97.5%** |

The call graph wins on both axes, so it leads. **The two tiers are combined
differently per direction, because the measurements differ.** Unioning costs the
forward walk 16.8 points of precision for 0.6 of recall, and a false "something
reaches this" hides a real gap, so forward is call edges only. Reverse falls back
instead of unioning, spending the import tier only on targets the call graph left
silent, which answers one more target at identical precision.

**Depth 3 is where the call walk saturates**: 3, 4 and 5 hops return the same 48
claims and 44 confirmations, so the ceiling is the call graph's capture rate rather
than the depth. The import tier keeps its measured default of one hop.

**"Filtered" is `resolution_origin` doing work.** Not every call edge is equally
trustworthy, and this is the first thing to read the graph's own confidence
vocabulary: `global_unique`, which binds a name to the only symbol carrying it
repo-wide and which the vocabulary itself scores 0.50, is dropped. That buys **4.0
points of forward precision and 1.1 of reverse at no cost to recall**. A confidence
floor of 0.90 was tried instead and cut recall from 27.7% to 23.3%.

**Why recall reads low at 27.7%**, which is a property of the truth set rather than
the walk: `coverage` records a line as run whether a test called into it or Python
merely evaluated the module body on import, so the truth set cannot tell an
imported file from an exercised one. Splitting the 159 truth files by how much of
what ran was inside a function body: 39 ran nothing inside one at all (0% recall,
correctly), 19 ran under a quarter inside (0%), 45 a quarter to three quarters
(7%), and 56 over three quarters (**77%**). Of the 13 missed in that last group, 11
are alembic migrations the framework invokes by naming convention with no static
caller anywhere.

<sub>Reachability walks <code>EXECUTION_EDGE_TYPES</code>, which is the reachability
view minus <code>references</code> and <code>reads</code>: those record a mention
rather than a transfer of control. Dead code asks "is this used", which a mention
answers; this asks "is this run", which it does not. Both depths are keyword
arguments on the walks.</sub>

### Nothing is stored

The inferred map is read off `graph_edges` when asked and never written to
`test_coverage`. Two reasons, and the first is the one that matters:

1. **A consumer cannot mistake it for measured data.** Sharing the table behind
   a marker column would make every existing reader work immediately and would
   make every existing reader silently start returning inferred rows the day
   someone forgot to check the marker. That exact ambiguity, "no data" read as
   "not loaded", is the shape of [#1739](https://github.com/repowise-dev/repowise/issues/1739).
2. **It would be a transitive closure.** On this repository tests reach 1,630 of
   2,509 production files; materialising that is O(tests x sources) rows that go
   stale the moment the graph moves, to answer a query that is a bounded
   breadth-first search over rows already in the database.

The cost of deriving it is what makes that affordable. Indexing gains nothing:
the health pass computes the whole-repo answer in one multi-source walk, measured
at **27 ms** on this 3,700-file repository, once per run and cached. The
per-change walk is one `IN` query at depth 1, over the same edge table
`pr_blast` already reads.

## Empty means unknown, not "no tests"

This is the contract that makes the whole layer safe to act on, and it is worth
stating plainly: **an empty test list never means the change is untested.**

Both tools carry an explicit discriminator alongside the list:

- `get_change_risk` sets `status` to `"map_present"` only when a map exists.
  With no map ingested it returns `status: "inferred"` when the graph can name
  candidate test files, and `status: "no_map"`, `map_present: false` and a
  summary that says "run the full suite" when it cannot. Other degraded statuses
  are `no_index` (nothing indexed yet), `unknown` (the git read failed), and
  `no_source_line_changes`. `basis` carries the same distinction in one word.
- `get_risk` and the REST blast-radius route consume one canonical
  `test_impact` population. The directive lifts capped typed recommendations and
  keeps `tests_to_run` as a non-contradictory compatibility projection. Coverage
  availability, freshness, and measured-map presence remain explicit at
  `pr_blast_radius.test_impact.coverage`; the legacy `guarding_tests` block is a
  projection of the same rows rather than a separate derivation.

Only `status: "map_present"` with an empty `tests` list means "the map exists and
nothing in it covers this change". That is a real finding. `status: "inferred"`
is not: it is a candidate list from the import graph, and passing everything in
it does not clear a change. Everything else is an
absence of evidence, and repowise says so rather than implying a clean bill of
health. The same rule runs through the CLI ("unknown, run the full suite"), the
`no_coverage_data` bucket, and the coverage lookup helpers, which document
absence as unknown at every layer.

## Configuration

The `coverage:` block in `.repowise/config.yaml`:

```yaml
coverage:
  auto_discover: true
  artifacts:                     # override the discovery globs
    - "coverage/lcov.info"
  format: lcov                   # skip format sniffing
  strip_prefix: "/build/src/"    # trim an absolute prefix from report paths
  reingest_on_update: false
```

Coverage is also auto-discovered and ingested during `init` and `update`, and
`repowise init --coverage-report <path>` takes explicit reports (repeatable).
Note that `--coverage-report` is test coverage, while `--coverage` controls
*documentation* breadth. Two different things, similarly named.

## CLI reference

| Command | What it does |
|---------|--------------|
| `repowise coverage add [PATHS...]` | Ingest reports. Auto-discovers when no path is given, merges multiple, builds the per-test map when contexts are present. Flags: `--path`, `--format`, `--verbose` |
| `repowise coverage status` | Coverage summary plus test-to-code map counts. Flag: `--path` |
| `repowise impacted-tests [REVSPEC]` | The tests a change exercises. Flags: `--path`, `--staged`, `--format` |

Full reference: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-coverage).

## See also

- [CODE_HEALTH.md](CODE_HEALTH.md): the coverage markers and how they deduct from the score.
- [CHANGE_RISK.md](CHANGE_RISK.md): the authoritative review percentile and supporting diff-shape score that `impacted_tests` rides alongside.
- [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_change_risk): full parameter and response reference.
- [CONFIG.md](../reference/CONFIG.md): the `coverage:` block.
