# Change risk (`repowise risk`)

`repowise risk` scores a **change** — a commit or a `base..head` range — for
defect risk from the shape of its diff, not the health of any file. It is a
just-in-time / pre-merge signal: complementary to `repowise health` (which
scores files), and useful as a PR gate because it fires on risky *small* changes
a file-level delta misses.

```bash
repowise risk                 # score HEAD
repowise risk abc123          # score a single commit
repowise risk main..HEAD      # score a branch / PR range as one change
repowise risk main..HEAD --ext .py        # count only .py files
repowise risk --format json               # machine-readable
```

It runs in-process: pure `git` + learned constants. **No LLM, no network, and no
blame at runtime** — SZZ labelling lives entirely in the offline calibration.

## What it measures

The model uses Kamei-style *change* metrics (Kamei et al., "A large-scale
empirical study of just-in-time quality assurance"):

| Feature | Meaning |
|---------|---------|
| `la`, `ld` | lines added / deleted |
| `nf` | files touched |
| `nd`, `ns` | distinct directories / top-level subsystems touched |
| `entropy` | Shannon entropy of the per-file churn distribution (diffusion) |
| `exp` | author's prior commit count (experience); unknown → scored neutrally |

These are properties of the *diff*, so the score is a change-level signal rather
than a file-size proxy. The risk is a plain L2-logistic over standardized,
log-compressed features — `logit = intercept + Σ coefᵢ·zᵢ` — so every feature's
push on the risk is exact and reported as an attributable driver (the same
linear / per-finding-attributable contract the file health score holds).

## How to read the result

The headline signal is **repo-relative**. The raw 0–10 logistic probability is
anchored to the offline calibration corpus, so on a repo whose typical commit is
large the diff-size term dominates and the absolute band skews high — two-thirds
of commits can read "high" while ranking perfectly normally for *that* repo. The
*ranking* is sound; the absolute band is not portable.

So the surfaces lead with where the change sits in its **own repo's**
distribution:

- **Review priority** — `Below typical` / `Typical` / `Elevated` (terciles of
  the repo's own commit-risk distribution). This is the signal to triage on.
- **Percentile** — "riskier than N% of this repo's commits".
- **Raw model score** (0–10) — kept for transparency but shown as a secondary,
  clearly corpus-anchored number, not the thing to act on.

Each **driver** is reported relative to *the model's baseline commit* (the
calibration-corpus mean), not this repo — so a `+19 / −1` change can legitimately
read "more lines added than baseline" while still ranking `Below typical` for a
repo of large commits. The signed contribution and colour (red raised the raw
score, green lowered it) carry the direction; the label only states the
feature's standing, never an absolute verdict.

The `repowise risk` CLI samples the repo's recent commits live (`--baseline`,
default 200) to compute this percentile; in the web UI it is precomputed from the
indexed commit history.

## Calibration & accuracy

Constants are learned offline against the defect corpus (AG-SZZ bug-inducing
commits as labels, time-ordered evaluation with a right-censoring gap, and a
leave-one-repo-out comparison to the churn-only baseline). On a 7-repo,
5-language slice the pooled leave-one-repo-out AUC is **0.772 vs 0.766 for
churn-only** (Δ +0.0068, 95% CI [-0.0003, +0.0131]) — competitive with churn
across the corpus and stronger on some repos (clap +0.053 on a time-ordered
split). Diff size dominates the fit, with change entropy risky and author
experience protective, both literature-consistent. Only the learned constants
ship; the runtime stays deterministic and zero-LLM.

Recalibrate via `repowise-bench/health-defect/jit_calibration.py`; the constants
live in `packages/core/src/repowise/core/analysis/change_risk/model.py`.
