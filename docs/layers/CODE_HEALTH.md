# Code Health

Repowise scores every source file from 1 to 10 and lists which files to fix
first. It reads the parse tree, the dependency graph and git history, needs no LLM
key or network, and runs inside `repowise init` and `repowise update`. The
analysis is budgeted at under 30 seconds for 3,000 files, and a test enforces it.

It finds code shapes and change patterns that go with bugs, slow code and code
that is hard to change. It does not run your code or prove a bug exists. A low
score means "look here first", not "this is broken".

## Quick start

```bash
repowise init             # full index; scores every file
repowise health           # Fix first, KPIs, lowest-scoring files, top findings (stored)
repowise next             # the short "Do next" list across every layer
repowise update           # re-scores only the files that changed
```

```python
get_health()                                   # repo KPIs, worst files, top findings
get_health(targets=["src/api/server.py"])      # one file in detail
```

The dashboard (`repowise serve`) has a Code Health page per repository at
`/repos/<id>/code-health`.

```
Fix first (3 of 41 eligible items, 12 due; 230 in the inventory, tests, tooling and history-only files left out)
 1. Extract the retry branch from parse_config  now, effort M, +1.4 health
    src/config/loader.py:212
    CCN 47 and nesting 6 in a file changed 19 times this quarter.
    verify: pytest tests/unit/test_loader.py

Code health: 7.4/10 [Good], Hotspot: 6.1/10, Worst: 2.3/10 (src/config/loader.py)
```

## The three signals

They come from one stream of findings, each with its own weights and caps. They
are never blended into one number.

| Signal | What it answers | Weights |
|---|---|---|
| **Code health** (defect risk) | Which files are most likely to carry the next bug | Fitted against a defect corpus; the headline number, band and badge |
| **Maintainability** | How hard the code is to read and change | Set by hand; never fitted to bugs |
| **Performance risk** | Where the structure wastes work | Per-marker precision weights; see [PERFORMANCE.md](PERFORMANCE.md) |

Smells that fire often but predict bugs weakly (low cohesion, brain methods,
duplicated code, broad error handling) are down-weighted in code health and counted
in full in maintainability.

## Reading the score

Each file starts at 10. Every finding deducts its severity times its marker weight,
deductions are capped per category, and the result is clamped to 1-10.

| Band | Score | Meaning |
|---|---|---|
| **Excellent** | 8.5 and above | Low risk, easy to change |
| **Good** | 7.0 to 8.5 | Sound; nothing demands attention |
| **Fair** | 5.5 to 7.0 | Rising complexity or process risk |
| **Needs work** | 4.0 to 5.5 | Worth scheduling |
| **At risk** | under 4.0 | Where defects concentrate |

Bands are absolute, so a 6.2 means the same in every repository. On the 2,770-file
benchmark corpus, files under 4.0 carried 16.9 times the per-file defect rate of
files at 8.0 and above.

Repository KPIs are **average health**, **hotspot health** (hotspot files only; both
weighted by code lines) and the **worst performer**. A file in an unsupported
language is counted as not analysed, never scored as a 10.

Two controls decide what a figure describes. Every response echoes them.

| Control | Values | Effect |
|---|---|---|
| `scope` | `all` (default), `production` | `production` drops test files, which lowers the headline because tests usually score higher |
| `counts` | `everything` (default), `code_shape` | `code_shape` removes the git-history half of each deduction, to see whether your code improved after a refactor raised churn |

`everything` is the calibrated number every accuracy claim refers to, and the badge
always reports it.

## Fix first

**Fix first** is one ranked list of what to fix. `repowise health` prints the top 3;
`get_health()` and the dashboard Overview tab show more. Each item is one unit of
work: a refactoring plan, one performance intervention, or the strongest code-shape
finding in a file with no plan. It gives what to change and where, why, the first
edit, the expected gain, the effort and the tests to run.

Left out, and counted with a reason:

- test files, tooling, generated and vendored code, docs and examples
- files whose only findings come from git history
- changes that would recover under half a point, and items with no concrete first
  edit
- kinds that reviewers found not worth doing
- code a sure dead-code finding covers (the fix is to delete it)
- dormant functions, which a constant flag switches off

A function is dormant when its body opens with `if not FLAG: return ...` (or is one
`if FLAG:` block) and `FLAG` is a module-level `False`, `0` or `None` (`const FLAG =
false` in TypeScript or JavaScript) that nothing in the same file assigns again.
Dormant functions are counted next to the excluded totals, not dropped silently.
Only the file itself is read: a flag imported from another module, or flipped from
outside with `setattr` or a test's monkeypatch, still reads as off.

Items rank by value first (how far the code sits past size and complexity bars,
one step more in a widely imported file), then by tier. Within a value, the
complexity a fix removes times how widely the file is imported decides, so a
4,000-line loop outranks a long print routine. A performance fix ranks by what runs
it: a request or message handler, then a scheduled job, then code with no role
evidence. No kind takes more than 3 of the first 5 places while another has work
worth doing.

| Tier | Meaning |
|---|---|
| `now` | Worth doing, safe to start |
| `next` | Worth doing, needs judgment |
| `later` | Real but can wait; the reason is shown |

Findings, performance causes, refactoring plans and Fix first items are counted the
same way everywhere: open in the inventory, in scope, eligible, due (tier `now` or
`next`) and shown, with every exclusion named by its reason. The numbers on the
dashboard, in `get_health` and in `repowise health --format json` (`queue_counts`) agree.

### Do next

`repowise next` prints the cross-layer list the dashboard calls **Do next** and
`get_overview` returns as `next_actions`. It reads stored actions, so it is instant.
Fix first items sit beside a live secret, fresh regressions, fragile files, files
with knowledge loss, broken doc references, a dead-code batch, missing or stale
coverage, and decisions waiting for review. Rows are grouped as **Now**, **Worth
planning** and **Improve what Repowise can see**. Within a group, rows rank by value
times confidence over effort, and the top due Fix first item leads its group, so Do
next and Fix first lead with the same work. Dismissing or
snoozing a row takes effect at once.

A fragile file asks for tests only when no measured coverage and no test in the
code graph reaches it; otherwise the row asks you to simplify its lead function.
An index with fewer than five commits leaves out the rows ranked by history.

```bash
repowise next                          # top 5 for this week (or the quarter when the week is empty)
repowise next --horizon quarter --all
repowise next --json                   # the whole stored view
```

## Findings

Findings are the observations behind the score. The dashboard **Findings** tab lists
them by dimension and severity, and `get_health(targets=[...])` returns them per
file. Each has a stable id that survives edits above it. Mark one `acknowledged`,
`false_positive` or `resolved` in the dashboard, over REST, or with the opt-in
`set_finding_status` MCP tool. Triage survives re-indexing, and a `resolved` finding
that is detected again reopens.

The 53 detectors fall into families. Category caps keep any one family from
dominating a file.

| Family | Detectors | Cap | Catches |
|---|---:|---|---|
| Structural complexity | 6 | 2.5 | Deep nesting, brain methods, god classes, low cohesion, bumpy roads, complex conditions |
| Size and complexity | 3 | 1.5 | Long or high-CCN functions, primitive obsession |
| Duplication | 1 | 1.0 | Cloned blocks |
| Error handling | 1 | 0.5 | Swallowed or catch-all handlers, `unwrap`/panic, discarded Go errors |
| Change history | 10 | 1.0 to 3.5 | Churn, change entropy, co-change scatter, ownership, knowledge loss, prior bug fixes |
| Test coverage | 3 | 2.0 + 2.0 | Untested hotspots, coverage gaps, uncovered lines (needs a coverage report) |
| Test quality | 4 | 0.5 | Oversized or duplicated assertion blocks; two advisory markers |
| SQL | 4 | none | Complex SQL, `SELECT *`, `UPDATE`/`DELETE` with no `WHERE`, cartesian joins; maintainability or performance only |
| Performance | 21 | none | See [PERFORMANCE.md](PERFORMANCE.md); performance only |

Twenty-five detectors move code health. Three markers are **advisory**: they are
listed, carry zero impact, and stay out of impact-ranked lists unless you ask for the
advisory dimension. Three **governance** findings (an ungoverned hotspot, stale
governance, a contradictory decision) come from the [decisions layer](DECISIONS.md)
and never deduct. Every detector id: [architecture/code-health.md](../architecture/code-health.md#the-full-roster).

**History amplifies code risk; it does not stand alone.** Git history costs a file at
most 1.0 point, plus one point for each point its code-shape findings deduct, up to
3.5. A simple file that changes often reads as busy, not broken, and never drops
below 9.0 from history alone.

| Detector | Fires when |
|---|---|
| `knowledge_loss` | A file still changing has one real author (bus factor 1) who is gone from recent commits or makes under 20% of them |
| `ownership_risk` | 5 or more commits and either 3 or more minor contributors (each under 5%) or no author above 40% |
| `developer_congestion` | 5 or more contributors, top quarter of churn, and nobody owns half |
| `assertion_free_test` | Advisory. A Python or TypeScript/JavaScript test that checks nothing |
| `mock_saturated_test` | Advisory. Many mock setup statements per assertion (same languages) |

On a small team, `knowledge_loss` and `ownership_risk` are capped at low severity
unless the file is a hotspot, and the `small-team` profile lowers them further.
`large_assertion_block` and `duplicated_assertion_block` fire only in test files.
House assertion helpers go in the [`assertions:` block](../reference/CONFIG.md#the-assertions-block).
Per-language coverage: [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md#code-health-coverage).

## Trends, coverage and badges

Every `init`, `update` and `health --recompute` stores a snapshot (the last 50 per repository). Alerts: **declining** (fell
0.5 or more against five snapshots back), **predicted decline** (three falls in a
row) and **history drag** (either, where only the git-history half moved, so there
is nothing in the code to act on). `repowise health --trend` prints the last 10.

Coverage reports (LCOV, Cobertura, Clover, JaCoCo, Go coverprofile, Repowise JSON)
feed the coverage detectors: `repowise coverage add coverage.lcov`. With no report,
only `untested_hotspot` runs, and no file is treated as uncovered.
`repowise health --badge` prints README badge Markdown. After adding a report, run `repowise health --recompute`, which analyzes the working tree instead of reading the stored analysis and, on a whole-repo table run, writes the result to the index.

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

`path` uses gitignore semantics, and every matching rule applies. `small-team`
lowers the people signals a 1-3 person repository cannot support, and an explicit
`severity_overrides` entry wins over it. Only severity labels are tunable; weights
and caps are the calibrated constants the published accuracy rests on. Full schema:
[CONFIG.md](../reference/CONFIG.md#the-health-rulesjson-file).

## Accuracy and limits

Measured without leakage: files scored before the bug window, checked against the
next six months of bug fixes.

- ROC AUC 0.737 (95% CI 0.683 to 0.787) across 21 repositories, 9 languages and
  2,826 files. Held out on PROMISE jEdit: 0.761 and 0.776.
- Against CodeScene on the same files, recall at a 20%-of-lines review budget is
  0.173 vs 0.074 (p = 0.003), 2.3 times the defects. AUC and precision are ties.
- Lines of code alone scores AUC 0.742, a tie. The gain is in ordering a fixed
  review budget and saying why. Within size quartiles AUC is 0.525 to 0.718.
- The score did not track GitHub merge time on open data.
- `get_health(include=["accuracy"])` reports how many of your 20 lowest-scoring files
  had a bug fix in the last 180 days against the base rate. It is an association,
  not a forward test, and stays silent below 25 scored files or 5 fixed ones.

Every test: [BENCHMARKS.md](../BENCHMARKS.md#code-health-predicts-defects).

## More

| Where | What |
|---|---|
| CLI | `repowise health`, `repowise next`, `repowise status`; flags in [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-health-path) |
| MCP | [`get_health`](../agent/MCP_TOOLS.md#get_health); health fields on `get_risk`, `get_context` and `get_overview` |
| Editor | Per-file findings in the [VS Code extension](../agent/VSCODE.md) |
| Refactoring | Plans per file, from `repowise health --refactoring-targets`. Break Cycle is advisory: shown beside a file's steps, never as one, and never in Fix first. A Split File or Extract Class plan with an unnamed group is held the same way (`needs_design`). See [REFACTORING.md](REFACTORING.md) |
| Layers | [PERFORMANCE.md](PERFORMANCE.md), [REFACTORING.md](REFACTORING.md), [TEST_INTELLIGENCE.md](TEST_INTELLIGENCE.md), [BUG_HISTORY.md](BUG_HISTORY.md), [DOC_DRIFT.md](DOC_DRIFT.md) |
| Internals | [architecture/code-health.md](../architecture/code-health.md) |
