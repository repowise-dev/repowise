# Benchmarks

Every number repowise publishes, with its sample size, its test, and a link to
the raw data. The harnesses and full reports live in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**, and nothing
here is measured on a private corpus.

## Why this page is built the way it is

In July 2026, JetBrains took two popular token-saving tools and reran their
headline claims on real agent work with Sonnet 5.

| Tool | Advertised saving | What JetBrains measured |
|---|---|---|
| [Caveman](https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/) | 65% | **8.5%** |
| [RTK](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) | 60 to 90% | **7.6% more expensive** at low reasoning effort (p = 0.004), and no change at high effort |

Greptile's 82% became 45% under Augment's rerun. The pattern is consistent
enough to be a rule: in this category, the advertised number and the reran
number are different numbers.

They are different for a reason that is worth understanding before you read
anything below, because it is the single most common way a token claim goes
wrong.

**Measuring one context load is easy. Measuring an agent loop is hard.** You can
show that your representation of a file is smaller than the file. That is a real
measurement and it is the one almost everybody publishes. It is also not the
question anyone actually has, which is whether an agent given your tool finishes
the job having done less work. Agents re-read, backtrack, re-plan and re-explore.
A compression that looks like 90% on one payload routinely nets out near zero
across a real session, and can go negative when the agent has to work harder to
recover what you compressed away.

**This page publishes both numbers and labels which is which.** Section 3 is our
one-context-load figure, the easy one. Section 2 is the agent-loop figure,
measured the way JetBrains measured: real agent, real repository, real tool use,
against a control. Section 2 is the one that matters, and it is deliberately the
smaller number.

That is also why this page prints n beside every mean, states which tool
produced each number, and publishes the rows we lose. It is built to survive a
rerun, because in this field that is the only property that turns out to matter.

## What we measured, and against whom

repowise builds [five intelligence layers](layers/INTELLIGENCE_LAYERS.md) from
one index, so there is no single competitor to measure against. Different tools
overlap on different layers, and this table says exactly which.

| Layer | Measured against | Result |
|---|---|---|
| Finding the right files | CodeGraph, Graphify, code-review-graph | [§1](#1-finding-the-right-files) **we win**, n=42 held out, p=0.00004 |
| Work saved in a real agent loop | CodeGraph, Serena, Graphify, code-review-graph, bare agent | [§2](#2-what-changes-in-a-real-agent-loop) **we win**, n=15, p=0.035, and the only tool in the field to reach significance |
| Loading one commit's context | naive file reads, `git diff` | [§3](#3-loading-one-commits-context-the-easy-number) **35.6x** fewer tokens than naive |
| Command-output compression | no comparable tool in the field | [§4](#4-command-output-compression) |
| Code health and defect prediction | CodeScene | [§5](#5-code-health-predicts-defects) **we win**, p=0.003 |
| Indexing time | CodeGraph, Graphify, code-review-graph | [§6](#6-indexing-time-the-row-we-lose) **we lose**, 22x, because we build four more layers in the same pass |
| Documentation generation | DeepWiki, Google Code Wiki, Swimm | **not measured** |
| PR review | CodeRabbit, Greptile | **not measured** |

The last two rows are capability comparisons, not measurements, and they live in
the [README's feature table](../README.md#how-it-compares-on-capability) where a
reader can tell the difference. We would rather say "not measured" than let a
checkmark do a number's job.

---

## 1. Finding the right files

Before a tool can save an agent any work, it has to point at the right code.
This section measures only that.

**Grading here is deterministic. No LLM judge is involved anywhere in this
number**, which makes it the most reproducible result on the page. ContextBench
ships gold file spans; a tool either returns them or it does not.

The 112 instances were split into a 70-instance development half and a
42-instance **sealed** half, **pinned by instance id before any of this work
started**. All development uses the 70. **Every number in the table below comes
from the sealed 42**, which is the whole reason it is worth reading.

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

### The development half, for comparison

All development work happens on the other 70 instances. Those are not the
headline and never will be, but the numbers are worth printing beside the sealed
ones:

| | development half (n=70) | **sealed half (n=42)** |
|---|---:|---:|
| repowise (`get_answer`) | 0.810 | **0.876** |
| repowise (`search_codebase`) | 0.684 | 0.742 |
| CodeGraph | **0.6093** | **0.6095** |

**CodeGraph scores the same on both halves to three decimal places**, which is
how we know neither half is the easy one. Our own results are slightly stronger
on the sealed half than on the development half.

**We do not quote a pooled 112-instance figure**, though it is easy to compute
and would be 0.835. The two halves answer different questions and averaging them
loses the only one that matters, which is how the tool does on instances no
development work has ever seen.

### What it cost to produce

Every arm builds its **own** index of **every** instance's repository at that
instance's own `base_commit`. Nothing is shared between arms, nothing is cached
across instances, and a stale checkout is a wrong answer rather than a fast one.
Across the rung-8 matrix that is **748 index builds and roughly 78 machine-hours
of indexing for 1,129 graded (instance, arm) cells.**

This is retrieval, not task success. It says we find the right files, not that an
agent using us writes better code. That is what section 2 is for.

Measured on repowise at commit `081a59fa` (between v0.37.0 and v0.38.0).

Raw data and harness:
**[bakeoff\_2026\_08/rung8](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung8)**

---

## 2. What changes in a real agent loop

This is the section modelled on the JetBrains reruns, and the one we would ask a
skeptic to read first.

Fifteen questions on `django/django`, stratified across five question shapes and
drawn before any money was spent. Six arms: repowise, four competing tools, and
a bare agent with no tools at all. Every arm got a byte-identical prompt, its
full advertised tool surface, and a freshly built index on the same pinned
commit. The bare-agent control was verified free of any local hooks, so it is a
real control and not a contaminated one.

Two things have to be true for a tool to be worth mounting. The agent has to
actually call it, and the loop has to get leaner when it does. One table, both
questions.

| Tool | Tools advertised | Schema cost (chars) | Agent used it | Output tokens | vs bare agent | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|---:|
| **repowise** | 10 | 17,561 | **15 / 15** | **2,420** | **-15.9%** | **12 of 15** | **0.035** |
| CodeGraph | 1 | 1,567 | 13 / 15 | 2,540 | -11.7% | 10 of 15 | 0.302 |
| Serena | 29 | 29,050 | 4 / 15 | 2,551 | -11.3% | 8 of 15 | 1.000 |
| code-review-graph | 30 | 28,118 | **0 / 15** | 2,768 | -3.8% | 10 of 15 | 0.302 |
| Graphify | 10 | 5,482 | 3 / 15 | 2,878 | 0.0% | 7 of 15 | 1.000 |
| *bare agent (control)* | 0 | 0 | n/a | 2,877 | baseline | n/a | n/a |

**repowise is the only tool in this field whose reduction is large enough to
rule out chance.**

### How to read that table

**Agent used it** counts the questions where the agent made at least one call the
server actually answered. A tool the agent never reaches for is not a tool it
has, whatever the feature list says. code-review-graph advertises 30 tools over
a built, embedded graph of 40,904 nodes and 380,168 edges, and across 15
questions the agent never called it once.

**Output tokens** is how much the agent itself writes to reach an answer: its
reasoning, its tool calls, its final reply. Lower means it went in a straighter
line. This is the honest measure of work saved, for reasons in the box below.

**Leaner on** is the plain-language version of the statistic: on how many of the
15 questions did this tool beat the bare agent. 12 of 15 is unlikely to be luck,
about a 1-in-28 coincidence, which is what the p column says. 10 of 15 is roughly
what a coin produces.

Alongside the token reduction, the agent also took **8.5 turns instead of 9.7**,
made **7.5 tool calls instead of 8.7** (-13.1%), and opened **1.5 files instead
of 2.1** (-25.8%). Those move together because they are the same effect: work
done once, offline, that the agent would otherwise redo on every query.

### Why this section reports tokens and not dollars

Dollar cost per question is the number every tool in this category wants to
quote, and it is close to meaningless as a measure of a tool. Here is the control
that convinced us, run in our own harness.

**code-review-graph never called its server across all 15 questions.** It is
behaviourally identical to the bare agent, carrying an extra 28,118 characters of
tool schema that should make it cost *more*. Measured on dollars, it came out
**43% cheaper than the bare agent**. A tool that did nothing produced a
best-in-class saving.

The cause is prompt caching. Cached tokens bill at a fraction of fresh ones, so
whichever arm happens to run first pays to warm the cache and every arm after it
reads it cheaply. In our run the correlation between an arm's position in the
cycle and its dollar cost was **-0.487**. That is not a property of any tool. It
is a property of the schedule.

**Output tokens are immune to this.** They are never cached, and their
correlation with run position is **+0.010**, which is nothing. So that is what we
report. It is a smaller and less impressive number than the dollar figure would
have been, and it is the one that survives.

If you see a token-savings claim in this category that does not say whether it
controls for cache state and arm ordering, this is the first thing to ask about.

### Where the saving is largest

The effect is not uniform. **repowise saves more on questions that require
touching more of the codebase.** Splitting the 15 questions at the median by how
much work the bare agent did:

| | n | Bare agent output | Tokens saved | % saved |
|---|---:|---:|---:|---:|
| easier half | 7 | 2,063 | 154 | 8.2% |
| harder half | 8 | 3,590 | **722** | **19.9%** |

The harder half saves **4.7x more tokens per question**. Correlation between
question difficulty and tokens saved is +0.534, and a permutation test on the gap
gives **p = 0.013** over 200,000 shuffles.

Read that precisely, because there are two claims here and only one is
supported. The saving grows in **absolute** terms with the size of the task. It
does not grow as a *percentage*: proportionally the tool helps about as much on a
small lookup as on a large trace. The mechanism is that pre-computed structure
replaces exploration, and harder tasks contain more exploration to replace.

**Two honest limits on this one.** The split at the median was chosen after
seeing the data, which makes it weaker evidence than the pre-registered
comparisons elsewhere on this page. And every question here is answered in a
single session of roughly nine turns. **We have not measured a long multi-hour
task such as designing a feature across many files, and we will not imply a
number for one.** The reasonable expectation is that a saving which scales with
exploration keeps scaling when there is more exploration to do, but that is an
argument from mechanism, not a result, and it stays labelled as one until we run
it.

### What we will not claim from this run

- **Not a quality win, and not quality parity either.** A blind judge scored
  repowise best in the field at +0.13 against the bare agent, with CodeGraph at
  -0.41 and Serena at -0.44. But the judge's two graders disagree with each other
  by 0.46 points on the *same* answers, which is larger than every per-arm effect
  in the run. The instrument cannot resolve differences this small, so the
  quality column is decoration, not evidence. "No significant difference" is not
  parity, and an equivalence claim needs a TOST we have not run.
- **Not a universal saving.** This is n=15 on one repository, at one commit,
  under one prompt and one model. All four move the number. Treat -15.9% as this
  configuration's result, not a constant.
- **Adoption is a design result, not a retrieval result.** It measures whether we
  named and shaped our tools so an agent reaches for them, which is a real skill
  and a real advantage, and it says nothing about the quality of what comes back.
  Note it is clearly **not** ordered by surface size: we serve 10 tools and get
  called 15 of 15, CodeGraph serves 1 and gets called 13, Serena serves 29 and
  gets called 4.

### What producing this table cost

6 tools x 15 questions = **90 agent runs, 0 errors, $18.77** of API spend over
**106 minutes**. Every tool was given a fresh index built from scratch on the
same pinned commit, **11.3 minutes of indexing** in total: repowise 363.6s,
Graphify 102.7s, code-review-graph 54.3s, CodeGraph 15.6s.

Raw data:
**[bakeoff\_2026\_08/rung6](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung6)**

---

## 3. Loading one commit's context, the easy number

This is the measurement almost every tool in this category publishes, and we are
labelling it as such. It is a real measurement. It is not section 2, and it
should not be read as though it were.

How many tokens does it take to load one commit's context? Measured over the 30
most recent non-merge commits of `pallets/flask`, counted with deterministic
`tiktoken` (`cl100k_base`):

| Strategy | Tokens per commit |
|---|---:|
| naive, full contents of every changed file | 13,984 |
| `git diff` only | 1,408 |
| **`get_context`** | **393** |

**35.6x fewer than naive, pooled**, 29.3x as a mean of per-commit ratios, and
3.6x pooled against `git diff`.

**Lead with the pooled figure.** Pooled is sum-of-tokens over sum-of-tokens, so
it weights each commit by the tokens actually at stake. A mean of per-commit
ratios does not: a one-line commit where `get_context` returns 40 tokens
contributes a huge ratio that counts equally against a commit saving a hundred
thousand. That is exactly how a 35x becomes a 209x in a press release, and it is
why the pooled number is the one we print first.

**What this number does not tell you.** It is one payload, not a session. It says
our representation of a commit is smaller than the commit. It does not say an
agent finishes faster, and the honest version of that question is section 2,
where the answer is a much more modest 15.9%.

Raw CSV committed alongside the harness:
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
close. **The reason is not an optimisation we forgot: the tools we are measured
against build a call graph, and in the same pass we also mine git history,
generate and embed documentation, extract decision records and score code
health.** On `django/django`:

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
- **§2 is one repository**, `django/django` at one commit, which is in every
  model's training data.
- **§2 measures single-session questions**, roughly nine turns each. Nothing on
  this page measures a long multi-hour engineering task.
- **§1's development half is not a headline.** Pooling the development and sealed
  halves gives a stronger p, and we do not quote it. The development numbers
  appear in §1 for comparison only, never as the result.
- **§2's difficulty split is post-hoc.** The median split was chosen after seeing
  the data. The pre-registered comparisons on this page are stronger evidence.

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
