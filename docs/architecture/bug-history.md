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

## Measurements

- **Fix-shape classifier.** 98.3% agreement with hand labels on 240 fix
  commits: 60/60 on repowise, flask and django each, 56/60 on zod. The four
  misses are all "a code-extension file changed but no product code did".
- **Per-symbol attribution.** Inside the indexed symbol range for 97.4% of
  django's 2,179 events, 96.3% of flask's 125, 96.8% of repowise's 1,145 and
  81.8% of zod's 337. Old fixes drift because symbol spans are current; zod's
  misses are test files with few named symbols.
- **Filtering and the health score.** Re-running the defect calibration with
  filtered counts moves pooled AUC by +0.0002, so the health score does not
  change with filtering.

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

### Change outcomes: explicit links only

A Change Record's `outcomes` item (`core/changes/outcomes.py`) links a change
only to later commits that name it: reverts, by the decision-supersession
rules, and fixes that call it the cause in their own words. Hand-checked on 14
repositories, reverts held 16 of 16, with `Fixes: <sha>` trailers resolving to
the quoted commit 59 of 59 on the kernel. The kept phrases ("regression
from/in/since", "caused by", "broken by") were judged blind on 67 links from
django, pandas, litellm and langfuse: 67 of 67 named the change as the cause
(95% Wilson interval 94.6% to 100%; regression 47, caused by 12, broken by 8). `Fixes #N` (about 2 of
7), "introduced in #N" (about 5 of 14) and a fix commit quoting a sha (0 of
15) fell below 90% and are not read.

## Suspects ranking: measured

`repowise suspects` (`core/suspects/`) ranks the Change Records merged before a
symptom and names no cause, so it stays clear of the inducing-commit claim above.
It was preregistered (gate: the explicitly named change in the top 5 for at least
60% of issues pooled, at least 15 points over the better of recency and shared
files, no repo losing on MRR) with ground truth from reverts and fixes that name
the change (`This reverts commit`, `Regression in <sha>`, `Fixes: <sha>`), never
from blame. Each link class was hand-checked blind with 20% decoys (no decoy
passed); every class used scored at least 95%, and django's `Fixes #N` class
(25%) was dropped because `#N` names a Trac ticket there.

Symptom: the closed issue's text as filed; candidates: the 14 days before it;
index facts from a monthly snapshot before the issue. Trailers that name people
(`Signed-off-by`, `Co-authored-by`, `Reviewed-By`, any trailer with an email)
are excluded from the text index; the figures below are from the rerun with
them excluded, on the same items and snapshots (before: node MRR 0.354, pooled
MRR 0.450, interval +0.256 to +0.535; every hit@5 unchanged).

| Arm (hit@5 / MRR) | django (18) | node (25) | pooled (43) |
|---|---|---|---|
| Fused ranking | 0.833 / 0.582 | 0.560 / 0.358 | 0.674 / 0.452 |
| Shared files | 0.389 / 0.333 | 0.200 / 0.197 | 0.279 / 0.254 |
| Newest first | 0.111 / 0.103 | 0.160 / 0.127 | 0.140 / 0.117 |

The lift over shared files is +0.395 hit@5 (95% bootstrap interval +0.233 to
+0.558). Two limits come with it: most regressions surface later than 14 days
(window misses: django 91%, node 62%), and those issues are outside the scored
set. Text match carries most of the ranking; dropping recency scored higher on
these repos, but the test repos were scored once and a change to the fusion
needs a new held-out set.

Known limits: symptom files map onto the files tracked at HEAD, so a file
renamed or deleted inside the window may name nothing; and the trace signal
matches frame function names by their last segment only, so a generic name
(`run`, `get`, `handle`) can match unrelated changes. Changing that matching
changes the ranking and needs a new freeze. Frame function names are matched against
the names in diff hunk headers only, so a change that adds a whole file gets
file-level but not function-level trace evidence.

