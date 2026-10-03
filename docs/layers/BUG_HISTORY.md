# Bug-fix history

Repowise records which files and which functions have been fixed, how
recently, and how often. You get a per-file fix count over a trailing 180-day
window, a per-symbol breakdown, a decayed **bug magnet** flag, and the age of
the most recent fix.

Churn says a file changes a lot, which is often just where the work is. Fix
history says it breaks a lot, which is more actionable. It does not name the
commit that introduced a bug (see [Accuracy and limits](#accuracy-and-limits)).

No LLM key. It is git history, a `-U0` diff pass and arithmetic, run inside the
git phase of `repowise init` and `repowise update`.

## Quick start

Nothing to turn on: index the repo and the counts are there.

```bash
repowise init
repowise risk --target src/pipeline/persist.py   # history for one file
```

From an agent:

```python
get_risk(targets=["src/pipeline/persist.py"])
# defect_profile: {fix_count, window, last_fix_days_ago, bug_magnet, top_symbols}
```

When an agent is about to edit a file with a recent run of fixes, the pre-edit
hook adds one line to its context:

```
[repowise] pipeline/persist.py has been bug-fixed 5x in the last 6 months,
last 2 weeks ago (bug magnet); mostly in run_pipeline.
```

In the dashboard, look at the health drawer's **Bug history** section and the
**Bug-fixed** facet on the Symbols page.

## Reading the results

| Field | Meaning |
|---|---|
| `fix_count` | Counted bug-fix commits that touched the file in the last 180 days |
| `last_fix_days_ago` / `last_fix_at` | Age of the most recent counted fix |
| `bug_magnet` | The decayed fix mass is 3.0 or more. Present only when true |
| `top_symbols` | Up to three functions or classes that absorbed the most fixes. Approximate |

The whole `defect_profile` block is omitted for a file with no counted fixes,
and on a repo with no fix data. Absence means no counted fix, not a clean bill
of health (see the limits below).

### What counts as a bug fix

A commit whose subject matches the fix keywords (`fix`, `bug`, `patch`,
`resolves`, `closes #N`, `fixes #N`, and none of `docs`, `typo`, `bump`,
`deps`, `chore`, `lint`, `format`, `style`) is a candidate. Its `-U0` diff is
then classified, and only `code_fix` counts:

| Shape | Counted | Example |
|---|---|---|
| `code_fix` | yes | production source lines changed |
| `test_only` | no | only test files |
| `doc_only` | no | only `.md` / `.mdx` / `.rst` / `.txt` / `.adoc` or `docs/` |
| `config_other` | no | only lockfiles, CI YAML, `*.config.*`, dotfile rc |
| `comment_only` | no | code files touched, but only comments or docstrings changed |
| `empty` | no | merge commits and no-op diffs |

The comment-only rule is line level: a commit that rewords a docstring in a
`.py` file is not a bug fix. Both the filtered count (`prior_defect_count`)
and the raw keyword count (`prior_defect_raw_count`) are stored, so the
difference stays inspectable.

### The bug magnet flag

Each file's counted fixes collapse into one decayed mass:

```
mass = sum(0.5 ^ (age_days / 90))
bug_magnet = mass >= 3.0
```

Only a same-day fix is worth a full 1.0, so three fixes spread over a couple of
weeks land near 2.9 and do not flag. In practice the flag needs four recent
fixes, or three very recent ones. It is meant to interrupt someone mid-edit, so
it is kept rare. The rollup is recomputed for the whole repo on every index and
update, because decay ages it even when nothing in the file changes.

### Recency is part of the claim

`bug_magnet` is a recency claim, so every surface that shows it also shows the
last-fix age. When `last_fix_at` is missing, the flag is dropped instead of
being shown without an age. The pre-edit hook stays silent when the last fix is
older than 180 days, however large the old count.

### Per-symbol counts

Each fix's replaced line ranges are matched against the symbol spans in the
file, which gives the per-function breakdown. Spans come from the current tree
and fix ranges from each fix's parent commit, so read the result as "mostly
here", not as an exact ledger. Every surface that shows symbol counts says so.

### On a change

`get_change_risk` adds a `fix_history.overlap` block: for each changed file,
how many past fixes it carries and how many of the change's lines fall inside a
past fix's replaced ranges. Fix counts are exact (one commit fixing three files
is one fix). Line overlap is marked `approximate`. Files are ranked by overlap
then count, and a `truncated` flag marks a cut list. See
[CHANGE_RISK.md](CHANGE_RISK.md).

## Tuning and suppressing

There are no settings for this layer. The 180-day window, the 90-day half-life
and the 3.0 threshold are fixed.

The pre-edit hook fires only when all three hold: at least 3 counted fixes, a
last fix within 180 days, and no earlier notice for that file in the same
session. A file with a governing architectural decision as well gets two lines,
never more. To turn hooks off, see [HOOKS.md](../agent/HOOKS.md).

## Accuracy and limits

- **Fix-shape classifier**: agrees with hand labels on 98.3% of 240 fix commits
  across repowise, flask, django and zod (60/60 on the first three, 56/60 on
  zod). The four misses are all "a code-extension file changed but no product
  code did".
- **Noise removed varies by repo**: 58.7% of keyword-matched fix commits on
  flask are not code fixes; 7.0% on repowise.
- **Per-symbol attribution** lands inside the indexed symbol range for 97.4% of
  django's 2,179 events, 96.3% of flask's 125, 96.8% of repowise's 1,145 and
  81.8% of zod's 337. Old fixes drift (symbol spans are current); zod's misses
  are test files with few named symbols.
- **No inducing commit is named.** File-level SZZ reached 74.5% top-candidate
  precision on 53 hand-judged rows, below the 80% bar for putting a commit and
  an author on screen. Counts and recency ship; blame does not.
- **Recall is keyword bound.** A fix whose subject does not say so is
  invisible. The shape filter improves precision only.
- **The window is fixed.** A file fixed heavily two years ago and quiet since
  reads as clean.
- **The health score does not change with filtering**: re-running the defect
  calibration with filtered counts moved pooled AUC by +0.0002.
- **In a workspace**, the hook's lookup does not filter by repository; where
  member repos lack their own `.repowise`, it falls silent.
- **Whether the hook notice changes agent behaviour has not been measured.**

Benchmark method for the health model that reads these counts:
[docs/BENCHMARKS.md](../BENCHMARKS.md#code-health-predicts-defects). The
measured SZZ attempt is written up in
[architecture/bug-history.md](../architecture/bug-history.md).

## Where it shows up

- **Agent hook**: the pre-edit notice, in both the Claude Code and Codex hook
  paths.
- **MCP**: `defect_profile` on `get_risk`; `fix_history.overlap` on
  `get_change_risk`.
- **CLI**: `repowise risk --target FILE`.
- **Dashboard**: the health drawer's collapsed **Bug history** section
  (per-symbol counts, last-fix age); the file signals panel's **Bug magnet**
  badge; a **Bug fixes** tile beside **Modifications** on symbol detail; a
  **Bug-fixed** facet and per-row chip on the Symbols list. See
  [DASHBOARD.md](../start/DASHBOARD.md).
- **VS Code**: one line on the file hover.

## Reference

MCP schemas: [`get_risk`](../agent/MCP_TOOLS.md#get_risk) and
[`get_change_risk`](../agent/MCP_TOOLS.md#get_change_risk). Hook behaviour:
[HOOKS.md](../agent/HOOKS.md).

## See also

- [architecture/bug-history.md](../architecture/bug-history.md): the
  `fix_events` table, the rollup, and the SZZ findings.
- [CODE_HEALTH.md](CODE_HEALTH.md): the `prior_defect` marker that reads the
  same counts.
- [CHANGE_RISK.md](CHANGE_RISK.md): `fix_history` on a change.
