# Change risk internals

Contributor reference for the change-risk model, its calibration, and every
public risk scale. User-facing behaviour is in
[docs/layers/CHANGE_RISK.md](../layers/CHANGE_RISK.md).

Code lives in `packages/core/src/repowise/core/analysis/change_risk/`
(`features.py`, `model.py`, `baseline.py`, `fix_history.py`, `service.py`),
`analysis/risk_semantics.py` (the scale contracts) and `analysis/pr_blast.py`
(PR structural impact). The runtime is deterministic, makes no LLM or network
calls, and runs no blame: SZZ labelling exists only in the offline
calibration.

## The model

A plain L2-logistic over standardized, `log1p`-compressed Kamei change metrics
(`la`, `ld`, `nf`, `nd`, `ns`, `entropy`, `exp`):
`logit = intercept + sum(coef_i * z_i)`. Every feature's contribution is exact
and reported as a driver. `exp` is optional; when the author cannot be
resolved it is `null` and contributes zero, since `0` is a real value (first
commit) the model reads as risk-raising. `nf`, `nd` and `ns` have small
negative coefficients from collinearity with `la`; they stay in the logit as
fit and are hidden as drivers because the explanation would contradict itself.

Constants are hard-coded in `_CONSTANTS` in `model.py`. Only constants ship.

## Calibration

- **Corpus**: 4,102 commits (662 inducing) from a 7-repo, 5-language slice:
  clap, pydantic, fd, gin, fastify, bat, chi.
- **Labels**: AG-SZZ bug-inducing commits, with a 120-day right-censoring gap.
  `age_days` is excluded as a label-availability artifact.
- **Evaluation**: leave-one-repo-out pooled out-of-fold AUC against a
  churn-only baseline.
- **Result**: 0.772 for the model vs 0.766 for churn-only (delta +0.0068, 95%
  CI [-0.0003, +0.0131]).

That margin is small, and it is the number the constants were selected on. It
is not evidence that the score ranks danger.

### Why the score is reported as a size statistic

- `la` carries a coefficient 7.6x the next largest; scoring by `la` alone
  reproduces the full score within 0.12-0.16 points on every repo tried.
- On 47 constructed within-repo pairs (repowise, flask, django, zod) of a small
  dangerous change and a large boring one, the score ranks the dangerous change
  higher in 0 of 47; fix density does in 46 of 47. The pairs are built so size
  alone scores 0, so this is a falsification test, not an accuracy estimate.
- A PR-granularity refit (`--first-parent` merge spans) with two
  size-orthogonal features scored worse: pooled LOO AUC 0.769, against 0.776
  for the current feature set and 0.780 for churn-only. Lines added alone
  matched or beat the fitted model in five of six repos.

The cause is the labels. A commit is marked inducing when a later fix's blame
lands on a line it wrote, and a bigger commit writes more lines, so the label
is size-biased. Any size-orthogonal feature scores near chance against it (fix
density lands at 0.46-0.57 AUC). The constants were kept and the score is
labelled as what it is: `risk_authority` and `score_measures` say so on every
payload.

`fix_history` carries no AUC for the same reason. Its evidence is the 46/47
pair gate and that its top-ranked files in this repo are the ones with the
longest bug-fix records. It uses a 365-day half-life (`FIX_HALF_LIFE_DAYS` in
`fix_history.py`) and `DEEP_WALK_LIMIT = 20_000`.

## PR structural impact (`get_risk`)

`structural_impact_score` in `pr_blast.py`:

```
combined   = 0.5 * mean + 0.5 * max of pagerank * (1 + temporal_hotspot) over changed files
file_term  = 8 * (1 - exp(-10 * combined))          # 0-8
reach_term = 2 * min(transitive_dependents / 20, 1) # 0-2
score      = min(file_term + reach_term, 10)
```

Bands: `localized` below 4, `moderate` 4 to below 7, `broad` 7 and up.
Deterministic and uncalibrated. Co-change evidence is reported beside it and
never enters it. `overall_risk_score` is an exact deprecated alias, described
by `overall_risk_score_compatibility`.

The fixture corpus `tests/fixtures/risk_scale_corpus.json` pins the
distribution:

| Control | Score | Band |
| --- | ---: | --- |
| documentation / low signal | 0.01 | localized |
| small ordinary source | 0.34 | localized |
| historical fixes, limited reach | 0.34 | localized |
| co-change only | 0.08 | localized |
| moderate multi-file | 4.27 | moderate |
| structurally broad, little history | 7.34 | broad |
| genuinely broad high control | 9.91 | broad |

No fixture saturates at 10, ordinary controls stay below the moderate
threshold, and all three bands are occupied.

## Public scale inventory

| Public value | Producer | Unit / range | Authority |
| --- | --- | --- | --- |
| `get_change_risk.score` / REST `score` / stored `change_risk_score` | Logistic model over diff size, spread, entropy, author experience | normalized points 0-10, single-commit scale | Supporting; not a probability |
| `risk_percentile` | Mid-rank of the score among filtered recent commits | percentile 0-100 | Authoritative with `classification` |
| `review_priority` / `classification` | Percentile terciles at 33.33 and 66.67 | category | Authoritative |
| `fallback_band` | Score thresholds at 4 and 7, only without a baseline | category | Absolute fallback |
| `fix_history.density` | Churn-weighted, recency-decayed prior fixes | decayed fixes, unbounded | Uncalibrated, separate evidence |
| `get_risk` `hotspot_score` | Repo-relative churn percentile | ratio 0-1 | Uncalibrated component |
| `get_risk` `health_score` | Code-health model | 0-10, higher is healthier | Benchmarked file signal |
| `structural_impact_score` | Formula above | normalized points 0-10 | Uncalibrated; not authoritative |
| `direct_risks[].structural_score` | `pagerank * (1 + temporal_hotspot)` | unbounded | Uncalibrated; `risk_score` is an alias |
| `direct_risks[].temporal_hotspot` | Decayed per-commit churn (half-life 180 d, each commit up to 3.0) | unbounded | Input to `structural_score` |
| `direct_risks[].churn_percentile` | Rank of `temporal_hotspot` in the repo | 0-1 | Rank only |
| `direct_risks[].is_hotspot` | Top-quartile churn plus absolute activity floors | boolean | Use instead of re-deriving from `churn_percentile` |
| `cochange_warnings[].score` | Commits in which the pair co-changed | count | Historical evidence only |
| workspace `impacted[].score` | Strongest path product of edge confidence, kind weight, `0.6` per hop | 0-1 | Uncalibrated ranking |
| dashboard hotspot triage index | `40% churn pct + 35% bus-factor tier + 25% temporal activity` | 0-100 | Client-side orientation |

`risk_authority`, `structural_impact_scale`, `overall_risk_score_compatibility`
and `impact_score_semantics` carry the guard tier (unit, range, calibration,
authority) on every payload. The reference tier (corpus, formula, components,
the full `risk_scales` dictionary) never varies, so MCP returns it only for
`include=["scales"]`; the CLI `--format json` output carries it always.

## Recalibrating

The calibration script lives in the separate benchmark repository
(`health-defect/jit_calibration.py`). Paste the regenerated constants into
`_CONSTANTS` in `model.py`, and update the corpus figures in `model.py` and
`risk_semantics.py` together.
