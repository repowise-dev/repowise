# Benchmarks

Every number repowise publishes, with its sample size, its test, and a link to the
raw data. Nothing here is measured on a private corpus. The harnesses, the
pre-registrations and every graded cell live in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**, which is also
where the depth is: this page is the summary, that repository is the evidence.

**Read this page in one minute:** the two charts below are the two retrieval
results, and [§8](#8-the-same-question-against-an-answer-key-we-do-not-control)
is the graph result, where a compiler rather than we decided who was right.
Everything else is the sample size, the caveat and the row we lose.

| Layer | Measured against | Result |
|---|---|---|
| Finding the right files | CodeGraph, Graphify, code-review-graph, cocoindex | [§1](#1-finding-the-right-files) **we win**, n=42 held out, p=0.00004 |
| Work saved in a real agent loop | the same field plus Serena and a bare agent | [§2](#2-what-changes-in-a-real-agent-loop) **we win** on all three agent harnesses we tried, n=43 at p&lt;0.0001 |
| Loading one commit's context | naive file reads, `git diff` | [§3](#3-loading-one-commits-context-the-easy-number) **35.6x** fewer tokens than naive |
| Command-output compression | RTK | [§4](#4-command-output-compression) **not measured head to head** |
| Code health and defect prediction | CodeScene | [§5](#5-code-health-predicts-defects) **we win** on recall and effort-aware ranking, p=0.003 |
| Indexing time | CodeGraph, Graphify, code-review-graph | [§6](#6-indexing-time-the-row-we-lose) **we lose**, 22x, because we build four more layers in the same pass |
| Are the call edges true | CodeGraph | [§7](#7-edge-precision) **we win**, 84.8% against 57.0%, 540 rows hand-graded from source |
| The same question, judged by a compiler | CodeGraph, codebase-memory-mcp | [§8](#8-the-same-question-against-an-answer-key-we-do-not-control) **most precise arm in 7 of 7 cells**, and the recall column we lose |
| Documentation generation | DeepWiki, Google Code Wiki, Swimm | **not measured** |
| PR review | CodeRabbit, Greptile | **not measured** |

The last two rows are capability comparisons, not measurements, and they live in
the [README's feature table](../README.md#how-it-compares-on-capability) where a
reader can tell the difference. **§4 carries the same label for the same reason:**
RTK does exactly what `repowise distill` does, and we have not run the two against
each other. We would rather write "not measured" than let a checkmark do a
number's job.

---

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../.github/assets/bench/file-coverage-dark.svg" />
  <img src="../.github/assets/bench/file-coverage.svg" alt="File coverage on 42 sealed ContextBench instances: repowise get_answer 0.876, repowise search_codebase 0.742, CodeGraph 0.610, Graphify 0.546, code-review-graph 0.445, cocoindex 0.361" width="100%" />
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../.github/assets/bench/agent-output-tokens-dark.svg" />
  <img src="../.github/assets/bench/agent-output-tokens.svg" alt="Output tokens an agent writes to reach an answer across 43 django questions on Codex: repowise 1,250, CodeGraph 1,383, Serena 1,550, Graphify 1,658, code-review-graph 1,710, bare agent 1,828 baseline" width="100%" />
</picture>
</div>

---

## 1. Finding the right files

Before a tool can save an agent any work, it has to point at the right code. This
section measures only that.

**Grading is deterministic. No LLM judge is involved anywhere in this number**,
which makes it the most reproducible result on the page. ContextBench ships gold
file spans; a tool either returns them or it does not.

**Every number below comes from instances this work has never seen.** The 112
instances were split 70 / 42 by instance id, **pinned before any of it started**,
and the 42 were kept sealed until the final measurement.

| Tool | File coverage | n | Precision | Files served |
|---|---:|---:|---:|---:|
| **repowise** (`get_answer`) | **0.876** | 42 | 0.087 | 19.2 |
| **repowise** (`search_codebase`) | **0.742** | 42 | **0.168** | **8.2** |
| CodeGraph | 0.610 | 42 | 0.093 | 14.0 |
| Graphify | 0.546 | 42 | 0.033 | 34.5 |
| code-review-graph | 0.445 | 42 | 0.240 | 5.4 |
| cocoindex | 0.361 | 41 | 0.092 | 7.1 |

Head to head per instance against CodeGraph: `get_answer` **19 wins, 1 loss, 22
ties, sign test p = 0.00004**. `search_codebase` **13 wins, 3 losses, 26 ties,
p = 0.021**.

**These are two different tools with two different profiles, and we would rather
say so than average them into one claim.** `get_answer` finds the most, from a list
of ~19 files. `search_codebase` finds fewer but is the most efficient per file
served: **0.742 from 8.2 files**, better coverage-per-file than anything else in
the table. If you are paying by the token, that is the row to read.

**Precision is not our column.** code-review-graph's 0.240 is nearly three times
ours. Part of that is mechanical, since precision rises for whoever returns fewest
files, which is exactly why files-served is a column here and not a footnote.

**The cocoindex row is not from the same sitting**, and it is printed rather than
smoothed away: measured 2026-08-09 against the other five from 2026-08-02 to
2026-08-06, same instances, same gold spans, same deterministic grading. Its n is
41 because one named instance never answered even when queried alone, so it is
excluded rather than counted as a zero, which makes its row *better* than counting
it would (0.361 against 0.353). Its placing was
[pre-registered before its index existed](https://github.com/repowise-dev/repowise-bench/tree/master/configs),
including the prediction that it would come last.

### Why this is not benchmark tuning

We first ran this and came **last, at 0.228**. We published that. The cause was a
query-time gate discarding most candidates before ranking ever happened. Fixing
that path is what moved the number, and it is a fix any user gets, not a change
shaped around these questions.

The check on that claim is the split, and it points the right way:

| | other half (n=70) | **sealed half (n=42)** |
|---|---:|---:|
| repowise (`get_answer`) | 0.810 | **0.876** |
| repowise (`search_codebase`) | 0.684 | 0.742 |
| CodeGraph | **0.6093** | **0.6095** |

**Overfitting makes the unseen half score worse. Ours scores better**, on both
tools. CodeGraph, which nobody tuned against either half, scores the same on both
to three decimal places, so the two halves are equally hard and the gap is about
the tool rather than the questions.

**We do not quote a pooled 112-instance figure**, though it is easy to compute and
would be 0.835. Averaging the halves loses the only number that matters.

This is retrieval, not task success. It says we find the right files, not that an
agent using us writes better code. That is what §2 is for. Producing it cost
**748 index builds and roughly 78 machine-hours**, because every arm builds its own
index of every instance at that instance's own `base_commit`, with nothing shared
and nothing cached.

Measured on repowise `081a59fa` (between v0.37.0 and v0.38.0). Raw cells:
**[rung8](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung8)**.

---

## 2. What changes in a real agent loop

The section a skeptic should read first, and the one modelled on the JetBrains
reruns described [at the bottom of this page](#how-to-read-a-number-on-this-page).

Every question in `django/django`'s question set, 48 of them, across five question
shapes. Six arms: repowise, four competing tools, and a bare agent with no tools.
Byte-identical prompt, full advertised tool surface per arm, a freshly built index
on the same pinned commit, and a bare control verified free of local hooks.

Two things have to be true for a tool to be worth mounting: the agent has to
actually call it, and the loop has to get leaner when it does.

**We ran this on three agent harnesses, because the answer depends on the harness
as much as on the tools.**

### The main run: 48 questions on Codex (`gpt-5.6-sol`)

Every tool called on every question, so this is like-for-like.

| Tool | Agent used it | Output tokens | vs bare agent | Tool calls | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|
| **repowise** | **44 / 44** | **1,250** | **-31.6%** | **3.8** | **37 of 44** | **<0.0001** |
| CodeGraph | 44 / 44 | 1,383 | **-24.4%** | 4.0 | 37 of 44 | **<0.0001** |
| Serena | 43 / 43 | 1,550 | -14.8% | 10.1 | 35 of 43 | <0.0001 |
| Graphify | 43 / 43 | 1,658 | -8.9% | 7.4 | 31 of 43 | 0.003 |
| code-review-graph | 43 / 43 | 1,710 | -6.0% | 7.2 | 26 of 43 | 0.046 |
| *bare agent (control)* | 0 / 44 | 1,828 | baseline | 7.2 | n/a | n/a |

A third less output than working with no tool at all, reached in **3.8 tool calls
against the bare agent's 7.2**, opening 3.0 files instead of 7.2. One answered
question replacing roughly six greps, visible in the call counts rather than
inferred.

Correcting for testing five tools at once, three reductions are solid and two are
marginal. **CodeGraph is a genuine second at -24.4%**, and the honest reading is
that we lead a field in which more than one tool works. Serena is the counter-case:
it writes less than the bare agent while calling tools **42% more often**. Busier,
not leaner.

5 of the 48 questions are missing from every arm equally because the run hit an API
usage cap. Paired comparisons are unaffected; the figures are over the 43 all six
arms completed.

**The saving grows with the work.** Splitting at the median by how much the bare
agent needed: easier half 27.2%, harder half **34.3%**, correlation +0.379.
Pre-computed structure replaces exploration, and harder questions contain more
exploration to replace. That split is post-hoc and the pre-registered comparisons
are the stronger evidence.

### Second harness: 15 questions on Claude Code (`claude-sonnet-5`)

| Tool | Tools advertised | Schema cost (chars) | Agent used it | Output tokens | vs bare | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|---:|
| **repowise** | 10 | 17,561 | **15 / 15** | **2,420** | **-15.9%** | **12 of 15** | **0.035** |
| CodeGraph | 1 | 1,567 | 13 / 15 | 2,540 | -11.7% | 10 of 15 | 0.302 |
| Serena | 29 | 29,050 | 4 / 15 | 2,551 | -11.3% | 8 of 15 | 1.000 |
| code-review-graph | 30 | 28,118 | **0 / 15** | 2,768 | -3.8% | 10 of 15 | 0.302 |
| Graphify | 10 | 5,482 | 3 / 15 | 2,878 | 0.0% | 7 of 15 | 1.000 |
| *bare agent (control)* | 0 | 0 | n/a | 2,877 | baseline | n/a | n/a |

The "agent used it" column is the interesting one. Under Claude Code most of these
tools were barely called at all: code-review-graph never once, Graphify three times
in fifteen. Nothing differed about the servers, questions or indexes between the
two harnesses. Claude Code loads MCP tool schemas on demand, so the agent has to go
looking before it can call anything, and frequently never does.

**Treat that column as unstable, including our own 15 of 15.** Reruns on later days
returned 4 of 15 and then 3 of 15 for us, and 2 of 14 for CodeGraph.

**Is the collapse the model or the harness? Inconclusive.** The same 15 questions on
Opus, against bands fixed before the run (12+ means the model, 6 or fewer means
schema deferral, 7 to 11 inconclusive) **came back at 7**. Opus goes looking on 11
of 15 and declines about a third of the times it looks, so deferral is part of the
story and not all of it. **That run's token column fails its own control** (-9.3%
when the tool was called against -10.7% when it was not), so no token figure is
quoted from it for any tool including ours.

### Third harness: a small model on your own hardware

`qwen3:8b` under Ollama, driven by opencode, same 15 django questions, same seed.
Inference cost is zero, so the question becomes "is it faster, and is it better".

**Every cell called its tool: 15 of 15 on both rows**, against 7 of 15 for Opus on
this identical draw. Adoption ordering across four instruments is Sonnet 3 to 4,
Opus 7, Codex 15, local `qwen3:8b` 15.

| Row | Agent used it | Output tokens | vs bare | Leaner on | p | Wall clock | vs bare |
|---|---:|---:|---:|---:|---:|---:|---:|
| **repowise, full surface** | **15 / 15** | **1,319** | **-40.8%** | **15 of 15** | **0.00006** | **117s** | **-27.5%** |
| **repowise, local-only tools** | **15 / 15** | **1,172** | **-47.9%** | **15 of 15** | **0.00006** | **96s** | **-41.5%** |
| *bare agent (control)* | 0 / 15 | 2,336 | baseline | n/a | n/a | 171s | baseline |

**Two repowise rows, never combined.** `get_answer` writes its answer using a hosted
model, so a row using it is not a local-only result. The second row switches it off
and leaves only tools that run against the local index. We verified the restriction
holds rather than assuming it: instructed directly and repeatedly to call
`get_answer`, that agent could not reach it in any of its 15 cells.

**On a local model the win shows up as time, and the mechanism differs from the
hosted case.** repowise roughly doubles the tokens fed in on a single step while
cutting steps from 3.3 to 2.1 and halving what the model writes. Reading a large
payload once is cheap on a GPU; generating text token-by-token across several
rounds is not.

**We are not claiming a quality improvement from this run.** The full surface scored
+1.32 against the bare agent on a 0 to 10 judge scale, above the judge's measured
0.69 noise, but the win-loss count is 10 to 5 at p = 0.30 and removing the single
best question drops it to +0.99. The local-only row is **+0.20, a null**. The
defensible statement is narrower: **the local-only configuration answers about as
well as a bare agent in roughly half the wall clock, on hardware you already own.**

### What this section will not claim

- **This is work saved, not quality.** A blind judge scored every tool in the field,
  ours included, a fraction **below** the bare agent on the 48-question run, in a
  range of 0.04 to 0.25 points on a 10-point scale. All are smaller than the 0.69
  points by which this benchmark moves when rerun unchanged. No tool here measurably
  changed answer quality in either direction. Ours is at the low end of that band
  and we will say so if it becomes a real effect. "No significant difference" is not
  parity, and an equivalence claim needs a test we have not run.
- **The quality columns cannot be compared across tables.** Different judges, because
  grading a model with a judge from its own family is a known bias.
- **Not a universal saving.** One repository, one commit, one prompt. Three
  harnesses, which is more than most published numbers in this category, and still
  not a constant.
- **Adoption is not a stable property of a tool.** Whether an agent calls a codebase
  server at all depends more on the harness than on the server. Any adoption figure,
  ours included, is only meaningful with its harness and its date attached.
- **No dollar figure**, and the control that retired it is the most useful thing we
  ran. code-review-graph, having never called its server once in 15 questions and
  carrying 28,118 extra characters of schema that should cost *more*, measured
  **43% cheaper than the bare agent**. The cause is prompt caching: whichever arm
  runs first warms it. An arm's position in the cycle correlated **-0.487** with its
  dollar cost. Output tokens are never cached and correlate **+0.010**, so those are
  what we publish. If you see a token claim here that does not say whether it
  controls for cache state and arm ordering, ask about that first.

**What producing these tables cost: 471 agent runs, about 13 hours of machine time,
roughly $44 of API spend**, plus 11.3 minutes of fresh index builds for every tool.
Roughly a third of those runs are gates and repeats rather than headline numbers.
Full breakdown, and the per-tool setup traps that were most of the real effort, in
[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head).

Raw data, every cell including failures:
**[rung9](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung9)**
(48-question Codex) and
**[rung6](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung6)**
(the 15-question runs).

---

## 3. Loading one commit's context, the easy number

This is the measurement almost every tool in this category publishes, and we are
labelling it as such. Over the 30 most recent non-merge commits of `pallets/flask`,
counted with deterministic `tiktoken` (`cl100k_base`):

| Strategy | Tokens per commit |
|---|---:|
| naive, full contents of every changed file | 13,984 |
| `git diff` only | 1,408 |
| **`get_context`** | **393** |

**35.6x fewer than naive, pooled**, 29.3x as a mean of per-commit ratios, and 3.6x
pooled against `git diff`.

**Lead with the pooled figure.** Pooled is sum over sum, so it weights each commit by
the tokens actually at stake. A mean of per-commit ratios does not: a one-line commit
where `get_context` returns 40 tokens contributes a huge ratio that counts equally
against one saving a hundred thousand. That is how a 35x becomes a 209x in a press
release.

**What this does not tell you.** It is one payload, not a session. It says our
representation of a commit is smaller than the commit, not that an agent finishes
faster. The honest version of that question is §2, where the answer on the same
harness is a much more modest 15.9%.

Raw CSV:
**[token\_efficiency\_flask30](https://github.com/repowise-dev/repowise-bench/blob/master/results/bakeoff_2026_08/rung1/token_efficiency_flask30_2026-08-01.csv)**

---

## 4. Command-output compression

`repowise distill <cmd>` compresses command output *before* the agent reads it:
errors first, exit code preserved, every omission recoverable through an inline
`[repowise#<ref>]` marker.

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%**, all 11 `FAILED` lines kept |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits) | 62,833 | 8,635 | 86%, **unsupported, see below** |
| `git log --oneline -30` | 321 | 321 | 0%, already compact |
| `git status` (clean) | 83 | 83 | 0%, too small to distill |

The two 0% rows are the net-positive guard working: distill never inflates small
output. One run per command on one repository, so these are point measurements.
Reduction is also not comprehension: the bytes removed are measured, the evidence
they were safe to remove is narrower.

**One row above is inflated, by the mechanism that broke RTK's number.** A saving may
only count output the agent would actually have received, and the host truncates a
command result at 30,000 characters. `git diff`'s 62,833 raw tokens is eight times
that cap and predates it. **Treat that 86% as unsupported until re-measured.**
`pytest` and `git log` sit under the cap and are unaffected.

**There is a comparable tool and we have not run it.**
[RTK](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) does this
and essentially only this. It is also the tool whose advertised 60 to 90% came back
**7.6% more expensive** under JetBrains' rerun. So this table is a before-and-after
of our own output, which is the weaker design. Only a paired run would settle it.

Full guide: **[docs/agent/DISTILL.md](agent/DISTILL.md)**

---

## 5. Code health predicts defects

A health score is worth something only if the files it flags are the files that
break. Scores are taken at a historical commit, bug fixes are counted over the
following six months, and nothing after the scoring commit feeds the score.

Across **21 repositories, 9 languages, 2,826 files**: **ROC AUC 0.737**
(95% CI 0.683 to 0.787), ranging 0.55 to 0.86 across individual repos. It beats
recent churn by +0.100 AUC and prior-defect history by +0.117 (DeLong p < 1e-9). On
PROMISE/jEdit, a dataset it never saw which carries no git history at all, it holds
at **0.76 to 0.78**.

**It is not better than raw file size at discrimination.** LOC-only scores 0.742
against our 0.737 (p = 0.92, a tie). Where it wins is effort-aware ranking, Popt
+0.134 (95% CI +0.080 to +0.198): same discrimination as counting lines, much better
at ordering a fixed review budget, and unlike a line count it says why. Holding size
fixed, within-band AUC runs 0.525 / 0.572 / 0.593 / 0.718 across NLOC quartiles, so
the signal survives cleanly only in the largest quartile, and a positive control
confirms that collapse is a real absence rather than too few positives to detect one.

**Against CodeScene**, the closest commercial product and the only other vendor in
this category with a published empirical defect study. Both tools scored the same
2,770 files at the same leakage-free commit against the same labels:

| Paired test | repowise | CodeScene | |
|---|---:|---:|---|
| Recall at a 20%-of-lines review budget | **0.173** | 0.074 | p = 0.003 |
| Effort-aware ranking (Popt) | **0.607** | 0.462 | p = 0.003 |
| Defect density, size-normalized (Alert:Healthy) | **2.18x** | 0.56x | p = 0.003 |
| Discrimination (ROC AUC) | 0.731 | 0.705 | p = 0.054, marginal |
| Precision at a 20%-of-lines review budget | 0.580 | **0.636** | p = 0.64, a tie |

Ranking by repowise health surfaces **2.3x the defects under a fixed review budget**.

**Where CodeScene is ahead, and why it is a real choice.** Its precision lead is not
significant, but the behaviour behind it is worth having: it flags about **27 files**
where we flag **132**, trading recall for a short list a team will actually work
through. That is a deliberate operating point, not a weaker model. Our AUC edge is
**marginal**, and so is our raw defect-density lead (16.9x against 14.2x, p = 0.65 on
a heavy tail); the size-normalized version is the one that reaches significance and
the one to cite.

**The axis we lost outright.** CodeScene's "Code Red" study reports a **-0.58**
correlation between Code Health and mean issue-resolution time, on proprietary Jira
data. We could not replicate it on open data: across 17 repositories and 271 files,
GitHub PR merge time correlated at **-0.09** (95% CI -0.19 to +0.14), and six
queue-independent effort signals all came back flat with every interval spanning
zero. Merge time on GitHub largely measures maintainer review-queue availability.
**The business-impact axis remains CodeScene's, unreplicated on open data.**

Reports:
**[BENCHMARK\_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md)** ·
**[COMPARISON\_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)**

---

## 6. Indexing time, the row we lose

We are the slowest indexer in the field, on every repo we measured, and it is not
close. On `django/django`:

| Tool | Index time | What it builds |
|---|---:|---|
| CodeGraph | 16.4s | call graph |
| code-review-graph | 44.8s | call graph |
| Graphify | 141.5s | call graph, communities |
| **repowise** (`--no-prose`) | **366.8s** | five layers, below |
| **repowise** (default, prose on) | **1,058s** | five layers plus generated documentation |

That is **22x** CodeGraph like for like, and **135x** with prose on, which is what a
default `repowise init` actually costs you. Both numbers ship, and the 22x is not the
user-facing one.

**Here is what the extra time buys.** The tools above build a call graph. In the same
run, repowise builds: a **graph** of 36,485 nodes, 90,477 edges and 31,384 symbols
plus PageRank, betweenness, Leiden communities and execution-flow tracing; **git**
history mined across 2,630 files for hotspots, ownership, co-change pairs and bus
factor; **3,392 wiki pages** rendered and embedded for natural-language search;
**architectural decisions** mined from history and sessions; and **code health**,
5,317 findings plus 155 unreachable files and 98 unused exports.

So "22x slower" and "the index contains categorically more" are both true, and
neither cancels the other. If all you want is a call graph, CodeGraph builds one in
16 seconds and you should use it. The comparison that would be dishonest is quoting
the ratio without the column beside it, which is why the column is here.

It is also a one-time cost. Updates after the first index are incremental.

A fitted build-cost curve for five tools across a 12x repository-size range, which
nobody in this field had published, is in
[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head#the-result-nobody-in-the-field-had-published-a-build-cost-curve).
Our exponent is sublinear at 0.906, and we publish that as sublinear **work** rather
than efficiency, because symbol density halves across the range. That is a product
finding against us.

---

## 7. Edge precision

Every other row on this page counts things. This one asks whether they are **true**.

A resolver that guesses aggressively wins a coverage table and a raw edge count while
sending its reader to the wrong function. So we hand-graded call edges from source, on
both sides, by the same method: 30 rows per language per tool, seed 2026, stratified by
resolution strategy, every row read with its imports and enclosing scope open.

| | correct / n | 95% CI |
|---|---|---|
| **repowise** | **229/270 = 84.8%** | [80.0, 88.6] |
| **CodeGraph 1.5.0** | **154/270 = 57.0%** | [51.1, 62.8] |

Per language, nine languages, both sides read:

| Language | repowise | CodeGraph | |
|---|---|---|---|
| typescript | 29/30 | 7/30 | separates |
| go | 29/30 | 29/30 | tie |
| csharp | 28/30 | 20/30 | separates |
| python | 28/30 | 19/30 | separates |
| kotlin | 27/30 | 13/30 | separates |
| swift | 23/30 | 19/30 | tie |
| cpp | 23/30 | 16/30 | tie |
| rust | 22/30 | 13/30 | tie |
| java | 20/30 | 18/30 | tie |

**Read our number the other way round: roughly fifteen percent of our call edges are
wrong.** That is the number we plan against, and rust, java and cpp are where it
concentrates.

**Four of nine cells separate. Five are ties and we report them as ties.** At n=30 the
interval runs about ±16 points near 60%, so a point-estimate gap inside two overlapping
intervals is not a win. C++ is a tie despite looking like a 23-point lead.

**One repository goes clearly to them.** On `seastar` CodeGraph reads 6/10 against our
4/10, the only repository in the audit, on any language, where they beat us on a clear
margin. Our misses there are chained calls on an untyped receiver; they infer the
callee's declared return type and validate against it, so a failed inference costs them
an edge instead of buying them a wrong one. On `aria2` both sides read 10/10 and they
resolve 24,950 distinct call edges to our 9,486. Precision is not the only reading.

Cells were measured at different commits, and the staleness runs **conservative**: every
resolver change in between only removes wrong edges and gained zero, so 84.8% is a floor.
No cell was measured on the 0.44.0 release; the full table pins a commit per cell.

**All 540 graded rows are published**, one file per cell, each row carrying the call site,
the declaration the tool bound it to, the verdict and the reason it was given. A script in
the same directory rebuilds every table above from them and fails if it disagrees. One cell
of the eighteen, rust on our side, ships the 30 sites that were read without their per-row
verdicts, because that cell was re-read on a fresh draw and the verdicts of that read were
never written down; the file says so on every row.
**[graph/experiments/g1-edge-precision/rows](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g1-edge-precision/rows)**.

---

## 8. The same question, against an answer key we do not control

Section 7 is hand-graded, by us, on both sides. That is the strongest form of a
weak thing: **every graph-quality number in this field, ours included, is scored
against something the publisher controls.** A tool that is confidently wrong in a
consistent way scores well and the reader has no way to check.

So we re-asked the question with the answer key taken out of our hands. On Go the
oracle is the **Go team's own RTA call graph** from
`golang.org/x/tools/go/callgraph/rta`, over the fully type-checked program. On
TypeScript it is the **`tsc` type checker's own resolution** of every call site.
We did not write either one, we cannot tune either one, and anyone with the
toolchain can regenerate both.

The field gains a third arm here. **codebase-memory-mcp** is not in the retrieval
sections above because it is not that kind of tool; it is in this one because it
builds a call graph, and because it is the arm that beats us on coverage.

**Precision: of the call edges a tool emits, the share the compiler confirms.**

| cell | repowise | CodeGraph 1.5.0 | codebase-memory-mcp 0.10.8 |
|---|---|---|---|
| cobra (with tests) | **0.972** [0.963, 0.980] | 0.929 [0.916, 0.940] | 0.912 [0.898, 0.925] |
| gitleaks (no tests) | 0.976 [0.967, 0.982] | 0.972 [0.962, 0.979] | 0.934 [0.921, 0.945] |
| gitleaks (with tests) | 0.974 [0.965, 0.981] | 0.971 [0.961, 0.978] | 0.922 [0.909, 0.934] |
| syft (no tests) | **0.943** [0.935, 0.949] | 0.872 [0.862, 0.881] | 0.635 [0.623, 0.646] |
| syft (with tests) | **0.950** [0.945, 0.955] | 0.864 [0.857, 0.871] | 0.673 [0.665, 0.682] |
| zod (no tests) | 0.992 [0.984, 0.996] | 0.729 [0.694, 0.762] | 0.987 [0.977, 0.992] |
| hono (no tests) | 0.977 [0.961, 0.987] | 0.805 [0.771, 0.835] | 0.949 [0.926, 0.965] |

**We are the most precise arm in seven cells of seven.** Bold marks the three
cells where our interval clears both other arms at once. Against each competitor
taken singly we separate in five of seven: against CodeGraph everywhere except
the two gitleaks cells, against codebase-memory-mcp everywhere except the two
TypeScript cells. **The rest are ties and are printed as ties.**

This also does something section 7 cannot: it removes the n=30 ceiling.
Hand-grading caps a precision estimate at roughly ±13 points near a 95% rate. An
oracle grades every edge, so n becomes the size of the repository, and the seven
cells above are judged over 37,853 oracle edges rather than 540 hand-read rows.

**The two methods agree.** On Go, section 7 read 29/30 = 96.7% by hand for us and
29/30 = 96.7% for CodeGraph. The Go compiler, over roughly 1,600 edges on the same
repository, says 97.6% and 97.2%. Two unrelated methods, one person reading source
and one type checker, land within about a point on both arms. That is the result
we care about most and it is not a competitive one.

### The column we lose

Recall runs the other way, and we do not lead it.

| cell | repowise | CodeGraph | codebase-memory-mcp |
|---|---|---|---|
| cobra (with tests) | 0.684 [0.664, 0.704] | 0.763 [0.745, 0.781] | 0.743 [0.724, 0.761] |
| gitleaks (no tests) | 0.955 [0.943, 0.964] | 0.920 [0.906, 0.933] | 0.967 [0.957, 0.975] |
| gitleaks (with tests) | 0.914 [0.900, 0.926] | 0.895 [0.880, 0.909] | **0.945** [0.933, 0.954] |
| syft (no tests) | 0.513 [0.502, 0.524] | 0.508 [0.497, 0.519] | **0.542** [0.531, 0.553] |
| syft (with tests) | 0.322 [0.316, 0.328] | 0.338 [0.332, 0.344] | **0.361** [0.355, 0.367] |
| zod (no tests) | 0.703 [0.677, 0.727] | 0.373 [0.347, 0.401] | 0.694 [0.668, 0.719] |
| hono (no tests) | 0.731 [0.697, 0.762] | 0.684 [0.649, 0.717] | 0.686 [0.650, 0.719] |

**codebase-memory-mcp has the higher recall in every Go cell and separates in
three of them.** We lead no Go cell. The two TypeScript cells are ties.

**Do not compare recall across rows.** It swings from 0.32 to 0.97, driven by how
many entry points the oracle had (4 on gitleaks, 268 on syft-with-tests), not by
tool quality. Only within-row comparisons carry meaning and a pooled recall over
these cells would be meaningless.

**The two tables are one finding.** codebase-memory-mcp recovers more of the true
call graph and emits far more that is not in it: on syft, more than a third of
what it emits is a call the Go compiler says does not exist. That is also why our
own cross-file coverage trails it on 15 of 35 repositories in the same bench.
Coverage rewards drawing edges and never asks whether they are real, which is why
no page here publishes a coverage number without a precision number beside it.

### Limits

- **Two languages, seven cells, five repositories.** This is not a nine-language
  claim and must not be quoted as one. Section 7 is the nine-language number.
- **A contradicted edge is very strong evidence, not proof.** RTA is unsound under
  reflection and `go:linkname`, so a genuinely dynamic edge can land in that
  bucket. It applies to all three arms equally and the gaps are far too large to
  be explained by it, which is why the metric is named *precision against the
  oracle* rather than precision.
- **Edges the oracle cannot speak about are charged to nobody** and reported at
  full size. That bucket is 0.4% to 11.1% of a tool's output on the Go cells and
  16% to 46% on the TypeScript ones, where dependencies are not installed in the
  pinned corpus.
- **A library has no `main`, so RTA has no roots**; cobra is analysed through its
  test binaries only.
- The two variants of a repository answer different questions. Report both or say
  which one you used.
- **Two oracle languages, and the programme stops at two.** C#, Java, Kotlin and
  C++ each need a toolchain installed and a working build per repository, none of
  which exists on the measurement machine. Rust has the toolchain and no sound
  call-graph tool exists for it. Python, Ruby and PHP admit no oracle even in
  principle. So section 7 is the permanent method on those languages rather than
  a stopgap, and nothing here should be read as a claim about them.
- **The TypeScript cells exclude test files, and the with-tests variants are
  void.** A test file imports its own package by name, the pinned corpus has no
  dependencies installed, and roughly a third of call sites go unresolvable,
  which takes the unjudged bucket past three quarters. Both variants were run and
  neither is quoted anywhere. Installing the dependency trees would fix it and
  would also have to go to a scratch copy, since `node_modules/` inside the
  corpus changes what every tool walks.
- **codebase-memory-mcp is priced on these two languages only.** It has no
  precision figure of any kind on the other nine languages where it leads our
  coverage, and it was never entered into the section 7 audit in either
  direction. The finding that its coverage lead is bought with wrong edges is
  measured on Go and TypeScript and carried by inference elsewhere.

Full method, per-cell artifacts, the twenty hand-confirmed identities the protocol
required, and the graded pre-registration including the two predictions that
missed:
**[graph/experiments/g4-oracle-anchored](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g4-oracle-anchored)**.

---

## Limits

Beyond the ones stated in each section:

- **Every retrieval number here is Python or Go.** The graph sections are wider,
  at nine languages hand-graded and two compiler-graded, and they are the only
  sections that are. A JavaScript/TypeScript corpus
  (`mui/material-ui`, six tools, a 12x size range) is built and half graded, but
  **its sealed half is unrun and nothing from it is quoted here.** Publishing the
  development half is what that split exists to prevent, so the row arrives when the
  sealed half is evaluated, once.
- **We index code files only, which depresses our own coverage on repositories with
  documentation.** Deliberate: on doc-heavy repositories the docs outweigh the code,
  and indexing them polluted the index and degraded retrieval for the code questions
  the tool exists to answer. On the JavaScript corpus above, **21% of gold files are
  `.md` or `.json`**, unreachable to us by construction rather than by ranking.
  cocoindex makes the opposite trade, is the only arm of six to retrieve any
  documentation gold, and pays for it on code gold.
- **§2 is one repository**, `django/django` at one commit, which is in every model's
  training data.
- **§2 measures single-session questions**, roughly four to nine turns each. Nothing
  on this page measures a long multi-hour engineering task, and we will not imply a
  number for one.
- **§1 quotes only the sealed half.** Pooling both halves gives a stronger p and we
  do not quote it.
- **§2's difficulty split is post-hoc.** The pre-registered comparisons are stronger
  evidence.
- **§4 has no head-to-head and one row that needs re-measuring.**
- **§5's signal is weak among files of similar size**, and a prior-defects baseline
  still beats us on Popt by 0.085 even while losing on AUC.
- **§7 was graded by us on both sides.** That is what §8 exists to check, and the two
  agree where both exist, but only §8 has an answer key we did not produce.
- **§7 and §8 measure precision, which is one of two halves.** We lead no recall cell
  in §8 and lose cross-file coverage on 15 of 35 repositories in the same bench. A
  precision win is a claim about the edges a tool draws, never about how many.

---

## How to read a number on this page

In July 2026 JetBrains took two popular token-saving tools and reran their headline
claims on real agent work.

| Tool | Advertised saving | What JetBrains measured |
|---|---|---|
| [Caveman](https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/) | 65% | **8.5%** |
| [RTK](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) | 60 to 90% | **7.6% more expensive** at low reasoning effort (p = 0.004) |

Greptile's 82% became 45% under Augment's rerun. The pattern is consistent enough to
be a rule: in this category, the advertised number and the reran number are different
numbers. They differ for one reason.

**Measuring one context load is easy. Measuring an agent loop is hard.** Showing that
your representation of a file is smaller than the file is a real measurement, and the
one almost everybody publishes. It is not the question anyone has, which is whether
an agent given your tool finishes the job having done less work. Agents re-read,
backtrack, re-plan and re-explore, so a compression that looks like 90% on one
payload routinely nets out near zero across a session, and can go negative when the
agent works harder to recover what you compressed away.

**This page publishes both and labels which is which.** §3 is the easy
one-context-load figure. §2 is the agent-loop figure, measured the way JetBrains
measured, and deliberately the smaller number. We reran our own headline on a second
and then a third harness and published what came back: it moved a number we had been
quoting, and §2 carries the correction.

Four rules apply to every number here, and each exists because breaking it produced a
wrong published figure at least once:

- **Pre-register before spending**, as its own commit, so a favourable result cannot
  become a different question afterwards.
- **Seal a half**, split by instance id before any work begins, evaluated once.
- **Precision and files-served beside coverage**, never averaged into one figure.
- **Prove an arm was alive and its extractor works before recording a zero.** A dead
  server, a wrong tool name and a broken output parser all score exactly like a bad
  tool.

The full method, drawn end to end with every gate and the failure that put it there,
is
**[THE\_LOOP.md](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/THE_LOOP.md)**.

---

## Where the depth is

This page is the summary. Stop wherever you like; each level is the evidence for the
one above it.

| Level | What is there |
|---|---|
| **[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head)** | Who wins what, the build-cost curve, and what each index can rank at all |
| **[graph/](https://github.com/repowise-dev/repowise-bench/tree/master/graph)** | The graph-quality bench: edge precision hand-graded on both sides across nine languages against one competitor, precision and recall against a compiler oracle on Go and TypeScript against two, and cross-file coverage, adversarial invariance and build cost across five |
| **[arms/](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head/arms)** | One page per competitor: what it is, what it serves, and every setup trap. Four of six have a step that produces a clean zero when missed |
| **[THE\_LOOP.md](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/THE_LOOP.md)** | The method and all nine gates, each named with the failure that created it |
| **[configs/arms.yaml](https://github.com/repowise-dev/repowise-bench/blob/master/configs/arms.yaml)** | Every launch command, allowlisted tool and exclusion with its reason. Read this if you think an arm was set up unfairly |
| **[configs/\*.PREREGISTRATION.md](https://github.com/repowise-dev/repowise-bench/tree/master/configs)** | One per scored run, each committed before its run spent anything |
| **[results/bakeoff\_2026\_08](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08)** | Every graded cell and every verbatim response, including the runs we invalidated, with their invalidation notes attached |
| **[repro/README.md](https://github.com/repowise-dev/repowise-bench/blob/master/repro/README.md)** | Per claim: what it costs to reproduce, how long it takes, and which ones need credentials we cannot hand you |

**Found a problem with one of these numbers, or want your tool in the field?** Adding
a competitor is a YAML block, no Python and no runner change. See
[CONTRIBUTING.md](https://github.com/repowise-dev/repowise-bench/blob/master/CONTRIBUTING.md).

Tool versions as measured: CodeGraph 1.5.0, Graphify 0.9.31, Serena 1.6.2.dev0,
code-review-graph 2.3.7, cocoindex as of 2026-08-09, codebase-memory-mcp 0.10.8.

## See also

- [The five intelligence layers](layers/INTELLIGENCE_LAYERS.md)
- [Code health methodology](layers/CODE_HEALTH.md)
- [MCP tool reference](agent/MCP_TOOLS.md)
