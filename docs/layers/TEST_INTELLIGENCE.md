# Test Intelligence

Ingest a coverage report and repowise can answer two questions your CI cannot:
which files are risky *and* untested, and which tests a given diff actually
exercises. That second one turns a 4,000-test suite into the 40 tests that guard
the change you just made.

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

The aggregate always gets stored. The map is only built when the report carries
per-test contexts. A report without contexts still ingests fine, it just skips
the map.

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
| Changed file has no coverage rows | A filename-pattern **guess** at its paired test, labelled `via: filename-pattern-guess` and printed in its own "NOT coverage-backed" table |
| Neither coverage nor a paired test | "unknown, run the full suite to be safe" |
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
   at all, it falls back to firing only when the file has no paired test file
   either, which is the conservative reading.

Severity is `CRITICAL` at 15% coverage or less with 10+ dependents, `HIGH` at one
of those two, `MEDIUM` otherwise. Its sibling `coverage_gap` handles the
has-coverage-but-thin case, and `coverage_gradient` applies a continuous
deduction proportional to the uncovered fraction, so a file is penalised in
proportion to how much of it is untested rather than only at a cliff.

## From an agent

Two MCP tools carry test information, at two different granularities.

**`get_risk(changed_files=[...])`** leads with a `directive` block whose
`tests_to_run` names the tests the map proves exercise the changed *files*. It is
file-level and scoped to the diff itself, capped at ten, and holds test node ids
rather than paths.

**`get_change_risk(revspec=...)`** returns `impacted_tests`, computed from the
changed *lines*, so it is a strictly narrower and more useful set:

```json
{
  "status": "map_present",
  "map_present": true,
  "tests": ["tests/test_auth.py::test_login", "..."],
  "total": 23,
  "truncated": true,
  "missing_tests": {
    "untested_changes": [{"source_file": "...", "uncovered_lines": [...]}],
    "stale_test_candidates": [...],
    "covered": [...],
    "no_coverage_data": [...]
  },
  "summary": "23 test(s) cover the changed lines; showing first 10."
}
```

The `missing_tests` buckets are the honest breakdown: `untested_changes` is the
strong signal (the file *is* in the map, but nothing covers the lines you
touched), `stale_test_candidates` flags covered lines whose guarding test file is
absent from the diff, and `no_coverage_data` means the file is simply not in the
map.

`get_change_risk` deliberately omits the CLI's filename-pattern guess. An agent
cannot tell a guess from real coverage, and `no_coverage_data` already reports
those files honestly.

## Empty means unknown, not "no tests"

This is the contract that makes the whole layer safe to act on, and it is worth
stating plainly: **an empty test list never means the change is untested.**

Both tools carry an explicit discriminator alongside the list:

- `get_change_risk` sets `status` to `"map_present"` only when a map exists.
  With no map ingested it returns `status: "no_map"`, `map_present: false`, and a
  summary that says "run the full suite" plus the two commands that build the
  map. Other degraded statuses are `no_index` (nothing indexed yet), `unknown`
  (the git read failed), and `no_source_line_changes`.
- `get_risk` produces `tests_to_run` from a `guarding_tests` block whose
  `map_present` flag is `false` when no map is ingested. Note that the
  `directive` lifts `tests_to_run` but not `map_present`, so read
  `pr_blast_radius.guarding_tests.map_present` to tell the two cases apart.

Only `status: "map_present"` with an empty `tests` list means "the map exists and
nothing in it covers this change". That is a real finding. Everything else is an
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
- [CHANGE_RISK.md](CHANGE_RISK.md): the risk score that `impacted_tests` rides alongside.
- [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_change_risk): full parameter and response reference.
- [CONFIG.md](../reference/CONFIG.md): the `coverage:` block.
