# Bug-fix history

Repowise records which files and which functions have actually been fixed, how
recently, and how often. The output is a small set of honest numbers: a
per-file fix count over a trailing 180-day window, a per-symbol breakdown, a
decayed "bug magnet" flag, and the age of the most recent fix. They surface in
the pre-edit hook, in `get_risk` and `get_change_risk` over MCP, in the health
drawer, and on the symbols views.

**No LLM calls.** This is git history, a `-U0` diff pass, and arithmetic. It
runs inside the existing git phase of `repowise init` and `repowise update`.

The reason to care: a file's churn tells you it changes a lot, which is often
just where the work is. A file's fix history tells you it *breaks* a lot, which
is a different and more actionable thing. On this repo the top bug magnets
(`pipeline/persist.py`, `pipeline/incremental.py`, `dead_code/analyzer.py`,
`update_cmd/command.py`) match the churn hotspot list, arrived at independently
from bug fixes rather than commit counts.

## Fix-shape filtering: what counts as a bug fix

A commit whose subject matches the fix keywords is a candidate, not a fact.
Plenty of `fix:` commits fix a typo in the README, tighten a test, or bump a
version. Counting those inflates every downstream number.

So each candidate's `-U0` diff is classified into one shape, and only one shape
counts:

| Shape | Counted | Example |
|---|---|---|
| `code_fix` | yes | production source lines changed |
| `test_only` | no | only files under `tests/` or matching a test naming rule |
| `doc_only` | no | only `.md` / `.mdx` / `.rst` / `.txt` / `.adoc` or `docs/` |
| `config_other` | no | only lockfiles, CI YAML, `*.config.*`, dotfile rc |
| `comment_only` | no | code files touched, but the changed lines are comments or docstrings |
| `empty` | no | merge commits and no-op diffs |

The comment-only rule is line-level, not path-level: it strips trailing comments
and compares the code part of each removed and added line, so a commit that
rewords a docstring in a `.py` file does not count as a bug fix.

The classifier was validated against 240 hand-labelled fix commits across four
repos (repowise, flask, django, zod) and agrees with the labels **98.3%** of the
time (repowise, flask and django all 60/60; zod 56/60). The four residual
disagreements are all the same shape, "a file with a code extension changed but
no product code did", and were left as honest disagreements rather than closed
with repo-specific ignore lists.

The effect is real and varies enormously by repo. On flask, **58.7%** of
keyword-matched fix commits are noise and filtering removes half the raw
attributions. On repowise it is 7.0%. That is the point: a number that means the
same thing in both repos is worth more than a bigger number in one.

`GitMetadata` stores both `prior_defect_count` (filtered) and
`prior_defect_raw_count` (unfiltered), so the delta stays inspectable.

One thing filtering deliberately did not do: change the health score.
Re-running the defect-weight calibration on the 21-repo corpus showed filtering
moves pooled OOF AUC by +0.0002, inside noise. The `prior_defect` marker stays
at weight 1.0. Phase 1 shipped for count honesty, not for a score change, and
the doc says so rather than implying a win that was not measured.

## The `fix_events` table

Each counted fix produces one row per file it touched:

```
fix_events(repository_id, fix_sha, file_path, shape_kind,
           old_ranges_json, fixed_at, ...)
```

`old_ranges_json` holds the line ranges the fix replaced, numbered on the fix's
own parent commit. Persisting the events, rather than only a count, is what
makes per-symbol attribution and changed-line overlap possible later without
re-walking git.

The table is populated during a full index and extended incrementally by
`repowise update`. A pre-existing database picks the new columns up through the
additive reconciler with no migration code; rollups sit at their defaults until
the next update.

## Per-symbol attribution

After both `fix_events` and `wiki_symbols` are persisted, each event's replaced
line ranges are matched against the symbol spans in that file, producing a
`fix_symbol_counts` map per file: which function or class absorbed each fix.

Measured attribution rate, walking the shipped tracer live and joining against
each repo's existing index:

| Repo | Events | In-envelope rate |
|---|---|---|
| django | 2,179 | 97.4% |
| flask | 125 | 96.3% |
| zod | 337 | 81.8% |
| repowise | 1,145 | 96.8% |

"In-envelope" means the event's lines land somewhere between the file's first
and last indexed symbol. Gaps *between* symbols still count as misses, so this
remains a test of the join rather than a restatement of it. The two causes of
the loose-denominator misses were measured, not argued: django and flask are
line drift (symbol spans are at HEAD, ranges are at each fix's parent, so a 2011
fix is matched against lines that moved a decade ago, and 99.8% of django's
events are 3+ years old), and zod is symbol density in test files the parser
indexes with one or two named symbols.

**The counts are approximate and every surface says so.** Symbol spans are
current-tree, fix ranges are historical. Read them as "mostly here", not as an
exact ledger.

## The bug magnet rollup

Each file's counted fixes are collapsed into one decayed mass:

```
mass = Σ 0.5 ^ (age_days / 90)
bug_magnet = mass >= 3.0
```

The 90-day half-life was picked from a sweep of 60 / 90 / 180 days against the
21-repo calibration corpus. Decay was the only lever that moved the
`prior_defect` coefficient at all (0.15 undecayed to 0.23 decayed); 90d and 180d
tied inside noise and 90d is the tighter of the two.

Read the threshold with the decay in mind. Only a same-day fix is worth a full
1.0, so three real fixes spread over a couple of weeks land near 2.9 and do
**not** flag. In practice the flag needs four recent fixes, or three very recent
ones. That is deliberately the conservative end: the flag exists to interrupt
someone mid-edit, so it should be rare enough to be worth reading.

The rollup recomputes the whole repo on every index and update rather than
patching the files an update touched, because decay ages a rollup with nothing
in the file changing. It costs about 0.25s on this repo (1,172 events, 739
files): two indexed queries and arithmetic.

## The recency contract

This is the rule that keeps the feature honest, and it is enforced in the type
definitions rather than left to each surface:

> `bug_magnet` is the decayed fix mass past its trigger, so it is a recency
> claim. Any copy that shows it must show `last_fix_at` too.

Two consequences:

1. **A magnet claim never appears without its age.** If `last_fix_at` is null,
   the flag is dropped rather than shown unanchored. Pre-merge review caught
   three surfaces violating this (the health panel badge, the VS Code hover, and
   the MCP `defect_profile`); all three now drop the flag, and there is a test
   on the null-timestamp path.
2. **The surface goes silent outside the window.** The pre-edit hook does not
   fire at all when the last fix is older than 180 days, no matter how large the
   historical count is. "This file was fixed nine times, in 2019" is not a
   warning, it is trivia.

A related bug worth knowing about, since it is the kind that hides: `last_fix_at`
was originally serialized without a timezone. SQLite's DATETIME bind processor
drops `tzinfo`, so `.isoformat()` produced a string JavaScript parses as local
time. West of UTC, a fix committed two hours ago landed in the future, tripped
the relative-time future-guard, and the age silently vanished from all four
surfaces required to carry it, worst for the freshest fixes. Fixed once
upstream, not four times in the consumers.

## Where it surfaces

### Pre-edit hook

When an agent is about to edit a file with a real recent run of fixes, one line
goes into its context:

```
[repowise] pipeline/persist.py has been bug-fixed 5x in the last 6 months,
last 2 weeks ago (bug magnet); mostly in run_pipeline.
```

Three gates, all of which must hold: at least 3 counted fixes, a last fix inside
180 days, and one claim per file per session (an atomic ledger, so two racing
hook processes cannot double-fire). A file that also has a governing
architectural decision emits two lines, never more. Wired into both the Claude
Code and Codex hook paths. See [docs/agent/HOOKS.md](../agent/HOOKS.md).

### `get_risk`

Files with counted fixes gain a `defect_profile` block:

```python
get_risk(targets=["packages/core/src/repowise/core/pipeline/persist.py"])
# defect_profile: {fix_count, window, last_fix_days_ago,
#                  bug_magnet, top_symbols}
```

`bug_magnet` appears only when true, `top_symbols` is the top three with the
path prefix stripped, and the whole block is omitted on repos with no fix data.
Measured at 46 tokens mean, 80 worst case, against a 150-token ceiling across
803 files. The approximation caveat lives in the tool docstring, stated once,
rather than as a constant string repeated on every row.

Shipping `defect_profile` also let the old keyword `risk_type="bug-prone"`
derivation retire. It used to scan commit subjects for `/fix|bug|patch/`, which
counted doc and test commits, had no recency, and disagreed with the count every
other surface showed. It now reads `bug_magnet` or `prior_defect_count >= 3`.
That does silently reclassify some files: a high-traffic file with 3 fixes in
200 commits now reads `bug-prone` where it read `churn-heavy`.

### `get_change_risk`

A diff or commit range gains a `prior_fixes` block: per changed file, how many
past fixes it carries and how many of the change's lines fall inside a past
fix's replaced ranges. The per-file counts are exact (counted by
`COUNT(DISTINCT fix_sha)`, so one commit fixing three files reports one fix, not
three). The line overlap is labelled `approximate` in the payload, because past
ranges are numbered on their own parent commit. Files are ranked by overlap then
count, and the list carries a `truncated` flag rather than silently dropping the
tail.

See [CHANGE_RISK.md](CHANGE_RISK.md) for the rest of that tool.

### Dashboard

- **Health drawer**: a collapsed **Bug history** section with per-symbol counts,
  the last-fix age on the toggle, and an approximation footnote.
- **File signals panel**: the bug-fix tile gains a **Bug magnet** badge and
  folds the last-fix age into its caption.
- **Symbol detail** (modal and routed page): a **Bug fixes** stat tile beside
  **Modifications**. How often it changes next to how often it breaks is the
  contrast that earns the cell.
- **Symbols list**: a **Bug-fixed** facet and a per-row fix chip. The filter is
  symbol-level, unlike the file-level filters beside it, because a bug-fixed
  file says very little about the one function you are reading.
- **VS Code**: one line on the file hover, off the same signals block.

See [docs/start/DASHBOARD.md](../start/DASHBOARD.md) for where each of these
lives.

## What was built, measured, and deliberately not shipped

The most useful thing this layer can tell you is what it refuses to tell you.

### SZZ inducing-commit attribution: built, measured, deleted

The obvious next step from "this line was fixed" is "and here is the commit that
introduced the bug". That is the SZZ algorithm, and it was fully built: a blame
pass walking each fix's replaced lines back through history, refactor-aware, with
overlap-based ranking of the candidates.

Then it was measured against a hand-labelled set. Fifty-three judged rows, five
reviewers. **File-level SZZ tops out at 74.5% top-candidate precision on this
corpus. The gate was 80%.**

74.5% is not a bad algorithm. It is a *good* SZZ implementation. It is also not
good enough to put a commit SHA and a person's name in a UI, because one time in
four the UI would be accusing the wrong commit, and there is no way for the
reader to tell which time. So the blame pass was deleted, `szz.py` is gone, and
**no repowise surface names an inducing commit.** The planned
"(last: `<sha>` `<subject>`)" tail on `get_change_risk` was cut. The planned
inducing-commit link on commit rows was cut.

Deleting it was verified rather than assumed: `inducing_shas_json` appeared in
exactly four places in the tree (the model, the migration, the writer, and the
writer's tests), and neither the hosted backend nor the PR bot referenced
`fix_events` in any spelling. There was no consumer to break. Removing the pass
also gave back 9.1s of index time on this repo and 5.9s on zod.

The findings are kept here so they do not vanish with the code, because they are
reusable by anyone attempting this again:

- **Refactor-aware blame is worth building.** Walking through
  behaviour-preserving moves to the parent lifted strict precision from 70.6% to
  74.5%. All 14 of the initial false calls were refactors inheriting moved lines.
- **Overlap ranking beats earliest-commit ranking by 12 points** on judged rows.
  Earliest-commit's apparent 80% was an artefact of its wrong answers going
  unjudged: 7 of its 16 unknowns were this repo's initial commit, and all 7 came
  back "not plausible" once judged. This is the finding most likely to be
  re-litigated, so, plainly: it was tested twice, and it settled the other way
  from the first read.
- **The two residual failure modes are properties of SZZ, not bugs.** An initial
  import has no earlier commit to name, and package splits that move lines
  wholesale while re-sorting imports defeat the carried-through test on exactly
  the lines that moved.

Restoring the tracer is a `git revert` of one commit, not a re-derivation. If
someone finds a ranking that clears 80% on the frozen label set, the data layer
is ready for it.

### "AI vs human introduced bugs": cut

A repo-level card comparing defects introduced by AI-authored commits against
human-authored ones, per KLOC. It is an obvious feature to want given that
repowise already detects agent provenance on commits, and it was planned.

It was gated on a concentration check before build, and the check failed. **The
top 5 inducing commits held 30.3% of all traced mass against a 20% ceiling, with
the initial import alone at 13.9%.** SZZ error does not average out: when blame
concentrates on a handful of enormous commits, any aggregate built on inducing
commits inherits that concentration, and the aggregate is then mostly a
statement about who happened to author the repo's five biggest commits. A stat
like "AI introduces N% more bugs" is exactly the kind of number that gets
screenshotted and quoted, which raises the bar rather than lowering it.

So it was killed before it was built, and the provenance join it depended on was
cut with it.

### What survived the cuts

Counts and recency survived. Accusations did not. Everything shipped here
answers "this code has been broken before, recently, and roughly here", and
nothing answers "and it was your fault". The first question is answerable from
git with measurable accuracy. The second is not, yet.

## Limitations

- **Symbol counts are approximate.** Current-tree spans against historical line
  ranges. Stated on every surface that shows them.
- **Keyword-matched fix detection.** A bug fixed in a commit whose subject does
  not say so is invisible. The shape filter improves precision, not recall.
- **The 180-day window is fixed.** A file fixed heavily two years ago and quiet
  since reads as clean, which is intentional but is a choice, not a truth.
- **Workspace lookup in the hook does not filter by `repository_id`,** matching
  the co-change query beside it. In a workspace whose members lack their own
  `.repowise` this degrades to silence rather than a wrong answer.
- **The hook's usefulness is unproven.** It shipped without the planned week of
  live-session dogfooding, so whether the notice changes behaviour or becomes
  ignorable noise is an open question. If it is noise, the plan is to cut or
  reword the hook surface and keep every data layer beneath it.

## See also

- [CODE_HEALTH.md](CODE_HEALTH.md): the `prior_defect` marker and the
  "does the score find the bugs?" self-check that reads the same counts.
- [CHANGE_RISK.md](CHANGE_RISK.md): `get_change_risk` and its scoring.
- [docs/agent/MCP_TOOLS.md](../agent/MCP_TOOLS.md): `get_risk` and
  `get_change_risk` schemas.
- [docs/agent/HOOKS.md](../agent/HOOKS.md): the pre-edit hook.
