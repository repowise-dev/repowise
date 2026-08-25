# Change risk (`repowise risk`)

`repowise risk` reports on a **change** (a commit or a `base..head` range): the
bug-fix history of the files it touches, and the shape of its diff. It is a
just-in-time / pre-merge signal, complementary to `repowise health`, which
scores files rather than changes.

**Lead with `risk_percentile` and `classification`.** They are the benchmarked,
population-relative authority for live change review. `fix_history` is
complementary evidence about where the change lands. The supporting 0–10 score
measures how big and spread out a change is; it is not a probability. See
[What the score does and does not buy](#what-the-score-does-and-does-not-buy).

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

`risk_authority` and `score_measures` state this in every payload;
`include=["scales"]` adds the per-field dictionary.
Read the result in this order:

- **Review priority** / **classification** / **percentile**: where this change's
  *diff shape* sits in the repo's own distribution. This is the authoritative
  population-relative review signal.
- **`fix_history`**: uncalibrated historical evidence about where the change
  lands, reported separately rather than folded into a probability.
- **`score`** (0–10 normalized points): supporting diff size and spread,
  offline-calibrated and corpus-anchored to a single commit.
- **`fallback_band`**: the heuristic-thresholded absolute `low` / `moderate` /
  `high` model-score band. Present
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

## PR structural-impact scale (`get_risk`)

PR-mode `get_risk` answers a different question. Its
`structural_impact_score` is a deterministic, uncalibrated structural-exposure
heuristic in normalized points from 0 to 10. It combines the mean and maximum
of `pagerank * (1 + temporal_hotspot)` across changed files, maps that component
to at most 8 points with `8 * (1 - exp(-10 * combined))`, then adds at most 2
points for `min(transitive_dependents / 20, 1)`. Bands are `localized` below 4,
`moderate` from 4 to below 7, and `broad` from 7. It is not a probability and is
not authoritative for live change review. Historical co-change evidence is
reported separately and never enters this structural score.

`overall_risk_score` remains an exact deprecated alias so older clients keep
the same value and unit. `overall_risk_score_compatibility` names the migration
to `structural_impact_score`; the two fields cannot contradict.

The deterministic fixture corpus records the retained scale's distribution:

| Control | Score | Band |
| --- | ---: | --- |
| documentation / low signal | 0.01 | localized |
| small ordinary source | 0.34 | localized |
| historical fixes, limited reach | 0.34 | localized |
| co-change only | 0.08 | localized |
| moderate multi-file | 4.27 | moderate |
| structurally broad, little history | 7.34 | broad |
| genuinely broad high control | 9.91 | broad |

The numeric formula was retained, so the before-and-after numeric distributions
are identical; the correction changes names, units, authority, and labels. No
fixture saturates at 10, ordinary controls remain below the moderate threshold,
and all three bands are occupied. The corpus lives in
`tests/fixtures/risk_scale_corpus.json`.

## Public scale inventory

| Public value | Producer and evidence | Unit / range | Calibration and authority |
| --- | --- | --- | --- |
| `get_change_risk.score` / REST `score` / stored `change_risk_score` | Offline-fitted logistic model over live diff size, spread, entropy, and author experience; deterministic at runtime | normalized points, 0-10; calibrated at single-commit granularity | Benchmarked on 4,102 commits across 7 repositories; supporting signal, not a probability or review authority |
| `risk_percentile` | Mid-rank of the same score among filtered recent commits | percentile rank, 0-100 | Population-relative benchmark; authoritative with `classification` for live change review |
| `review_priority` / `classification` | Shared percentile terciles at 33.33 and 66.67 | category | Authoritative population-relative label |
| `fallback_band` | Shared score thresholds at 4 and 7, emitted only without a usable baseline | category | Heuristic thresholds on the benchmarked model score; absolute fallback, not population-relative |
| `fix_history.density` | Churn-weighted, recency-decayed prior bug fixes on touched files | recency-weighted prior fixes, unbounded | Uncalibrated historical heuristic; separate evidence, never folded into a probability |
| `get_risk` `hotspot_score` | Repository-relative churn percentile from the index | ratio, 0-1 | Uncalibrated normalized component |
| `get_risk` `health_score` | Indexed code-health model | health points, 0-10; higher is healthier | Benchmarked file-health signal, not interchangeable with change-risk points |
| `structural_impact_score` | PR structural formula above | normalized points, 0-10 | Deterministic and uncalibrated; not authoritative. `overall_risk_score` is an exact deprecated alias |
| `direct_risks[].structural_score` | `pagerank * (1 + temporal_hotspot)` | raw pagerank-weighted-hotspot value, unbounded | Uncalibrated within-change structural weight. `risk_score` is an exact deprecated alias |
| `cochange_warnings[].score` | Number of historical commits in which the pair co-changed | raw commit count, 0+ | Historical evidence only; cannot become structural or runtime-breakage evidence |
| workspace `impacted[].score` | Strongest path product of edge confidence, edge-kind weight, and `0.6` per hop | relative path weight, 0-1 | Deterministic, uncalibrated ranking heuristic; not a probability or change-review authority |
| dashboard hotspot triage index | `40% * churn percentile + 35% * bus-factor tier + 25% * bounded temporal activity` | heuristic points, 0-100 | Client-side, uncalibrated orientation only; labelled adjacent to the chart |

Machine-readable `risk_authority`, `structural_impact_scale`,
`overall_risk_score_compatibility`, and `impact_score_semantics` carry these
definitions on every payload. They ship the guard tier - unit, range,
calibration status, authority - by default. The reference tier (fitting corpus,
formula, component breakdown, and the full `risk_scales` dictionary) is
identical on every call, so MCP returns it only for `include=["scales"]`; the
CLI `--json` output and this table carry it unconditionally.

## Cross-repo change risk (workspace mode)

> **Note:** This section describes `get_risk` in PR mode (`changed_files`). `get_change_risk` is
> pure diff-shape scoring and does not access the workspace graph — it produces no cross-repo fields.

In a workspace, a change rarely stops at the repo boundary. When `get_risk` is
called in PR mode (`changed_files`), its `directive` block gains two cross-repo
fields derived from the [system graph](../scale/WORKSPACES.md#system-graph):

- `will_break_consumers`: deprecated compatibility name for services in *other*
  repos that structurally depend on the changed repo (a contract or package
  import). This is structural reach for review, not a runtime-breakage claim.
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
