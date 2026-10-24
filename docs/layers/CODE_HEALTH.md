# Code Health

Repowise scores every source file from 1 to 10 and lists which files to fix first. It
reads the parse tree, the dependency graph and git history, needs no LLM key or network,
and runs inside `repowise init` and `repowise update`, budgeted (and tested) at under 30
seconds for 3,000 files. It finds code shapes and change patterns that go with bugs, slow
code and hard-to-change code; it does not run your code or prove a bug exists, so a low
score means "look here first", not "this is broken".

How it works: [architecture/code-health.md](../architecture/code-health.md).

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
 1. Extract the retry branch from parse_config now · effort M · +1.4 health on this file
    src/config/loader.py:212
    CCN 47 and nesting 6 in a file changed 19 times this quarter.
    verify: pytest tests/unit/test_loader.py

Code health: 7.42/10 [Good] · Production code: 6.93/10 [Fair] · Hotspot: 6.10/10 [Fair] · Worst: 2.3/10 [At risk] (src/config/loader.py)
```

Every headline score carries its band, and Production code leaves test files out. A
coverage figure measured at an earlier commit than the index says so on its row.

## The three signals

Three signals come from one stream of findings, each with its own weights and caps, and
are never blended into one number.

| Signal | What it answers | Weights |
|---|---|---|
| Code health (defect risk) | Which files are most likely to carry the next bug | Fitted against a defect corpus; the headline number, band and badge |
| Maintainability | How hard the code is to read and change | Set by hand; never fitted to bugs |
| Performance risk | Where the structure wastes work | Per-marker precision weights; see [PERFORMANCE.md](PERFORMANCE.md) |

Smells that fire often but predict bugs weakly (low cohesion, duplicated code, broad
error handling) are down-weighted in code health and counted in full in maintainability.

## Reading the score

Each file starts at 10; every finding deducts its severity times its marker weight, and
the result is clamped to 1-10. Deductions are bounded per category; code categories bend toward their bound rather than
stopping at it, so shrinking a critical function raises the score while it stays
critical, and several findings on one function count mostly once
([scoring](../architecture/code-health.md#6-scoring-scoringpy)).

| Band | Score | Meaning |
|---|---|---|
| Excellent | 8.5 and above | Low risk, easy to change |
| Good | 7.0 to 8.5 | Sound; nothing demands attention |
| Fair | 5.5 to 7.0 | Rising complexity or process risk |
| Needs work | 4.0 to 5.5 | Worth scheduling |
| At risk | under 4.0 | Where defects concentrate |

Bands are absolute, so a 6.2 means the same everywhere. On the 2,770-file benchmark
corpus, files under 4.0 carry 16.9 times the per-file defect rate of files at 8.0 and
above. Repository KPIs are average health, hotspot health (hotspot files only; both
weighted by code lines) and the worst performer. A file in an unsupported language is
not analysed, never scored as a 10.

| Control | Values | Effect |
|---|---|---|
| `scope` | `all` (default), `production` | `production` drops test files, which lowers the headline because tests usually score higher |
| `counts` | `everything` (default), `code_shape` | `code_shape` removes the git-history half of each deduction, to see whether code improved after a refactor raised churn |

Every response echoes both. `everything` is the calibrated number every accuracy claim
refers to, and the badge always reports it.

## Fix first

Fix first is one ranked list of what to fix: `repowise health` prints the top 3,
`get_health()` and the dashboard Map tab show more. Each item is one unit of work (a
refactoring plan, one performance intervention, or the strongest code-shape finding in a
file with no plan) with what to change and where, why, the first edit, the expected
gain, the effort and the tests to run.

| Rule | What it means |
|---|---|
| Left out, counted by reason | Tests, tooling, generated and vendored code, docs and examples; history-only files; gains under half a point or with no concrete first edit; kinds reviewers rejected; code a sure dead-code finding covers |
| Also left out | Dormant functions (switched off by a constant flag); print and prompt flows; functions under CCN 80 and 200 lines in a quiet file with no bug fixes, which stay in the refactoring list ([every reason](../architecture/code-health.md#63-fix-first-ranking-fix_first-worthpy)) |
| Ranking | Value first (how far past size and complexity bars), then tier; a frequently changed function beats an equally complex quiet one and says how many commits changed it |
| Performance ranking | By what runs it: a request or message handler, then a scheduled job, then code with no role evidence |
| Variety | No kind takes more than 3 of the first 5 places while another has work worth doing |
| Tiers | `now` (worth doing, safe to start), `next` (needs judgment), `later` (can wait; reason shown); a refactoring stays `later` while any step's plan leaves a function above the break-up bar |
| First step | A stored Extract Method plan that shrinks the function leads (title names the first lift; next call opens the plan), else "Break up" with the function's shape and what splitting buys |
| Shown | A `now` or `next` item shows its first step and check command on the card and over MCP (the lead, and each `only=["fix_first"]` item); Extract Method shows the helper header and replacing call ([how](../architecture/code-health.md#fix-first-steps)) |
| Verify | When no test reaches the code, add a characterization test first |
| Counts | Counted the same everywhere (open, in scope, eligible, due, shown, each exclusion by reason), so the dashboard, `get_health` and `repowise health --format json` (`queue_counts`) agree |

### Do next

`repowise next` prints the cross-layer list the dashboard calls Do next and
`get_overview` returns as `next_actions`, from stored actions, so it is instant.

```bash
repowise next                          # top 5 this week (the quarter if the week is empty)
repowise next --horizon quarter --all
repowise next --json                   # the whole stored view
```

- Fix first items sit beside a live secret, fresh regressions, fragile files, fading
  ownership, broken doc references, a dead-code batch, missing or stale coverage and
  decisions waiting for review, grouped as Now, Worth planning and Improve what Repowise
  can see.
- Rows rank by value times confidence over effort; the top due Fix first item leads its
  group. A row's severity is the worst of its findings, capped at high. Dismissing or
  snoozing takes effect at once.
- A fresh regression is in Now only when this week's commits added a proven performance
  cause (one in the performance queue) or lost measured coverage; the long, complex or nested code a week leaves is one
  row to plan.
- A fragile file asks for tests when no coverage or test reaches it, else to simplify its
  lead function if Fix first would break it up, else to review why fixes keep landing.
  A folder where fixes concentrate names its most fixed file's first step, or is left
  out. A coverage report 14 days older than the latest commit gets a refresh row. Under
  five commits, history-ranked rows are left out.

## Findings

Findings are the observations behind the score: the dashboard Findings tab lists them by
dimension and severity, and `get_health(targets=[...])` per file. Each has a stable id
that survives edits above it. Mark one `acknowledged`, `false_positive` or `resolved` in
the dashboard, over REST or with the opt-in `set_finding_status` MCP tool; triage
survives re-indexing, and a re-detected `resolved` finding reopens.

### Detector families

The 59 detectors fall into families whose caps keep any one from dominating a file.
Every id with its threshold and weight:
[the full roster](../architecture/code-health.md#the-full-roster).

| Family | Detectors | Cap | Catches |
|---|---:|---|---|
| Structural complexity | 6 | 2.5 | Deep nesting, brain methods (listed, not scored), god classes, low cohesion, repeated nesting, complex conditions |
| Size and complexity | 3 | 1.5 | Long or high-CCN functions, primitive obsession |
| Duplication | 1 | 1.0 | Cloned blocks |
| Error handling | 1 | 0.5 | Swallowed or catch-all handlers, `unwrap`/panic, discarded Go errors |
| Change history | 10 | 1.0 to 3.5 | Churn, change entropy, co-change scatter, ownership, fading ownership, prior bug fixes |
| Test coverage | 4 | 2.0 + 2.0 | Untested hotspots, coverage gaps, uncovered lines (needs a report), untested complex functions (advisory) |
| Test quality | 4 | 0.5 | Oversized or duplicated assertion blocks; two advisory markers |
| SQL | 4 | none | Complex SQL, `SELECT *`, `UPDATE`/`DELETE` with no `WHERE`, cartesian joins |
| Apex | 4 | none | SOQL injection, a class with queries and no sharing keyword, hardcoded record Ids, `SeeAllData=true` tests; all advisory |
| COBOL | 1 | none | `ALTER`, `GO TO ... DEPENDING ON`, and `GO TO` out of a paragraph's `PERFORM THRU` range; advisory |
| Performance | 21 | none | See [PERFORMANCE.md](PERFORMANCE.md) |

- Caps are code-health caps; SQL deducts only in maintainability (2.0) and performance
  findings, cartesian joins included, only in performance risk (2.0).
- Twenty-four detectors move code health. Nine are advisory: listed with zero impact,
  out of impact-ranked lists unless you ask for the advisory dimension. Three governance
  findings from the [decisions layer](DECISIONS.md) never deduct.
- `dry_violation` and `duplicated_assertion_block` stay off every default surface (overview, priorities, MCP, REST lists) until
  a re-measurement clears their precision; they are still scored. Assertion-block
  markers fire only in test files; house assertion helpers go in the
  [`assertions:` block](../reference/CONFIG.md#the-assertions-block).
- History amplifies code risk: alone it costs a file at most 1.0 point, so a simple file
  that changes often reads as busy, not broken, and never drops below 9.0 from history
  alone. On a small team, `knowledge_loss` and `ownership_risk` stay low off a hotspot,
  and the `small-team` profile lowers them further.
- Per-language coverage: [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md#code-health-coverage).

## Trends, coverage and badges

- Every `init`, `update` and `health --recompute` stores a snapshot (the last 50 per
  repository); `repowise health --trend` prints the last 10 with declining, predicted
  decline and history-drag alerts ([rules](../architecture/code-health.md#8-trends-trendspy)).
- `analyzer_version` (JSON output, `--trend` rows, `get_health`) names the scoring a
  number came from, so a score that moves with it may reflect new scoring, not new code;
  it is `null` for analysis stored before it was recorded.
- Coverage reports feed the coverage detectors (`repowise coverage add coverage.lcov`);
  with none, only `untested_hotspot` and the reach-tier `untested_complexity` (hidden by
  default) run, and no file is treated as uncovered
  ([coverage findings](TEST_INTELLIGENCE.md#coverage-in-code-health)).
- After adding a report, `repowise health --recompute` analyzes the working tree instead
  of the stored analysis and, on a whole-repo table run, writes the result to the index.
- `repowise health --badge` prints README badge Markdown.

## In CI: `repowise health check`

`repowise health check` analyses the changed files at the base and the head, with no
index, and fails only on a code-shape finding the change wrote.

```bash
repowise health check origin/main...HEAD
```

- It exits `1` only on a new code-shape finding at high severity or above (`--fail-at`
  moves the floor). A lower one warns, as does a worsened one (`--fail-on worsened`
  fails on it); a moved or renamed one never fails.
- History markers, coverage and file scores never decide it, so a squash merge cannot
  change the verdict; `.repowise/health-rules.json` is read from the base, so a change
  cannot loosen its own gate.
- It warns when the change adds a suppression comment, skips or removes tests, loosens a
  coverage threshold, or adds a test-environment branch to production code;
  `--diff-risk error` makes those fail.

Exit codes, formats and setup: [Repowise in CI](../start/CI.md).

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

`path` uses gitignore semantics and every matching rule applies. `small-team` lowers the
people signals a 1-3 person repository cannot support; an explicit `severity_overrides`
entry wins over it. Only severity labels are tunable: weights and caps are the
calibrated constants the published accuracy rests on. Full schema:
[CONFIG.md](../reference/CONFIG.md#the-health-rulesjson-file).

## Accuracy and limits

Measured without leakage: files scored before the bug window, checked against the next
six months of bug fixes ([method](../architecture/code-health.md#62-validation-and-calibration)).

- ROC AUC 0.737 (95% CI 0.683 to 0.787) across 21 repositories, 9 languages and 2,826
  files; held out on PROMISE jEdit, 0.761 and 0.776.
- Against a commercial code-health tool on the same files, recall at a 20%-of-lines
  review budget is 0.173 vs 0.074 (p = 0.003), 2.3 times the defects; AUC and precision
  are ties.
- Lines of code alone scores AUC 0.742, a tie: the gain is in ordering a fixed review
  budget and saying why. Within size quartiles AUC is 0.525 to 0.718.
- On open data, the score does not track GitHub merge time.
- `get_health(include=["accuracy"])` reports how many of your 20 lowest-scoring files had
  a bug fix in the last 180 days against the base rate: an association, not a forward
  test, silent below 25 scored files or 5 fixed ones.

Every test: [BENCHMARKS.md](../BENCHMARKS.md#code-health-predicts-defects).

## More

| Where | What |
|---|---|
| CLI | `repowise health`, `repowise next`, `repowise status`; flags in [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-health-path) |
| MCP | [`get_health`](../agent/MCP_TOOLS.md#get_health); health fields on `get_risk`, `get_context` and `get_overview` |
| Editor | Per-file findings in the [VS Code extension](../agent/VSCODE.md) |
| Refactoring | Plans per file from `repowise health --refactoring-targets` ([REFACTORING.md](REFACTORING.md)). Break Cycle is advisory: beside a file's steps, never one, never in Fix first; a Split File or Extract Class plan with an unnamed group is held the same way (`needs_design`) |
| Layers | [PERFORMANCE.md](PERFORMANCE.md), [TEST_INTELLIGENCE.md](TEST_INTELLIGENCE.md), [BUG_HISTORY.md](BUG_HISTORY.md), [DOC_DRIFT.md](DOC_DRIFT.md) |
