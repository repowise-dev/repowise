# Change risk (`repowise risk`)

`repowise risk` reports on a **change** (a commit or a `base..head` range): the
bug-fix history of the files it touches, and the shape of its diff. It is a
just-in-time / pre-merge signal, complementary to `repowise health`, which
scores files rather than changes.

**Read `fix_history` first, not `score`.** The 0–10 score measures how big and
spread out a change is. It does not measure where the change lands, and the two
are not the same question — see [What the score does and does not
buy](#what-the-score-does-and-does-not-buy).

```bash
repowise risk                 # score uncommitted work, else HEAD
repowise risk HEAD            # score the last commit
repowise risk abc123          # score a single commit
repowise risk main..HEAD      # score a branch / PR range as one change
repowise risk main..HEAD --ext .py        # count only .py files
repowise risk main..HEAD -x 'tests/' -x '*.spec.ts'  # omit matching paths
repowise risk --format json               # machine-readable
```

It runs in-process: pure `git` + learned constants. **No LLM, no network, and no
blame at runtime**: SZZ labelling lives entirely in the offline calibration.

## What gets scored

With **no revspec** the subject is the change in front of you: your uncommitted
work (staged, unstaged and untracked) if the tree is dirty, otherwise `HEAD`.
The payload sets `working_tree: true` when it took that path, and the CLI says
so. Naming a revspec — `HEAD` included — always means committed refs.

A **merge commit** is scored for the diff it brought onto its first parent, so a
merged PR reads as its own content rather than as an empty change.

## Excluding paths

Use repeatable `--exclude` / `-x` flags with gitignore-style patterns to omit
files from a score. The same filters apply to the requested change and the
recent commits sampled for its percentile, so the comparison remains like for
like. Put project-wide, risk-only rules in a repository-root `.riskignore`;
those patterns apply automatically and are combined with any command-line
flags. For example, `tests/` excludes that directory recursively, while
`test_*.py` excludes matching test filenames anywhere in the repository.

## Fix history: where the change lands

The first block in the result is `fix_history`, and it is the one to act on. It
answers a question the diff shape cannot: **have these files broken before?**

```
These files have broken before · 82nd percentile of this repo's recent commits
┌──────────────────────────────────────────┬───────┬─────────────┐
│ File                                     │ Lines │ Prior fixes │
├──────────────────────────────────────────┼───────┼─────────────┤
│ core/pipeline/persist.py                 │    40 │        21.6 │
│ cli/commands/update_cmd/command.py       │     6 │        19.3 │
└──────────────────────────────────────────┴───────┴─────────────┘
```

- **Prior fixes** is a count of bug-fix commits that previously touched that
  file, **recency-weighted against the change's own date**: a fix from a year
  earlier counts a half, from two years a quarter. So the number is
  "recent-equivalent fixes", not a raw tally, and a file that broke constantly
  and then settled decays away. Anchoring to the change rather than to today
  means the same commit scores the same on every re-run.
- **`density`** is the churn-weighted mean of those per-file numbers. Weighting
  by churn means the file a change mostly edits dominates the answer rather than
  a one-line drive-by next door. It is a *ratio*, so unlike the score it does
  not grow with the size of the diff: one line in a file fixed twenty times
  outranks a thousand lines in files never fixed at all.
- **`percentile`** ranks that density against the same measure over the
  repository's own recent commits, since a bare "3.4 decayed fixes" means
  nothing on its own. Ranking against whole commits rather than against
  individual per-file numbers is what keeps it readable: a change spread over
  several files averages below any single hot file, so a per-file population
  pinned every multi-file change to the bottom. Commits that touch no
  fix-bearing file stay in the population; they are a legitimate answer to how
  much fix pressure a change here usually stands on. It is `null` when the
  change touches no fix history, or when fewer than eight sampled commits are
  available to rank against.

This comes from one `git log` walk (up to 20 000 commits, memoized per
repository state) using the same bug-fix classifier the indexer uses. It needs
no index, no database and no coverage data, so it is available on a repository
`repowise` has never indexed.

Fix history is read from **before** the change being scored: a commit is ranked
against the fixes that had already landed when it was written, never against
fixes it caused. For a range, the record is read at the fork point the diff
starts from, not at the base branch's current tip.

> **Caveat, stated rather than buried.** The bug-fix classifier is keyword-based
> and shared with the indexer, so `fix_history` under-reports in two ways.
>
> It matches `fix`, `bug`, `patch`, `resolves`, `closes #N`, `fixes #N` — and
> misses conventions outside that set. Django's `Fixed #12345` is the notable
> one: on a 4 000-commit sample it classifies 5 commits as fixes where roughly
> 1 800 use that prefix. (Django also uses `Fixed #N` for features, so the
> subject line alone cannot separate the two — which is why the classifier has
> not simply been widened.)
>
> It also **excludes** any subject containing `docs`, `typo`, `bump`, `deps`,
> `chore`, `lint`, `format` or `style`. That keeps cosmetic commits out, but
> drops genuine fixes like "fix: docs build crash" with them.
>
> Where the classifier fires the ranking is good; where a project's convention
> falls outside it, `fix_history` reads lower than the truth.

## What the diff-shape score measures

The model uses Kamei-style *change* metrics (Kamei et al., "A large-scale
empirical study of just-in-time quality assurance"):

| Feature | Meaning |
|---------|---------|
| `la`, `ld` | lines added / deleted |
| `nf` | files touched |
| `nd`, `ns` | distinct directories / top-level subsystems touched |
| `entropy` | Shannon entropy of the per-file churn distribution (diffusion) |
| `exp` | author's prior commit count (experience) |

`exp` is genuinely optional: when the author cannot be resolved — a diff-only
caller with no local history, or a name whose regex breaks the `git rev-list`
lookup — it is reported as `null` and contributes exactly zero to the logit.
Nothing is imputed, because `0` is a real value meaning "first ever commit" and
the model reads it as a risk-raising signal.

These are properties of the *diff*, so the score is a change-level signal rather
than a file-size proxy. The risk is a plain L2-logistic over standardized,
log-compressed features (`logit = intercept + Σ coefᵢ·zᵢ`), so every feature's
push on the risk is exact and reported as an attributable driver (the same
linear / per-finding-attributable contract the file health score holds).

## What the score does and does not buy

The score is a **diff-size statistic**. That is a measured claim, not a hedge:

- `la` (lines added) carries a coefficient 7.6× the next largest, and scoring by
  `la` alone reproduces the full seven-feature score to within 0.12–0.16 points
  on every repository tried.
- On a hand-picked set of small-but-dangerous changes versus large-but-boring
  ones (47 within-repo pairs across repowise, flask, django and zod), the score
  ranks the dangerous change above the boring one in **0 of 47** pairs. Ranking
  by fix density alone gets **46 of 47**.

  That set is constructed, not held out: the pairs were chosen so that the
  dangerous change is always the smaller one, which means ranking by lines added
  scores 0 by construction and any signal genuinely independent of size scores
  near-perfectly. It is a falsification test — "can the score ever do this?" —
  and not an accuracy estimate. Its value is that the score failed it
  completely, on cases a reviewer would call obvious.

A refit was measured and rejected rather than shipped. Regrouping the corpus to
PR granularity (`--first-parent` merge spans) and adding two size-orthogonal
features made accuracy *worse*: pooled leave-one-repo-out AUC 0.769 for the
refit against 0.776 for the current feature set and 0.780 for a churn-only
baseline. Per repository, **lines added alone matches or beats the fitted model
in five of six repos**. The reason is the labels: a commit is marked
defect-inducing when a later bug-fix's blame points back at a line it wrote, and
a larger commit writes more lines, so the label is itself size-biased. Any
deliberately size-orthogonal feature scores near chance against it — fix density
lands at 0.46–0.57 AUC — which is a fact about the labels, not about the
feature. So the model constants are unchanged and the score is reported as what
it demonstrably is.

`score_measures` states this in the payload. Read the result in this order:

- **`fix_history`**: where the change lands. The signal to triage on.
- **Review priority** / **classification** / **percentile**: where this change's
  *diff shape* sits in the repo's own distribution. Useful for "is this a big
  one for us", not for "is this a dangerous one".
- **`score`** (0–10): diff size and spread, corpus-anchored to a single commit.
- **`fallback_band`**: the absolute `low` / `moderate` / `high` band. Present
  *only* when there was no baseline to rank against (a shallow repo, or
  `--baseline 0`), which is why it is not a peer of the review priority.

The score's absolute band is also **unit-blind**. Its corpus is individual
commits (baseline: 10.5 lines added, 1.7 files), so a squash-merged PR or a
`base..head` range is several commits' worth of diff read against a one-commit
scale and skews high: two-thirds of commits can read "high" while ranking
normally for *that* repo. The payload states the assumption in `score_unit`.

Each **driver** is reported relative to *the model's baseline commit* (the
calibration-corpus mean), not this repo, so a `+19 / −1` change can legitimately
read "more lines added than baseline" while still ranking `Below typical` for a
repo of large commits. The signed contribution and colour (red raised the raw
score, green lowered it) carry the direction; the label only states the
feature's standing, never an absolute verdict.

`nf`, `nd` and `ns` enter the logit exactly as fit but are **not reported as
drivers**. Their coefficients are small and negative — collinearity with `la`
(size), not a finding that touching more files is safer — so as an explanation
they contradict themselves: the label reads "more directories than baseline"
while the contribution is protective. Hiding an explanation we cannot stand
behind is the honest interim; a refit is the real fix.

The `repowise risk` CLI samples the repo's recent commits live (`--baseline`,
default 200) to compute this percentile; in the web UI it is precomputed from the
indexed commit history.

### What the sample is anchored to

The sample is the recent history the change is measured against, and where it
starts depends on what is being scored:

| Subject | Sample runs back from | Why |
|---|---|---|
| A commit already in `HEAD`'s history | `HEAD` | Ranked against how the repo commits *now*. |
| A commit that is not (another branch) | that commit | `HEAD`'s history is not its cohort. |
| A `base..head` range | the merge-base of the two sides | The range's own commits stay out of the distribution it is measured against. |
| Uncommitted work | `HEAD` | Same subject, same cohort as scoring `HEAD`. |

A change never ranks against itself: the target's own score is removed from the
sample before the percentile is taken.

Because the sample depends only on that anchor and the active filters, and not
on the individual change, one walk is reused for every change scored against the
same history in the same process. A long-running MCP server pays for the walk
once and answers subsequent `get_change_risk` calls without repeating it. A new
commit, a moved branch, or a different set of filters produces a different
anchor or a different filter set, and so a fresh walk.

## Calibration & accuracy

Constants are learned offline against the defect corpus (AG-SZZ bug-inducing
commits as labels, time-ordered evaluation with a right-censoring gap, and a
leave-one-repo-out comparison to the churn-only baseline). On a 7-repo,
5-language slice the pooled leave-one-repo-out AUC is **0.772 vs 0.766 for
churn-only** (Δ +0.0068, 95% CI [-0.0003, +0.0131]).

Read that number for what it is. A churn-only baseline scores 0.766 on the same
labels, and lines-added alone scores higher still, so the margin measures very
little. It is reported because it is the number the constants were selected on,
not as evidence the score ranks danger — for that claim, see
[What the score does and does not buy](#what-the-score-does-and-does-not-buy),
where it fails.

**`fix_history` carries no AUC of its own, deliberately.** Its evidence is the
47-pair ranking gate (46/47) and the fact that the files it ranks highest in
this repository are the ones with the longest bug-fix records. It scores near
chance against the SZZ labels, which — as above — is a property of those labels.
Quoting a number from a benchmark that structurally cannot see the signal would
be worse than quoting none.

Only learned constants ship; the runtime stays deterministic, zero-LLM, and
free of new dependencies. Recalibrate via
`repowise-bench/health-defect/jit_calibration.py`; the constants live in
`packages/core/src/repowise/core/analysis/change_risk/model.py`.

## Cross-repo change risk (workspace mode)

> **Note:** This section describes `get_risk` in PR mode (`changed_files`). `get_change_risk` is
> pure diff-shape scoring and does not access the workspace graph — it produces no cross-repo fields.

In a workspace, a change rarely stops at the repo boundary. When `get_risk` is
called in PR mode (`changed_files`), its `directive` block gains two cross-repo
fields derived from the [system graph](../scale/WORKSPACES.md#system-graph):

- `will_break_consumers`: services in *other* repos that structurally depend on
  the changed repo (a contract or package import). These are the consumers most
  likely to break.
- `missing_cross_repo_cochanges`: services in other repos that historically
  co-change with the changed repo but aren't in the diff. Correlation, not a
  call, so they read as "may drift," not "will break."

The same reachability powers the `get_blast_radius` MCP tool, the
`GET /api/workspace/blast-radius` endpoint, and the Live System Map's
blast-radius ripple. Structural edges outweigh behavioral co-change in the
ranking (one named constant, `BEHAVIORAL_EDGE_WEIGHT`, in
`packages/core/src/repowise/core/workspace/blast_radius.py`). See
[Cross-Repo Blast Radius](../scale/WORKSPACES.md#cross-repo-blast-radius) for the full
model.

The directive carries a third cross-repo field, `breaking_changes`, when a
provider contract in the changed repo changed *incompatibly* (a removed route or
field, a type or field-number change, a newly-required field). Where
`will_break_consumers` is topology ("who depends on this repo"),
`breaking_changes` is schema-level truth ("this specific contract changed in a
way that breaks these consumers"), each entry listing the changed contract and
the consumer files it endangers across repos. It is computed by diffing the
current contracts against the previously-indexed set during
`repowise update --workspace`; non-breaking changes (an added optional field, a
new endpoint) never appear. See
[Breaking-Change Guard](../scale/WORKSPACES.md#breaking-change-guard) for the full model.
