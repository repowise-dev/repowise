# Benchmarks

Every number repowise publishes, with its sample size, its test, and a link to
the raw data. The harnesses and full reports live in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**, and nothing
here is measured on a private corpus.

Three headline claims in this category collapsed when someone else reran them:
RTK's 60-90% became 7.6% *worse*, Caveman's 65% became 8.5% under JetBrains,
Greptile's 82% became 45% under Augment's rerun. That is the reason this page
prints n beside every mean, states which tool produced each number, and publishes
the rows we lose. It is built to survive a rerun, because in this field that is
the only property that turns out to matter.

## What we measured, and against whom

repowise builds [five intelligence layers](layers/INTELLIGENCE_LAYERS.md) from
one index, so there is no single competitor to measure against. Different tools
overlap on different layers, and this table says exactly which.

| Layer | Measured against | Result |
|---|---|---|
| Finding the right files | CodeGraph, Graphify, code-review-graph | [§1](#1-finding-the-right-files) **we win**, n=42 held out, p=0.00004 |
| Cost in a real agent loop | CodeGraph, Serena, Graphify, code-review-graph, bare agent | [§2](#2-cost-in-a-real-agent-loop) **we win**, n=15, p=0.007 |
| Whether agents call the tools at all | same five | [§3](#3-whether-agents-call-the-tools-at-all) **we win**, 15/15 |
| Command-output compression | no comparable tool in the field | [§4](#4-command-output-compression) |
| Code health and defect prediction | CodeScene | [§5](#5-code-health-predicts-defects) **we win**, p=0.003 |
| Indexing time | CodeGraph, Graphify, code-review-graph | [§6](#6-indexing-time-the-row-we-lose) **we lose**, 22x |
| Documentation generation | DeepWiki, Google Code Wiki, Swimm | **not measured** |
| PR review | CodeRabbit, Greptile | **not measured** |

The last two rows are capability comparisons, not measurements, and they live in
the [README's feature table](../README.md#how-it-compares-on-capability) where a
reader can tell the difference. We would rather say "not measured" than let a
checkmark do a number's job.

---

## 1. Finding the right files

**Grading here is deterministic. No LLM judge is involved anywhere in this
number**, which makes it the most reproducible result on the page. ContextBench
ships gold file spans; a tool either returns them or it does not.

The 112 instances were split into a 70-instance development half and a
42-instance **sealed** half, **pinned by instance id before any tuning work
started**. All improvement work uses the development half. **Every number in the
table below is from the sealed half**, which is the whole reason it is worth
reading.

### The numbers, on the 42 sealed instances

| Tool | File coverage | n | Precision | Files served |
|---|---:|---:|---:|---:|
| **repowise** (`get_answer`) | **0.876** | 42 | 0.087 | 19.2 |
| **repowise** (`search_codebase`) | **0.742** | 42 | **0.168** | **8.2** |
| CodeGraph | 0.610 | 42 | 0.093 | 14.0 |
| Graphify | 0.546 | 42 | 0.033 | 34.5 |
| code-review-graph | 0.445 | 42 | 0.240 | 5.4 |

Head to head per instance against CodeGraph: `get_answer` **19 wins, 1 loss, 22
ties, sign test p = 0.00004**. `search_codebase` **13 wins, 3 losses, 26 ties,
p = 0.021**.

**These are two different tools with two different profiles, and we would rather
say so than average them into one claim.** `get_answer` finds the most, from a
list of ~19 files. `search_codebase` finds fewer but is the most efficient per
file served: **0.742 from 8.2 files**, better coverage-per-file than anything
else in the table. If you are paying by the token, that is the row to read.

### Why this changed, and the honest disclosure that goes with it

The `get_answer` row was **0.597** in the previous version of this page, an
honest tie with CodeGraph, and we published it as a loss. Two fixes then landed
([#1284](https://github.com/repowise-dev/repowise/pull/1284),
[#1289](https://github.com/repowise-dev/repowise/pull/1289)): four early-return
paths were discarding the ranked candidate pool they had already computed. The
diagnosis and both fixes were developed **entirely on the development half.**

**So the sealed half was evaluated twice: once at first publication, and once
after those two fixes shipped.** It was not re-run to chase a number, no change
was made in response to what it said, and this sentence exists because "evaluated
once" was the previous claim on this page and it is no longer true.

### The check that matters more than the headline

If we had tuned against the benchmark, the development half would flatter us and
the sealed half would not. It does not happen:

| | development half (n=70) | **sealed half (n=42)** |
|---|---:|---:|
| `get_answer`, before | 0.513 | 0.597 |
| `get_answer`, after | 0.810 | **0.876** |
| **improvement** | **+0.297** | **+0.273** |
| instances regressed | 1 | **0** |

The two halves agree to within 0.024, and **the held-out half improved slightly
less than the tuned one, not more**, which is the direction you would want.

Two further controls, both of which we ran because they could have embarrassed
us. **CodeGraph scores 0.6093 on the development half and 0.6095 on the sealed
half**, so the two halves are equally hard and neither is a soft set. And
`search_codebase`, which neither fix touches, moved **0.000 across all 70**
development instances and 0.746 to 0.742 on the sealed half. An untouched arm
that stayed still is what says the change came from the fixes and not from drift
in the index, the embedder or the grader.

**We do not quote a pooled 112-instance figure.** It would be 0.835, which is
both lower than the sealed result and less meaningful, because it averages the
set the work was built on into the set it was not.

### What it cost to produce

Every arm builds its **own** index of **every** instance's repository at that
instance's own `base_commit`. Nothing is shared between arms, nothing is cached
across instances, and a stale checkout is a wrong answer rather than a fast one.
Across the rung-8 matrix that is **748 index builds and roughly 78 machine-hours
of indexing for 1,129 graded (instance, arm) cells.** Grading is deterministic
and no LLM judge is involved anywhere in this section.

This is retrieval, not task success. It says we find the right files, not that an
agent using us writes better code.

Measured on repowise at commit `081a59fa` (between v0.37.0 and v0.38.0).

Raw data and harness:
**[bakeoff\_2026\_08/rung8](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung8)**

---

## 2. Cost in a real agent loop

Fifteen questions on `django/django`, stratified across five question shapes,
drawn and pre-registered before any money was spent. Every arm got a
byte-identical prompt and its full advertised tool surface, and the bare-agent
control was verified free of the operator's own hooks.

| Arm | Cost per question | vs bare agent | Cheaper on | n |
|---|---:|---:|---:|---:|
| **repowise** | **$0.2068** | **-33.5%** | **13 of 15** | 15 |
| CodeGraph | | -27.0% | | 15 |

**p = 0.007**, sign test, two-sided. This is the cost claim, and it is the only
significant result in the run.

Two things we will not claim from it:

- **Not quality parity.** repowise scored +0.13 on the judge and CodeGraph
  -0.48, but the judge's two graders disagree by 0.46 points on the *same*
  answers, which is larger than every per-arm effect in the run. The quality
  column is inside the instrument's noise, and "no significant difference" is not
  parity. An equivalence claim needs a TOST that we have not run.
- **Not a universal saving.** This is n=15 on one repository, at one commit,
  under one prompt and one model. Cost deltas move with all four, and other runs
  in [repowise-bench](https://github.com/repowise-dev/repowise-bench) land
  elsewhere. Treat -33.5% as this configuration's result, not a constant.

### What the agent stops doing

The mechanism behind the saving is context substitution: work done once, offline,
that the agent would otherwise redo on every query. Measured over paired SWE-QA
runs on `pallets/flask` and `scikit-learn`, same model and same harness, with and
without repowise's MCP tools:

| Measure | Result |
|---|---|
| Tokens to load context | up to **-96%** |
| File reads | **-69% to -89%** |
| Tool calls | **-49% to -70%** |

Loading one commit's context through `get_context` costs **2,391 tokens against
64,039** read raw, roughly 27x fewer, and over a long investigation the effect
compounds to **-41% of the context re-read across the session**.

These are token and call counts, and they are not the same as dollars: agent-side
prompt caching mutes the cost delta on repeated context even where token counts
drop sharply. We report what these runs establish, which is the exploration the
agent no longer performs.

Reports:
**[flask48](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_FLASK48.md)** ·
**[flask v3](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_FLASK_V3.md)** ·
**[sklearn48](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_SKLEARN48.md)**

Raw data for the stratified run:
**[bakeoff\_2026\_08/rung6](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung6)**

---

## 3. Whether agents call the tools at all

Same 15 questions, same agent, same neutral prompt, every server verified alive
and serving its full advertised surface. This counts the cells where the agent
issued at least one call the server answered.

| Tool | Tools advertised | Schema cost (chars) | Cells adopted |
|---|---:|---:|---:|
| **repowise** | 10 | 17,561 | **15 / 15** |
| CodeGraph | 1 | 1,567 | 13 / 15 |
| Serena | 29 | 29,050 | 4 / 15 |
| Graphify | 10 | 5,482 | 3 / 15 |
| code-review-graph | 30 | 28,118 | **0 / 15** |

Tool counts are the surface each server advertises. Schema cost is measured on
the exact build used in the run.

code-review-graph advertises 30 tools over a built, embedded graph of 40,904
nodes and 380,168 edges, and the agent never called it once. A capability an
agent does not reach for is not a capability.

**Our own caveat, because it belongs to us to say:** this is an advantage of
naming and surface design, not of retrieval quality, and adoption is clearly
**not** ordered by surface size. We serve 10 tools and get called 15/15;
CodeGraph serves 1 and gets called 13/15; Serena serves 29 and gets called 4/15.
Designing tools an agent picks up is a real skill and this table measures it, but
it measures nothing about what comes back.

---

## 4. Command-output compression

`repowise distill <cmd>` compresses command output *before* the agent reads it:
errors first, exit code preserved, every omission recoverable through an inline
`[repowise#<ref>]` marker.

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%**, all 11 `FAILED` lines kept |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits) | 62,833 | 8,635 | **86%** |
| `git log --oneline -30` | 321 | 321 | 0%, already compact |
| `git status` (clean) | 83 | 83 | 0%, too small to distill |

The two 0% rows are the net-positive guard working: distill never inflates small
output. One run per command on one repository, so these are point measurements,
not a distribution. Reduction is also not comprehension: the bytes removed are
measured, and the evidence they were safe to remove is narrower, being preserved
failure lines plus CI-asserted zero-error-line-loss fixtures.

Full guide: **[docs/agent/DISTILL.md](agent/DISTILL.md)**

---

## 5. Code health predicts defects

A health score is worth something only if the files it flags are the files that
break. Scores are taken at a historical commit, bug fixes are counted over the
following six months, and nothing after the scoring commit feeds the score.

Across **21 repositories, 9 languages, 2,826 files**: **ROC AUC 0.74**
(95% CI 0.68 to 0.79), reaching 0.90 on individual repos. It survives controlling
for file size, so it is not simply flagging the big files, and it beats recent
churn by +0.10 AUC and prior-defect history by +0.12 (DeLong p < 1e-9). On
PROMISE/jEdit, a dataset it never saw, it holds at 0.76 to 0.78.

**Against CodeScene**, the closest commercial product and the only other vendor
in this category with a published empirical defect study. Both tools scored the
same 2,770 files at the same leakage-free commit against the same labels:

| Paired test | repowise | CodeScene | |
|---|---:|---:|---|
| Recall at a 20%-of-lines review budget | **0.173** | 0.074 | p = 0.003 |
| Effort-aware ranking (Popt) | **0.607** | 0.462 | p = 0.003 |
| Defect density, size-normalized (Alert:Healthy) | **2.18x** | 0.56x | p = 0.003 |
| Discrimination (ROC AUC) | 0.731 | 0.705 | p = 0.054, marginal |
| Precision at a 20%-of-lines review budget | 0.580 | **0.636** | p = 0.64, a tie |

Ranking by repowise health surfaces **2.3x the defects under a fixed review
budget**, at Popt +0.144 and recall +0.098, both p = 0.003, paired.

**Where CodeScene is ahead, and why it is a real choice.** Its nominal precision
lead is not statistically significant, but the behaviour behind it is worth
having: CodeScene flags about **27 files** where we flag **132**. That is a more
conservative threshold, trading recall for a short list a team will actually work
through. If what you want is a handful of files to fix this quarter rather than
the ranking that catches the most defects, that operating point is the better
one, and it is a deliberate design choice rather than a weaker model. Our AUC
edge is also **marginal**, not significant at 0.05, and we would rather say so
than round it up.

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

That is **22x** CodeGraph like for like, and **135x** with prose on, which is what
a default `repowise init` actually costs you. Both numbers ship, and the 22x is
not the user-facing one.

**Here is what the extra time buys.** The tools above build a call graph. In the
same run, repowise builds five layers over the same codebase:

| Layer | On django, in that run |
|---|---|
| **Graph** | 36,485 nodes, 90,477 edges, 31,384 symbols, plus PageRank, betweenness, Leiden communities and execution-flow tracing |
| **Git** | full history mined across 2,630 files: hotspots, ownership, co-change pairs, bus factor |
| **Documentation** | 3,392 wiki pages rendered and embedded for natural-language search |
| **Decisions** | architectural decision records mined from history and sessions |
| **Code health** | 5,317 findings, plus 155 unreachable files and 98 unused exports |

So "22x slower" and "the index contains categorically more" are both true, and
neither one cancels the other. If all you want is a call graph, CodeGraph builds
one in 16 seconds and you should use it. The comparison that would be dishonest
is quoting the ratio without the column beside it, which is why the column is
here.

It is also a one-time cost. Updates after the first index are incremental.

---

## Limits

Beyond the ones stated in each section:

- **Python and Go only.** No TypeScript or JavaScript row appears anywhere on
  this page. That was a scope choice made for instance density in the benchmark
  corpus, not a statement about language support.
- **§2 and §3 are one repository**, `django/django` at one commit, which is in
  every model's training data.
- **§1's development half is not a headline.** Pooling the development and sealed
  halves gives a stronger p, and we do not quote it, because the development half
  is the set the work was built against. The development numbers appear in §1
  only as the overfitting check, never as the result.
- **§1's sealed half has now been evaluated twice**, once at first publication and
  once after #1284 and #1289 shipped. Stated in §1 rather than left for a reader
  to discover. A third evaluation would need a reason better than a number we
  did not like.

## Method and provenance

The full methodology, the pre-registration files with their commit timestamps,
the arm-parity rules, the statistical tests, and the list of measurement traps
that produced wrong numbers before we caught them all live in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**. That
repository also holds every raw run, kept permanently, including the invalidated
ones with their invalidation notes attached.

Tool versions as measured: CodeGraph 1.5.0, Graphify 0.9.31, Serena 1.6.2.dev0,
code-review-graph 2.3.7.

## See also

- [The five intelligence layers](layers/INTELLIGENCE_LAYERS.md)
- [Code health methodology](layers/CODE_HEALTH.md)
- [MCP tool reference](agent/MCP_TOOLS.md)
