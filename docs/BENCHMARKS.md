# Benchmarks

Every number repowise publishes, with the method behind it and the limits it
carries. Three studies:

1. [Agent efficiency](#1-agent-efficiency) (context, file reads, tool calls)
2. [Distill](#2-distill-command-output-compression) (command-output compression)
3. [Code health predicts defects](#3-code-health-predicts-defects) (defect ranking, plus a CodeScene head-to-head)

The harnesses, raw run logs, and full reports live in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**. Nothing
here is measured on a private corpus: every study runs on public codebases so
you can re-run it.

Two ground rules for how we report:

- **The limits ship with the wins.** Each study has a "What this does not show"
  section, and it is the section we would want to read first if someone else
  published these numbers.
- **Token savings are not automatically dollar savings.** Agent-side prompt
  caching now mutes the cost delta on repeated context, even where token counts
  drop sharply. We report tokens, reads, and calls because those are what the
  measurements actually establish.

---

## 1 · Agent efficiency

Most of a coding agent's spend goes to exploration: greping for symbols, reading
candidate files, re-reading them as the context window fills. repowise does that
work once, offline, so the agent skips it on every query.

Paired SWE-QA runs on real repositories, same model and same harness, with and
without repowise's MCP tools:

| Measure | Result |
|---|---|
| Tokens to load context | up to **−96%** |
| File reads | **−69% to −89%** |
| Tool calls | **−49% to −70%** |
| Answer quality | at parity with raw exploration |

The mechanism is context substitution, not a smaller model or a shorter answer.
Loading one commit's context through `get_context` costs **2,391 tokens against
64,039** raw, roughly 27x fewer. On a long multi-step investigation the effect
compounds: **−41% of the context re-read across the whole session**, because the
agent is not re-reading files it already saw to recover a detail.

Reports:
[flask48](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_FLASK48.md) ·
[flask v3](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_FLASK_V3.md) ·
[sklearn48](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_SKLEARN48.md)

An earlier cut of the same paired setup is quoted in
[COMMERCIAL.md](business/COMMERCIAL.md) as −36% cost / −49% tool calls on
`pallets/flask` and −29% cost / −70% tool calls on `scikit-learn`, both at
parity answer quality.

### How to reproduce

1. Clone [repowise-bench](https://github.com/repowise-dev/repowise-bench) and
   the target repository (`pallets/flask` or `scikit-learn`) at the pinned
   commit named in the report you want to reproduce.
2. Index the target: `repowise init <path>`.
3. Run the harness twice over the same SWE-QA question set with the same model:
   once with the repowise MCP server registered, once without. Each report names
   its own question set, model, and pinned commit.
4. Compare the per-run token, file-read, and tool-call totals the harness emits.
   The answer-quality check is a graded comparison of the two answer sets, also
   described in the report.

The `get_context` figure is the cheapest thing to check on your own repo: run
`get_context` on a commit and compare the response size against the raw bytes of
the files it covers.

### What this does not show

- **Not a SWE-bench-style task-completion result.** These runs measure the cost
  and shape of answering questions about a codebase, not the rate at which an
  agent lands a correct patch.
- **Answer quality is "at parity", not "better".** The claim is that the agent
  reaches the same quality of answer for far less exploration. If a benchmark
  showed a quality gain, we would report a quality gain.
- **The ranges are wide because the repos differ.** "up to −96%" is a ceiling
  observed on a specific context-loading comparison, not the average case. Read
  the per-repo reports for the distribution.
- **Prompt caching changes the economics.** With agent-side caching, a large
  repeated context can be cheap in dollars even when it is expensive in tokens.
  Token reduction still buys you context-window headroom and fewer round trips;
  it no longer buys a proportional dollar saving.
- **Same model, same harness, one vendor's agent.** We have not shown the effect
  transfers unchanged to every agent framework.

---

## 2 · Distill: command-output compression

`repowise distill <cmd>` compresses command output *before* the agent reads it:
errors first, exit code preserved, and every omission recoverable through an
inline `[repowise#<ref>]` marker (`repowise expand <ref>`).

Paired runs on a public OSS repository (microdot), one run per command, tokens
estimated at chars/4, which is the same estimator the savings ledger uses:

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%**, all 11 `FAILED` lines preserved |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits of history) | 62,833 | 8,635 | **86%** |
| `git log --oneline -30` | 321 | 321 | 0%, already compact, passed through |
| `git status` (clean tree) | 83 | 83 | 0%, too small to distill, passed through |

The two 0% rows are the net-positive guard doing its job: distill never inflates
small output. In an end-to-end spot-check on the same repo with a seeded
11-failure bug, the agent reached the identical root-cause line and fix from
distilled output as from raw. Across the fixture suite, the core filters hold a
median of at least 60% reduction on test/build/lint output with zero error-line
loss, asserted in CI.

Full guide: **[docs/agent/DISTILL.md](agent/DISTILL.md)**.

### How to reproduce

1. Clone microdot (or any repo with a test suite) and index it with
   `repowise init`.
2. For each command, capture the raw output and the distilled output:

   ```bash
   pytest -q > raw.txt 2>&1
   repowise distill pytest -q > distilled.txt 2>&1
   ```

3. Compare sizes with the chars/4 estimator. `repowise saved` reports the same
   accounting cumulatively across a session.
4. To reproduce the failure-preservation claim, diff the `FAILED` lines in
   `raw.txt` against `distilled.txt`. They should match exactly.

### What this does not show

- **One run per command, one repository.** These are point measurements, not a
  distribution over many repos with confidence intervals. Your ratios will
  differ with your test suite's verbosity and your diff sizes.
- **Reduction is not comprehension.** The 61/89/86% figures measure bytes
  removed. The evidence that the removal is safe is narrower: preserved failure
  lines, the CI-asserted zero-error-line-loss fixtures, and a single end-to-end
  agent spot-check. One spot-check is an existence proof, not a rate.
- **`git diff` compresses hardest because diffs are the most redundant input.**
  Do not read 86% as the expected saving on arbitrary commands.
- **Token estimation is chars/4, not a tokenizer.** It is consistent across the
  raw and distilled sides, so the ratio is sound, but the absolute counts are
  approximations.
- **Dollar savings again depend on caching.** `repowise saved` prices tokens at
  your agent's model rate; a cached context reduces the real-world delta.

---

## 3 · Code health predicts defects

The health score is worth something only if the files it flags are the files
that actually break. Scores are collected at a historical commit (T0),
bug-fixing commits are counted over the following six months, and the two are
correlated with strictly no leakage: nothing after T0 feeds the score.

### Cross-project validation

Across **21 open-source repositories spanning 9 languages** and 2,826 files
(the study predates the promotion of Scala and Ruby to the Full tier, so it
covers 9 of today's 11):

- **Mean ROC AUC 0.74** (95% CI 0.68 to 0.79) at identifying the files that go
  on to receive bug fixes, reaching **0.90** on individual repos. ROC AUC is the
  probability the score ranks a known-buggy file worse than a clean one: 0.5 is
  a coin flip, 1.0 is perfect.
- **Survives controlling for file size**: partial Spearman rho = −0.16, so the
  signal is not simply "flag the big files".
- **Out-discriminates the obvious baselines**: +0.10 AUC over recent churn and
  +0.12 AUC over prior-defect history, DeLong p < 1e-9.
- **Holds on an external dataset it never saw**: PROMISE/jEdit CK-metrics, AUC
  **0.76 to 0.78**, within about 0.03 of that dataset's own tuned model. This
  held-out result is the main evidence the markers are not overfit to the
  calibration corpus.

Full report:
**[health-defect/BENCHMARK_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md)**

### CodeScene head-to-head

CodeScene is the closest commercial product on code health and the only vendor
in the category with a published empirical defect study (the "Code Red"
correlation study, Tornhill and Borg, TechDebt 2022). Both tools were run over
the **same 2,770 files across 9 languages**, scored at the same leakage-free
commit against the same defect labels:

| Axis (paired tests) | repowise | CodeScene |
|---|---:|---:|
| Recall at a 20%-of-lines review budget | **0.173** | 0.074 |
| Effort-aware ranking (Popt) | **0.607** | 0.462 |
| Defect density, size-normalized (defects/KLOC, Alert:Healthy) | **2.18x** | 0.56x |

Ranking by repowise health surfaces **2.3x the defects under a fixed review
budget**. The deltas are Popt +0.144 and recall +0.098, both p = 0.003, paired
and significant.

Full methodology and confidence intervals:
**[health-defect/COMPARISON_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)**.

### The per-repo self-check

Separate from the cross-project study, every index prints a check against your
own history:

```
Does the score find the bugs? 16/20 lowest-health files had a bug fix in the
last 6 months, 3.3x the 24% baseline (80% vs 24%).
```

Agents can read the same block over MCP with `get_health(include=["accuracy"])`.
It stays silent on repos with too little history to be honest (fewer than 25
scored files, or fewer than 5 recently-fixed files). Details in
[docs/layers/CODE_HEALTH.md](layers/CODE_HEALTH.md#does-the-score-find-the-bugs).

### How to reproduce

1. Clone [repowise-bench](https://github.com/repowise-dev/repowise-bench) and
   open `health-defect/`. The corpus list, the T0 commit per repo, and the
   bug-window definition are all pinned there.
2. The harness checks out each repo at T0, runs the health pass, then labels
   files from `fix:` commits in the following six months and computes ROC AUC,
   Popt, recall@20%, and the DeLong comparisons against the churn and
   prior-defect baselines.
3. The PROMISE/jEdit arm needs no repowise index at all: it scores the published
   CK-metrics dataset with the same marker weights.
4. The CodeScene arm requires a CodeScene account. The comparison is restricted
   to the 2,770 files both tools scored, so it can be re-derived from the two
   exported score sets plus the shared label file.
5. For the per-repo self-check, just run `repowise init` then `repowise health`
   on any repo with enough history.

### What this does not show

- **The per-repo callout is an association, not a forward prediction.**
  `prior_defect` is itself one (down-weighted) input to the score, so the
  "16/20 lowest-health files" line is measured on the indexed history. The
  cross-project study is the leakage-free one.
- **Within a size band the signal is weak.** Among files of similar size the AUC
  sits near 0.49, so part of the headline number is that larger files carry more
  risk. We report this because it is the most important caveat on the AUC.
- **A prior-defects baseline still wins on effort.** Under a fixed review budget
  that baseline finds bugs slightly more efficiently than the repowise score
  (Popt by 0.085), even though the score out-discriminates it on AUC.
- **The CodeScene comparison is scoped.** It covers 2,770 shared files at one
  leakage-free commit. The significant wins are Popt, recall@20%, and defect
  density; the ROC AUC edge is marginal (+0.026, p = 0.054) and precision@20% is
  a tie, not a win. The operating points also differ: CodeScene flags about 27
  files where repowise flags 132, a more conservative threshold, which is why
  its precision reads higher. This is not an unqualified "better than
  CodeScene", and it says nothing about CodeScene's breadth (28+ languages,
  knowledge maps, off-boarding simulation).
- **"Bug fix" means a `fix:` commit touching the file.** That is a proxy for a
  defect, and it inherits every quirk of the corpus repos' commit hygiene.
- **Maintainability and performance are not validated this way.**
  Maintainability weights are expert-set and the performance signal is
  high-precision, low-recall and advisory. Only the defect pillar carries these
  numbers, which is exactly why repowise refuses to blend the three into one
  headline score.

### Related: static performance risk

The performance pillar has its own, separate benchmark. On a 12,000-file corpus,
standard linters (clippy, ruff `PERF`, ESLint, golangci-lint) found **0** of the
cross-function I/O-in-loop cases, while repowise surfaced 557 findings, about 90
of them spanning function boundaries, with 98% in categories ruff has no rule
for. Findings are ordered by impact rather than raw count (NDCG 0.755 against
0.292 for severity-only). Hand-labeled `io_in_loop` precision on an 11-repo OSS
corpus: Go 96.7%, TypeScript 100%, Python 96.2%. One caveat travels with it: the
Rust dialect was new when the benchmark ran and clippy could not be built
end-to-end on the corpus under Windows, so the Rust comparison is
catalogue-level, not a measured head-to-head. Data and method:
[perf-detection](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

---

## See also

- [docs/layers/CODE_HEALTH.md](layers/CODE_HEALTH.md): the score, the 25
  markers, the bands, and the validation section in full.
- [docs/agent/DISTILL.md](agent/DISTILL.md): distill filters, the omission
  store, and the hook.
- [docs/agent/MCP_TOOLS.md](agent/MCP_TOOLS.md): the tools the agent-efficiency
  study exercises.
- [repowise-bench](https://github.com/repowise-dev/repowise-bench): harnesses,
  raw logs, and every full report.
