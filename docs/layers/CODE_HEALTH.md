# Code Health

Repowise scores every source file from 1 to 10 and reports three co-equal
signals: **code health** (the defect-risk score, calibrated against real bug
history), **maintainability** and **performance risk**. It reads the parse tree,
the dependency graph and git history. It needs no LLM key and no network, and it
runs as part of `repowise init` and `repowise update`. The analysis is budgeted at
under 30 seconds for a 3,000-file repository, a budget a test enforces.

It finds code shapes and change patterns that go with bugs, slow code and code
that is hard to change. It does not run your code, measure runtime, or prove a
bug exists. A low score means "look here first", not "this is broken".

## Quick start

```bash
repowise init             # full index; scores every file
repowise health           # Fix first, KPIs, lowest-scoring files, top findings
repowise next             # the short "Do next" list across every layer
repowise update           # re-scores only the files that changed
```

```python
get_health()                                   # repo KPIs, worst files, top findings
get_health(targets=["src/api/server.py"])      # one file in detail
get_health(include=["refactoring", "accuracy"])
```

In the dashboard (`repowise serve`), open **Code Health** for a repository:
`/repos/<id>/code-health`. Its tabs are Overview, Performance, Findings, Tests,
Dead code, Doc drift, Security and Blast radius.

Illustrative `repowise health` output:

```
Fix first (3 of 41 eligible; tests, tooling and history-only files left out)
 1. Extract the retry branch from parse_config  now · effort M · +1.4 health
    src/config/loader.py:212
    CCN 47 and nesting 6 in a file changed 19 times this quarter.
    verify: pytest tests/unit/test_loader.py

Code health: 7.4/10 [Good] · Hotspot: 6.1/10 · Worst: 2.3/10 (src/config/loader.py)
```

## The three signals

All three are computed from one stream of findings by one scoring routine, each
with its own weights and caps. They are never blended into one number.

| Signal | What it answers | How its weights were set |
|---|---|---|
| **Code health** (defect risk) | Which files are most likely to carry the next bug | Fitted against a defect corpus; the headline number, the band and the badge |
| **Maintainability** | How hard the code is to read and change | Set by hand against its own caps; never fitted to bugs |
| **Performance risk** | Where the code's structure wastes work (I/O in loops, quadratic building, blocking calls) | Per-marker precision weights in one capped category |

Some smells fire often but predict bugs weakly (low cohesion, brain methods,
duplicated code, broad error handling). They are down-weighted in code health
and counted at full weight in maintainability, where they belong.

## Reading the score

Each file starts at 10. Every finding deducts by its severity times its marker
weight, deductions are capped per category, and the result is clamped to 1-10.
When a category passes its cap, its findings are scaled down together, so each
finding's reported impact still adds up to the file's score.

### Bands

| Band | Score | Meaning |
|---|---|---|
| **Excellent** | 8.5 and above | Low risk, easy to change |
| **Good** | 7.0 to 8.5 | Sound; nothing demands attention |
| **Fair** | 5.5 to 7.0 | Rising complexity or process risk |
| **Needs work** | 4.0 to 5.5 | Worth scheduling |
| **At risk** | under 4.0 | Where defects concentrate |

Bands are absolute, not percentiles, so a 6.2 means the same thing in every
repository. The 4.0 cutoff is the measured one: on the 2,770-file paired
benchmark corpus, files under 4.0 carried 16.9 times the per-file defect rate of
files at 8.0 and above, and 2.18 times the defects per thousand lines once size is
taken out. The other edges are reading points on the same scale.

Repository KPIs: **average health** and **hotspot health** (both weighted by code
lines; hotspot health covers only the files the git layer marks as hotspots), plus
the **worst performer**. A file in a language health has no support for is counted
as not analysed, never scored as a 10.

### What moves the score

The **53 registered detectors** fall into these families. Category caps keep any
one family from dominating a file.

| Family | Detectors | Cap on code health | What it catches |
|---|---:|---|---|
| Structural complexity | 6 | 2.5 | Deep nesting, brain methods, god classes, low cohesion, bumpy roads, complex conditions |
| Size and complexity | 3 | 1.5 | Long or high-CCN functions, primitive obsession |
| Duplication | 1 | 1.0 | Cloned blocks |
| Error handling | 1 | 0.5 | Swallowed or catch-all handlers, `unwrap`/panic, discarded Go errors |
| Change history | 10 | 1.0 to 3.5 | Churn, change entropy, co-change scatter, ownership, knowledge loss, prior bug fixes |
| Test coverage | 3 | 2.0 + 2.0 | Untested hotspots, coverage gaps, a continuous deduction for uncovered lines (needs a coverage report) |
| Test quality | 4 | 0.5 | Oversized or duplicated assertion blocks; two advisory markers (below) |
| SQL | 4 | maintainability or performance only | Overly complex SQL, `SELECT *`, `UPDATE`/`DELETE` with no `WHERE`, cartesian joins |
| Performance | 21 | performance only | See [Performance findings](#performance-findings) |

Twenty-five detectors move code health. The SQL and performance detectors move
only their own signals. Three markers are **advisory**: they are measured and
listed, carry an impact of exactly zero, and stay out of impact-ranked lists unless
you ask for the advisory dimension. Three more **governance** findings (an
ungoverned hotspot, stale governance, a contradictory decision) come from the
[decisions layer](DECISIONS.md) and never deduct.

**History amplifies code risk; it does not stand alone.** Git history can cost a
file at most 1.0 point, plus one point for each point its code-shape findings
deduct, up to 3.5. A simple file that changes often reads as busy, not broken, and
never drops below 9.0 from history alone. Each file stores its deduction in two
halves (structure and history) so you can see which one holds it down.

<details>
<summary>All 53 detectors by family</summary>

| Family | Detector ids |
|---|---|
| Structural complexity | `nested_complexity`, `brain_method`, `god_class`, `low_cohesion`, `bumpy_road`, `complex_conditional` |
| Size and complexity | `complex_method`, `large_method`, `primitive_obsession` |
| Duplication | `dry_violation` |
| Error handling | `error_handling` |
| Change history | `change_entropy`, `churn_risk`, `co_change_scatter`, `code_age_volatility`, `developer_congestion`, `function_hotspot`, `knowledge_loss`, `ownership_risk`, `prior_defect`, `hidden_coupling` (advisory) |
| Test coverage | `untested_hotspot`, `coverage_gap`, `coverage_gradient` |
| Test quality | `large_assertion_block`, `duplicated_assertion_block`, `assertion_free_test` (advisory), `mock_saturated_test` (advisory) |
| SQL | `sql_high_complexity`, `sql_select_star`, `sql_update_delete_without_where` (maintainability); `sql_cartesian_join` (performance) |
| Performance | `io_in_loop`, `nested_loop_with_io`, `serial_await_in_loop`, `blocking_sync_in_async`, `hot_path_sync_io`, `blocking_io_under_lock`, `lock_in_loop`, `resource_construction_in_loop`, `lazy_load_in_loop`, `unbounded_read_reduced_in_memory`, `string_concat_in_loop`, `nested_loop_quadratic`, `membership_test_against_list_in_loop`, `list_insert_zero_in_loop`, `regex_compile_in_loop`, `json_parse_in_loop`, `defer_in_loop`, `goroutine_in_unbounded_loop`, `array_spread_in_reduce`, `pd_concat_in_loop`, `pandas_iterrows_in_loop` |

</details>

### Ownership and knowledge-loss signals

Three history detectors read who has worked on a file, from git authorship alone:

| Detector | Fires when |
|---|---|
| `knowledge_loss` | A file that is still changing has one real author (bus factor 1), and that author is gone from recent commits or now makes under 20% of them |
| `ownership_risk` | A file with 5 or more commits has 3 or more minor contributors (each under 5% of commits) or no author above 40% |
| `developer_congestion` | 5 or more contributors, the file is in the top quarter of churn for the repository, and nobody owns half of it |

A file nobody touches never fires `knowledge_loss`: stable code with a departed
author is low risk. On a small team (few active contributors) the first two are
capped at low severity unless the file is a hotspot, since one owner per file is
the normal way such a team works. The `small-team` profile (see
[Configuration](#configuration)) lowers them further. Ownership and bus factor per
file also appear in `get_context(include=["ownership"])` and on the dashboard.

### Test-quality smells

`large_assertion_block` and `duplicated_assertion_block` fire only in test files
and share a 0.5 cap. Two advisory markers ask harder questions with no defect
corpus to calibrate against, so they never deduct:

- `assertion_free_test` (Python, TypeScript/JavaScript): a test that runs code and
  checks nothing. No assertion, no mock verification, no hand-written raise, no
  call to an assertion helper.
- `mock_saturated_test` (Python, TypeScript/JavaScript): mock setup statements per
  assertion in a test function.

Add house assertion helpers with the `assertions:` block in
[CONFIG.md](../reference/CONFIG.md#the-assertions-block). Per-language coverage is
in [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md#code-health-coverage).

### Scope and counts

Two controls decide what a figure describes. Both work on the CLI, MCP and every
dashboard page, and every response echoes them.

| Control | Values | Effect |
|---|---|---|
| `scope` | `all` (default), `production` | `production` drops test files. It lowers the headline because tests usually score higher than the code they cover. |
| `counts` | `everything` (default), `code_shape` | `code_shape` removes the history half of each deduction. Use it to ask "is my code getting better" after a week of refactoring raised churn. |

`everything` is the calibrated number every accuracy claim refers to, and the
badge always reports it.

## Fix first

**Fix first** is one ranked list of what to fix. `repowise health` prints the top
3, `get_health()` and the dashboard Overview tab show more, and `repowise next`
mixes its top items with work from other layers. Each item is one unit of work: a
file's refactoring plan, one performance intervention, or the strongest code-shape
finding in a file with no plan. It says what to change and where, why, the first
edit, the expected gain, the effort and the tests to run afterwards.

What stays out, and is counted as excluded with a reason: test files, tooling,
generated and vendored code, docs and examples, files whose only findings come
from git history, changes that would recover under half a point, items with no
concrete first edit, kinds that reviewers found not worth doing, code a sure
dead-code finding covers (the fix is to delete it), and functions a constant flag
switches off. A function counts as switched off when its body opens with
`if not FLAG: return ...` (or is one `if FLAG:` block) and `FLAG` is a
module-level `False`, `0` or `None` (`const FLAG = false` in TypeScript or
JavaScript) that nothing in the same file assigns again. Those functions are
counted as **dormant** next to the excluded totals rather than dropped silently.
Only the file itself is read: a flag imported from another module, or flipped
from outside with `setattr` or a test's monkeypatch, still reads as off, and the
function is counted as dormant, not hidden.

Items rank by value first (health recovered, or how far the code sits past size
and complexity bars), then by tier: `now` (worth doing, safe to start), `next`
(worth doing, needs judgment), `later` (real but can wait; the reason is shown).
No single kind takes more than 3 of the first 5 places while another kind has work
worth doing.

### Do next: `repowise next`

`repowise next` prints the short cross-layer list the dashboard calls **Do next**
and `get_overview` returns as `next_actions`. It reads stored actions from the
index, so it is instant and makes no analysis pass. Fix first items sit beside
work from other layers: a live secret, fresh regressions, fragile files, files
with knowledge loss, broken doc references, a dead-code batch, missing or stale
coverage, and decisions waiting for review.

```bash
repowise next                    # top 5 for this week (or the quarter when the week is empty)
repowise next --horizon quarter --all
repowise next --json             # the whole stored view
```

Rows are grouped as **Now**, **Worth planning** and **Improve what Repowise can
see** (steps such as adding a coverage report that make other answers sharper).
Run `repowise update` to refresh the list after you change code.

A fragile file says "add tests" only when it has no measured coverage and no
test reaches it in the code graph: the call and import walks impacted tests use,
plus one more import hop through a module only tests import (test support such
as a `*.test-support.ts`). When tests do reach it, the row asks to simplify its
lead function instead and names the walk that found them, as an inferred fact.
If the walk fails, the file gets neither row. Measured coverage below 80% still
reads "raise test coverage". When the index holds fewer than five commits (a
one-commit import, a shallow clone), the rules that rank files by history
(fragile files, bug-fix concentration, knowledge loss) stand down and the view
sets `context.history_too_short`; an index with no git history at all is not
flagged.

## Performance findings

Performance risk flags structure that wastes work. It does not measure runtime.

| Kind | Examples |
|---|---|
| I/O per loop iteration (N+1) | `io_in_loop`, `nested_loop_with_io`, `lazy_load_in_loop` (Django and sync SQLAlchemy) |
| Concurrency | `serial_await_in_loop`, `blocking_sync_in_async`, `blocking_io_under_lock`, `lock_in_loop`, `goroutine_in_unbounded_loop` |
| Repeated setup | `resource_construction_in_loop`, `regex_compile_in_loop`, `json_parse_in_loop`, `defer_in_loop` |
| Quadratic or wasteful data work | `string_concat_in_loop`, `nested_loop_quadratic`, `membership_test_against_list_in_loop`, `list_insert_zero_in_loop`, `array_spread_in_reduce`, pandas `concat`/`iterrows` in loops |
| Over-fetch | `unbounded_read_reduced_in_memory`, `sql_cartesian_join` |

The loop and the I/O call do not have to sit in the same function: Repowise follows
the resolved call graph up to three hops and attaches the caller-to-sink path.
Per-function linters cannot see that case.

Findings group into **opportunities**, one per place you would edit (the function
holding the loop, or a helper every caller goes through). Each opportunity carries
one state: `plan_ready` (a proven fix strategy), `advisory` (a strategy with
unproven prerequisites), `investigate` (no supported strategy) or `expected` (the
repetition is real and nothing should change, such as deleting N files, or the
function that holds it is switched off by a constant flag, reason `gated_off`). The
default queue holds production `plan_ready` and `advisory` opportunities, ranked by
cost first; state only breaks ties. Everything left out is counted by reason.

Performance analysis covers Python, TypeScript/JavaScript (including Vue and Svelte
scripts), Java, Go, C# (including Razor), Rust, Kotlin, Scala, Ruby, C++, Dart and
Pascal. A language without support emits no performance findings, never a guessed
one. Dynamic dispatch, monkeypatching and callbacks passed as values leave no call
edge, so those cases are missed.

## Refactoring

Each low-scoring file can carry deterministic refactoring plans (Extract Method,
Split File, Break Cycle, Performance Fix and others) with evidence, gain, effort and
blast radius. `repowise health --refactoring-targets` prints the stored queue. See
[REFACTORING.md](REFACTORING.md).

## Trends, coverage and badges

Every run stores a snapshot (the last 50 per repository) with repository KPIs and
per-file scores. Alerts read them: **declining** (the score fell 0.5 or more
against five snapshots back), **predicted decline** (three falls in a row), and
**history drag** (either of those, where only the git-history half moved, so there
is nothing in the code to act on).
`repowise health --trend` prints the last 10 snapshots. Trends stay quiet on thin
history.

Coverage reports feed the test-coverage detectors. LCOV, Cobertura, Clover,
JaCoCo, Go coverprofile and a Repowise JSON format are detected automatically.
With no report, only `untested_hotspot` runs, judging a hotspot by whether any test
reaches it; the line-coverage detectors stay silent, so no file is treated as
uncovered.

```bash
pytest --cov --cov-report=lcov:coverage.lcov
repowise coverage add coverage.lcov
repowise health
```

`repowise health --badge` prints README Markdown: a static shields.io badge for the
current score and the live form served by a running Repowise server
(`/api/repos/<id>/health/badge.json`, or `badge.svg`).

## Configuration

Per-file rules live in `.repowise/health-rules.json`:

```json
{
  "profile": "small-team",
  "disabled_biomarkers": ["primitive_obsession"],
  "severity_overrides": { "complex_method": "low" },
  "rules": [
    { "path": "tests/**/*.py", "disabled_biomarkers": ["large_method"] }
  ]
}
```

- `path` uses gitignore semantics over the repo-relative path. Every matching rule
  applies. A single `*` stops at a directory, so write `src/legacy/**` for a whole
  subtree.
- `small-team` lowers the people and process signals a 1-3 person repository
  cannot support. An explicit `severity_overrides` entry wins over the profile.
- Only severity labels are tunable. Weights and category caps are the calibrated
  constants the published accuracy rests on, so local policy cannot change what
  those numbers mean.

Full schema: [CONFIG.md](../reference/CONFIG.md#the-health-rulesjson-file).
Refactoring detectors and their confidence floor are set in the `refactoring:`
block of `.repowise/config.yaml`.

**Triage.** Each finding has a stable id that survives edits above it and changes
in its metric values. Mark a finding `acknowledged`, `false_positive` or `resolved`
in the dashboard, over REST, or with the opt-in `set_finding_status` MCP tool.
Triage survives re-indexing; a `resolved` finding that is detected again reopens.

## Accuracy and limits

Measured leakage-free: each file scored at a commit before the bug window, then
checked against the bug fixes of the next six months.

- **Defect prediction:** ROC AUC **0.737** (95% CI 0.683 to 0.787) across 21
  repositories, 9 languages and 2,826 files; per-repository range 0.55 to 0.86.
- **Held-out data:** 0.761 and 0.776 on PROMISE jEdit 4.0 and 4.1, a public dataset
  with no git history that played no part in calibration.
- **Against CodeScene** (same 2,770 files, same commit, same labels): recall at a
  20%-of-lines review budget **0.173 vs 0.074** (p = 0.003), surfacing 2.3 times the
  defects. AUC 0.731 vs 0.705 is not significant (p = 0.054), and CodeScene's
  precision at that budget is a tie (0.636 vs 0.580, p = 0.64).
- **Calibration:** marker weights come from a regression on that defect corpus with
  file size as an explicit control, so a marker earns weight only for risk beyond
  being big.
- **Not better than file size at discrimination:** lines of code alone scores AUC
  0.742 (a tie). The gain is in ordering a fixed review budget and in saying why.
- **Weak among files of similar size:** within size quartiles AUC runs 0.525 to
  0.718; the signal holds cleanly only in the largest quartile.
- **Business impact not replicated:** the score did not track GitHub merge time on
  open data.
- **On your repository:** after an index, Repowise reports how many of the 20
  lowest-scoring files had a bug fix in the last 180 days against the repository's
  base rate (`get_health(include=["accuracy"])`). It is an association on indexed
  history, not a forward test, and stays silent below 25 scored files or 5 fixed
  ones.
- **Performance risk** is a static, high-precision, low-recall signal. It
  under-reports by design.

Method, sample sizes and every test: [BENCHMARKS.md](../BENCHMARKS.md#code-health-predicts-defects).
How the calibration works: [architecture/code-health.md](../architecture/code-health.md#62-validation-and-calibration).

## Where it shows up

| Surface | What you get |
|---|---|
| CLI | `repowise health`, `repowise next`, a line in `repowise status` |
| MCP | `get_health`; health fields on `get_risk`, `get_context(include=["health"])` and `get_overview` |
| Dashboard | Code Health page: Overview (Fix first), Performance, Findings, Tests |
| CLAUDE.md | Health KPIs and Fix first items in the generated file |
| Editor | Per-file findings in the [VS Code extension](../agent/VSCODE.md) |

## Reference

`repowise health [PATH]`

| Flag | Effect |
|---|---|
| `--file PATH` | Report one file (repo-relative path) |
| `--module PREFIX` | Report only files under this path prefix |
| `--scope all\|production` | Population to report on (default `all`) |
| `--counts everything\|code_shape` | Include or drop the history half of the deduction (default `everything`) |
| `--format table\|json\|md` | Output format; `json` and `md` do not write to the index |
| `--refactoring-targets` | Print the stored refactoring queue |
| `--recompute` | With `--refactoring-targets`: analyze the working tree in-process |
| `--generate-code SELECTOR` | Opt-in: generate code and a diff for one suggestion with the configured LLM (needs an API key) |
| `--trend` | Print the last 10 snapshots |
| `--badge` | Print README badge Markdown |
| `--repo ALIAS` / `--no-workspace` | Pick a workspace repository, or force single-repo mode |
| `-v`, `--verbose` | Show pipeline debug logs |

`repowise next [PATH]` takes `--horizon week|quarter`, `--all` (20 rows, not 5),
`--repo`, `--no-workspace` and `--format`/`--json`.

MCP: [`get_health`](../agent/MCP_TOOLS.md#get_health).

## See also

- [architecture/code-health.md](../architecture/code-health.md): internals, weight
  tables, calibration, Fix first and performance ranking rules.
- [BENCHMARKS.md](../BENCHMARKS.md): every published number with its sample and test.
- [REFACTORING.md](REFACTORING.md) · [TEST_INTELLIGENCE.md](TEST_INTELLIGENCE.md) ·
  [BUG_HISTORY.md](BUG_HISTORY.md) · [DOC_DRIFT.md](DOC_DRIFT.md) ·
  [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md#code-health-coverage)
