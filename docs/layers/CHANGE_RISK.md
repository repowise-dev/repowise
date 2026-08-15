# Change risk (`repowise risk`)

`repowise risk` scores a **change** (a commit or a `base..head` range) for
defect risk from the shape of its diff, not the health of any file. It is a
just-in-time / pre-merge signal: complementary to `repowise health` (which
scores files), and useful as a PR gate because it fires on risky *small* changes
a file-level delta misses.

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

## What it measures

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

## How to read the result

The headline signal is **repo-relative**. The raw 0–10 score is anchored to the
offline calibration corpus, and that corpus is **individual commits** (baseline:
10.5 lines added, 1.7 files). A squash-merged PR, a `base..head` range, or any
repo whose typical commit is large is several commits' worth of diff read
against a one-commit scale, so the absolute band skews high: two-thirds of
commits can read "high" while ranking perfectly normally for *that* repo. The
*ranking* is sound; the absolute band is not portable. The payload states the
assumption in `score_unit`.

So the surfaces lead with where the change sits in its **own repo's**
distribution:

- **Review priority** / **classification**: `Below typical` / `Typical` /
  `Elevated` (terciles of the repo's own commit-risk distribution). This is the
  signal to triage on.
- **Percentile**: "riskier than N% of this repo's commits".
- **Raw model score** (0–10): kept for transparency but shown as a secondary,
  clearly corpus-anchored number, not the thing to act on.
- **`fallback_band`**: the absolute `low` / `moderate` / `high` band. Present
  *only* when there was no baseline to rank against (a shallow repo, or
  `--baseline 0`), which is why it is not a peer of the review priority.

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
churn-only** (Δ +0.0068, 95% CI [-0.0003, +0.0131]): competitive with churn
across the corpus and stronger on some repos (clap +0.053 on a time-ordered
split). Diff size dominates the fit, with change entropy risky and author
experience protective, both literature-consistent. Only the learned constants
ship; the runtime stays deterministic and zero-LLM.

Recalibrate via `repowise-bench/health-defect/jit_calibration.py`; the constants
live in `packages/core/src/repowise/core/analysis/change_risk/model.py`.

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
