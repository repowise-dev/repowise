# Bug-fix history internals

Contributor reference for how fix history is stored and rolled up, and the
record of the inducing-commit work that was measured and not shipped.
User-facing behaviour is in [docs/layers/BUG_HISTORY.md](../layers/BUG_HISTORY.md).

## Pipeline

1. **Candidate selection.** `is_fix_commit` in
   `core/ingestion/git_indexer/_constants.py` applies the keyword include and
   exclude rules. The same rule feeds the health `prior_defect` marker and
   change-risk `fix_history`, so every fix count in the product counts the same
   commits.
2. **Shape classification.** `classify_fix_shape` in
   `git_indexer/fix_shape.py` reads the candidate's `-U0` diff and assigns one
   of `SHAPE_KINDS` (`code_fix`, `test_only`, `doc_only`, `config_other`,
   `comment_only`, `empty`). Only `code_fix` is counted.
3. **Events.** `git_indexer/fix_events.py` writes one `fix_events` row per
   counted fix per file it touched.
4. **Attribution and rollup.** `core/analysis/health/fix_attribution.py`
   matches events to symbol spans and computes the per-file decayed mass and
   `bug_magnet`.

## The `fix_events` table

```
fix_events(repository_id, fix_sha, file_path, shape_kind,
           old_ranges_json, fixed_at, ...)
```

`old_ranges_json` holds the line ranges the fix replaced, numbered on the
fix's own parent commit. Storing events, not only counts, is what makes
per-symbol attribution and changed-line overlap possible without re-walking
git. A full index populates the table; `repowise update` extends it.

`GitMetadata` carries the rollups: `prior_defect_count` (filtered),
`prior_defect_raw_count` (keyword only), `bug_magnet`, `last_fix_at`, and
`fix_symbol_counts_json` (`path::Name -> count`, written in descending order).

## Rollup

`FIX_HALF_LIFE_DAYS = 90.0` and `BUG_MAGNET_MASS = 3.0` in
`fix_attribution.py`. The half-life came from a 60 / 90 / 180-day sweep on the
21-repo health calibration corpus: decay was the only lever that moved the
`prior_defect` coefficient (0.15 undecayed to 0.23 decayed), and 90 and 180
tied inside noise. The rollup recomputes the whole repo on every run because
decay changes it with no file change; on this repo that is two indexed queries
and about a quarter second.

Change-risk `fix_history` uses its own 365-day half-life
(`analysis/change_risk/fix_history.py`), tuned separately on its ranking gate.

`last_fix_at` must be serialized with a timezone. SQLite's DATETIME bind drops
`tzinfo`, and a naive ISO string parses as local time in JavaScript, which
pushes a fresh fix into the future west of UTC and makes its age vanish. The
fix lives once, upstream of every consumer.

## Inducing-commit attribution (SZZ): measured, not shipped

A refactor-aware SZZ pass was built: blame each fix's replaced lines back
through history, walk through behaviour-preserving moves, rank candidates by
overlap. On 53 hand-judged rows it reached **74.5%** top-candidate precision
against an 80% gate. A UI naming a commit and its author would be wrong about
one time in four with no way for the reader to tell which, so the pass was
removed and no surface names an inducing commit. Removing it also saved 9.1 s
of index time on this repo and 5.9 s on zod.

Findings worth keeping for anyone who tries again:

- **Refactor-aware blame helps.** Walking through moves lifted strict precision
  from 70.6% to 74.5%; all 14 initial false calls were refactors inheriting
  moved lines.
- **Overlap ranking beats earliest-commit ranking by 12 points** on judged
  rows. Earliest-commit first looked like 80%, but 7 of its 16 unjudged answers
  were the repo's initial commit, and all 7 were judged not plausible.
- **Two failure modes belong to SZZ itself**: an initial import has no earlier
  commit to name, and package splits that move lines wholesale while
  re-sorting imports defeat the carried-through test.

### AI vs human introduced bugs: not built

A per-KLOC comparison of defects introduced by agent-authored and
human-authored commits was gated on a concentration check, and failed it: the
top 5 inducing commits held 30.3% of traced mass against a 20% ceiling, with
the initial import alone at 13.9%. Any aggregate over inducing commits inherits
that concentration and mostly describes who authored the five largest commits.

What ships answers "this code has broken before, recently, and roughly here".
Nothing answers "and this commit caused it".
