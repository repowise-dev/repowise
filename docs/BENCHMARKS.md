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

So we reran our own headline ourselves, on a second agent harness, and published
what came back. It moved a number we had been quoting, and section 2 now
carries both results and the correction.

## What we measured, and against whom

repowise builds [five intelligence layers](layers/INTELLIGENCE_LAYERS.md) from
one index, so there is no single competitor to measure against. Different tools
overlap on different layers, and this table says exactly which.

| Layer | Measured against | Result |
|---|---|---|
| Finding the right files | CodeGraph, Graphify, code-review-graph | [§1](#1-finding-the-right-files) **we win**, n=42 held out, p=0.00004 |
| Work saved in a real agent loop | CodeGraph, Serena, Graphify, code-review-graph, bare agent | [§2](#2-what-changes-in-a-real-agent-loop) **we win**, n=43 on Codex at p&lt;0.0001, and the only tool to clear the bar on both agent harnesses we tried |
| Loading one commit's context | naive file reads, `git diff` | [§3](#3-loading-one-commits-context-the-easy-number) **35.6x** fewer tokens than naive |
| Command-output compression | RTK | [§4](#4-command-output-compression) **not measured head to head** |
| Code health and defect prediction | CodeScene | [§5](#5-code-health-predicts-defects) **we win**, p=0.003 |
| Indexing time | CodeGraph, Graphify, code-review-graph | [§6](#6-indexing-time-the-row-we-lose) **we lose**, 22x, because we build four more layers in the same pass |
| Documentation generation | DeepWiki, Google Code Wiki, Swimm | **not measured** |
| PR review | CodeRabbit, Greptile | **not measured** |

The last two rows are capability comparisons, not measurements, and they live in
the [README's feature table](../README.md#how-it-compares-on-capability) where a
reader can tell the difference. We would rather say "not measured" than let a
checkmark do a number's job. **§4 carries the same label for the same reason**:
RTK does exactly what `repowise distill` does, and we have not run the two
against each other.

---

## 1. Finding the right files

Before a tool can save an agent any work, it has to point at the right code.
This section measures only that.

**Grading here is deterministic. No LLM judge is involved anywhere in this
number**, which makes it the most reproducible result on the page. ContextBench
ships gold file spans; a tool either returns them or it does not.

**Every number below comes from instances this work has never seen.** The 112
instances were split 70 / 42 by instance id, **pinned before any of it started**,
and the 42 were kept sealed until the final measurement.

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

### How this number moved, and why it is not benchmark tuning

We first ran this and came **last, at 0.228**. We published that. The cause
turned out to be a bug: a query-time gate was discarding most candidates before
ranking ever happened. Fixing that path is what moved the number, and it is a
fix any user of the tool gets, not a change shaped around these questions.

The check on that claim is the split, and it points the right way:

| | other half (n=70) | **sealed half (n=42)** |
|---|---:|---:|
| repowise (`get_answer`) | 0.810 | **0.876** |
| repowise (`search_codebase`) | 0.684 | 0.742 |
| CodeGraph | **0.6093** | **0.6095** |

**Overfitting makes the unseen half score worse. Ours scores better**, on both
tools. And CodeGraph, which nobody tuned against either half, scores the same on
both to three decimal places, so the two halves are equally hard and the gap is
about the tool rather than the questions.

**We do not quote a pooled 112-instance figure**, though it is easy to compute
and would be 0.835. Averaging the halves loses the only number that matters,
which is how the tool does on instances it has never seen.

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

Every question in `django/django`'s question set, 48 of them, spanning five
question shapes. Six arms: repowise, four competing tools, and a bare agent with
no tools at all. Every arm got a byte-identical prompt, its full advertised tool
surface, and a freshly built index on the same pinned commit. The bare-agent
control was verified free of any local hooks, so it is a real control and not a
contaminated one.

Two things have to be true for a tool to be worth mounting. The agent has to
actually call it, and the loop has to get leaner when it does. One table, both
questions.

We ran this on two agent harnesses, because the answer turned out to depend on
the harness as much as on the tools. The main result is on Codex, where every
tool in the field actually gets used, so the comparison is between the tools
rather than between agents that ignored them. Claude Code follows as a second
proof point.

### The main run: 48 questions on Codex (`gpt-5.6-sol`)

Every tool called on every question, so this is a like-for-like comparison.

| Tool | Agent used it | Output tokens | vs bare agent | Tool calls | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|
| **repowise** | **44 / 44** | **1,250** | **-31.6%** | **3.8** | **37 of 44** | **<0.0001** |
| CodeGraph | 44 / 44 | 1,383 | **-24.4%** | 4.0 | 37 of 44 | **<0.0001** |
| Serena | 43 / 43 | 1,550 | -14.8% | 10.1 | 35 of 43 | <0.0001 |
| Graphify | 43 / 43 | 1,658 | -8.9% | 7.4 | 31 of 43 | 0.003 |
| code-review-graph | 43 / 43 | 1,710 | -6.0% | 7.2 | 26 of 43 | 0.046 |
| *bare agent (control)* | 0 / 44 | 1,828 | baseline | 7.2 | n/a | n/a |

**repowise leaves the agent with the least work to do, and gets there in the
fewest steps.** A third less output than working with no tool at all, reached in
**3.8 tool calls against the bare agent's 7.2**. One answered question replacing
roughly six greps, visible directly in the call counts rather than inferred.

Correcting for testing five tools at once, three reductions are solid and two
are marginal. **CodeGraph is a genuine second at -24.4%**, and the honest reading
is that we lead a field in which more than one tool works, not that we are the
only one that does.

Serena is the interesting counter-case: it writes less than the bare agent while
calling tools **42% more often**. Busier, not leaner.

5 of the 48 questions are missing from every arm equally, because the run hit an
API usage cap near the end. Paired comparisons are unaffected and the figures
above are over the 43 questions all six arms completed.

### The second proof point: 15 questions on Claude Code (`claude-sonnet-5`)

| Tool | Tools advertised | Schema cost (chars) | Agent used it | Output tokens | vs bare agent | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|---:|
| **repowise** | 10 | 17,561 | **15 / 15** | **2,420** | **-15.9%** | **12 of 15** | **0.035** |
| CodeGraph | 1 | 1,567 | 13 / 15 | 2,540 | -11.7% | 10 of 15 | 0.302 |
| Serena | 29 | 29,050 | 4 / 15 | 2,551 | -11.3% | 8 of 15 | 1.000 |
| code-review-graph | 30 | 28,118 | **0 / 15** | 2,768 | -3.8% | 10 of 15 | 0.302 |
| Graphify | 10 | 5,482 | 3 / 15 | 2,878 | 0.0% | 7 of 15 | 1.000 |
| *bare agent (control)* | 0 | 0 | n/a | 2,877 | baseline | n/a | n/a |

**repowise is the only tool that clears the bar on both harnesses**, and it is
the same direction and the same mechanism in each.

The "agent used it" column is doing something different here, and it is the most
interesting number on this page. Under Claude Code most of these tools were
barely called at all: code-review-graph never once, Graphify three times in
fifteen. Nothing was different about the servers, the questions or the indexes
between the two runs. Claude Code loads MCP tool schemas on demand, so the agent
has to go looking before it can call anything, and frequently never does.

**Treat that column as unstable, including our own 15 of 15.** Rerunning the
same setup on later days returned 4 of 15 and then 3 of 15 for us, and 2 of 14
for CodeGraph. It is a property of the pairing of tool and harness on a given
day, not of the tool.

### Is that adoption collapse the model or the harness? Inconclusive.

The same 15 questions on Opus, everything else held fixed, against bands set
before the run: **12 or more means the model**, 6 or fewer means **the harness's
schema deferral**, 7 to 11 is inconclusive.

**It came back at 7.** Published as inconclusive.

The mechanism is more useful than the verdict. Opus goes looking on **11 of 15**
against Sonnet's 13 of 30, then declines about a third of the times it looks. So
schema deferral is part of the story and not all of it. Ordering across the three
instruments is Sonnet 3 to 4, Opus 7, Codex 15.

**That run's token column also fails its own control**, at -9.3% when the tool
was called against -10.7% when it was not, so no token figure is quoted from it
for any tool including ours. Same class of artifact as the 43%-cheaper dollar
control above, from position and variance at n=15 rather than caching. The Codex
run is unaffected: different harness, three times the `n`, every tool called on
every question.

### How to read those tables

**Agent used it** counts the questions where the agent made at least one call the
server actually answered. A tool the agent never reaches for is not a tool it
has, whatever the feature list says. code-review-graph advertises 30 tools over
a built, embedded graph of 40,904 nodes and 380,168 edges, and under Claude Code
the agent never called it once across 15 questions. Under Codex it called it on
all 15. **That number is a fact about the pairing of tool and harness, not about
the tool**, which is why it appears twice above and never as a single figure.

**Output tokens** is how much the agent itself writes to reach an answer: its
reasoning, its tool calls, its final reply. Lower means it went in a straighter
line. This is the honest measure of work saved, for reasons in the box below.

**Leaner on** is the plain-language version of the statistic: on how many
questions did this tool beat the bare agent, head to head. 37 of 44 is not
something a coin does. Roughly half is.

**Tool calls** is how many separate actions the agent took. It is the clearest
view of the mechanism: on Codex the repowise agent finished in 3.8 steps where
the bare agent needed 7.2, and it opened 3.0 files instead of 7.2. Those move
together because they are the same effect, which is work done once, offline,
that the agent would otherwise redo on every query.

### Why this section reports tokens and not dollars

Dollar cost per question is the number every tool in this category wants to
quote, and it is close to meaningless as a measure of a tool. Here is the control
that convinced us, run in our own harness.

**Under Claude Code, code-review-graph never called its server across all 15
questions.** It is behaviourally identical to the bare agent, carrying an extra
28,118 characters of tool schema that should make it cost *more*. Measured on
dollars, it came out
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
touching more of the codebase.** Splitting the 48-question Codex run at the
median, by how much work the bare agent needed:

| | n | Bare agent output | Tokens saved | % saved |
|---|---:|---:|---:|---:|
| easier half | 22 | 1,377 | 374 | 27.2% |
| harder half | 22 | 2,279 | **781** | **34.3%** |

The harder half saves **more than twice as many tokens per question**, and the
correlation between how much work a question demands and how much we save is
**+0.379**. The same pattern appeared on the smaller Claude Code run, so it has
now shown up twice.

The mechanism is that pre-computed structure replaces exploration, and harder
questions contain more exploration to replace.

**One honest limit.** Every question here is answered in a single session of
roughly four to seven turns. **We have not measured a long multi-hour task such
as designing a feature across many files, and we will not imply a number for
one.** The reasonable expectation is that a saving which scales with exploration
keeps scaling when there is more exploration to do, but that is an argument from
mechanism rather than a result, and it stays labelled as one until we run it.

### What we will not claim from this run

- **This is a work-saved result, not a quality result.** A blind judge scored
  every tool in the field, ours included, a fraction **below** the bare agent on
  the 48-question run, in a range of 0.04 to 0.25 points on a 10-point scale.
  None of those differences is statistically distinguishable from zero, and all
  of them are smaller than the 0.69 points by which this benchmark moves when we
  rerun it unchanged. So the correct reading is that no tool here measurably
  changed answer quality in either direction. Ours is at the low end of that
  band, we are watching it, and we will say so if it turns into a real effect.
  "No significant difference" is not the same as parity, and an equivalence
  claim needs a test we have not run.
- **The two quality columns cannot be compared with each other.** They were
  graded by different judges, because grading a model with a judge from its own
  family is a known bias and avoiding it means the judge changes when the agent
  does. So no quality number in one table may be subtracted from one in the other.
- **Not a universal saving.** One repository, one commit, one prompt. Two
  harnesses, which is two more than most published numbers in this category, and
  still not a constant. Treat each figure as that configuration's result.
- **Adoption is not a stable property of a tool.** We used to read the "agent
  used it" column as a design result about how well we had named and shaped our
  tools. It does not support that. Repeating the Claude Code run with nothing
  changed on anyone's side moved us from 15 of 15 to 4 of 15 to 3 of 15, and
  CodeGraph from 13 of 15 to 2 of 14. Switching harness moved every tool in the
  field to called-on-every-question. **Whether an agent calls a codebase server
  at all depends more on the harness than on the server.** Any adoption figure, ours included,
  is only meaningful with its harness and its date attached.

### What producing these tables cost

**471 agent runs, about 13 hours of machine time, roughly $44 of API spend.**

| | runs | wall clock | API spend |
|---|---:|---:|---:|
| Codex, 48 questions x 6 tools | 261 | 4.9h | $17.62 |
| Codex, earlier 15-question run | 90 | 1.7h | $6.08 |
| Codex, proof-of-life checks and a second language | 30 | 0.5h | $1.58 |
| Claude Code with Sonnet, 15 questions x 6 tools | 90 | 1.8h | $18.77 |
| Claude Code with Opus, the promised rerun | 90 | 2.1h | $28.57 |

The two harnesses' dollar figures are not comparable and are given only as what
each cost to produce: Claude Code reports its own spend, while Codex reports
token counts only, so its figure is computed from published list rates.

**Before any of that, every tool got a fresh index built from scratch** on the
same pinned commit, **11.3 minutes in total**: repowise 363.6s, Graphify 102.7s,
code-review-graph 54.3s, CodeGraph 15.6s. Serena indexes on demand. We are the
slowest of the four and [say so in §6](#6-indexing-time-the-row-we-lose). Those
indexes were then reused by every later run, so the second harness cost agent
time only.

Two things are not in that table and are most of the real effort. **The
competitor setups**:
[code-review-graph](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/arms/code-review-graph.md)
alone needs three steps that are not in its README, each of which produces a
clean, plausible zero when missed, and getting
[Serena](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/arms/serena.md)
to answer anything at all needs an explicit project activation. A tool scoring
zero because we set it up wrong is not a result, and finding that out is most of
what this work is. The setup each tool needs is written up per tool.

**And the checks that come before any number is allowed to count.** Every arm is
probed before each run to confirm its server actually answers, and a control
that checks it can also correctly report a tool as unused, so a broken detector
cannot quietly pass everything. Roughly a third of the total runs above are
those checks and repeats rather than headline numbers.

Raw data, every cell including the failures:
**[rung9](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung9)**
(the 48-question Codex run) and
**[rung6](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung6)**
(the 15-question runs and the proof-of-life checks).

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

**There is a comparable tool and we have not run it.**
[RTK](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) does
this and essentially only this. It is also the tool in the table at the top of
this page whose advertised 60 to 90% came back **7.6% more expensive** under
JetBrains' rerun. So the table above is a before-and-after of our own output, not
a head to head, and a before-and-after is the weaker design. Only a paired run
against RTK would settle it.

**One row above is inflated, by the mechanism that broke RTK's number.** A saving
may only count output the agent would actually have received, and a host
truncates a command result at 30,000 characters. The engine applies that cap;
`git diff`'s 62,833 raw tokens is eight times it and predates it. **Treat that
86% as unsupported until re-measured.** `pytest` and `git log` sit under the cap
and are unaffected.

Full guide: **[docs/agent/DISTILL.md](agent/DISTILL.md)**

---

## 5. Code health predicts defects

A health score is worth something only if the files it flags are the files that
break. Scores are taken at a historical commit, bug fixes are counted over the
following six months, and nothing after the scoring commit feeds the score.

Across **21 repositories, 9 languages, 2,826 files**: **ROC AUC 0.737**
(95% CI 0.683 to 0.787), ranging from 0.55 to 0.86 across individual repos. It
beats recent churn by +0.100 AUC and prior-defect history by +0.117 (DeLong
p < 1e-9). On PROMISE/jEdit, a dataset it never saw and which carries no git
history at all, it holds at 0.76 to 0.78.

It is **not** better than raw file size at discrimination: LOC-only scores 0.742
against our 0.737 (p = 0.92, a tie). Where it wins is effort-aware ranking,
Popt +0.134 (95% CI +0.080 to +0.198) — same discrimination as counting lines,
much better at ordering a fixed review budget, and unlike a line count it says
why. Holding size fixed, within-band AUC runs 0.525 / 0.572 / 0.593 / 0.718
across NLOC quartiles: the signal survives cleanly only in the largest quartile,
and a purpose-built positive control confirms that collapse is a real absence
rather than too few positives to detect one.

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
than round it up. So is our raw defect-density lead: 16.9x against 14.2x, but
p = 0.65 on a heavy tail. The size-normalized version of that row, 2.18x against
0.56x, is the one that reaches significance and the one to cite.

**The axis we lost outright.** CodeScene's "Code Red" study reports a Pearson
correlation of **−0.58** between Code Health and mean issue-resolution time, on
proprietary Jira cycle-time data. We tried to replicate that on open data and
could not. Across 17 repositories and 271 files, GitHub PR merge time correlated
with health at **−0.09** (95% CI −0.19 to +0.14), and six queue-independent
effort signals — commit span, commit count, review rounds, changes-requested,
commits after first review, review-comment density — all came back flat with
every interval spanning zero. The likely reason is structural: GitHub merge time
largely measures maintainer review-queue availability, not how hard a change was.
An early three-repo slice did show a review-rounds correlation of about −0.29,
and it did not survive expanding the corpus, so we treat it as small-sample
noise. **The business-impact axis remains CodeScene's, unreplicated on open
data.**

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

- **Every number here is Python or Go.** A JavaScript/TypeScript corpus
  (`mui/material-ui`, six tools, a 12x size range) is built and half graded, but
  **its sealed half is unrun and nothing from it is quoted here.** Publishing the
  development half is what that split exists to prevent, so the row arrives when the sealed
  half is evaluated, once.
- **We index code files only, which depresses our own coverage on repositories
  with documentation.** Deliberate: on doc-heavy repositories the docs outweigh
  the code, and indexing them polluted the index and degraded retrieval for the
  code questions the tool exists to answer. The cost is invisible in a coverage
  figure. On the JavaScript corpus above, **21% of gold files are `.md` or
  `.json`**, unreachable to us by construction rather than by ranking. A tool
  making the opposite trade retrieves some of them and pays for it on code.
- **§2 is one repository**, `django/django` at one commit, which is in every
  model's training data.
- **§2 measures single-session questions**, roughly nine turns each. Nothing on
  this page measures a long multi-hour engineering task.
- **§1 quotes only the sealed half.** Pooling both halves gives a stronger p and
  we do not quote it. The other half appears in §1 as a check on the sealed
  number, never as the result.
- **§2's difficulty split is post-hoc.** The median split was chosen after seeing
  the data. The pre-registered comparisons on this page are stronger evidence.
- **§4 has no head-to-head and one row that needs re-measuring.** See that
  section.

## Method and provenance

The method, drawn end to end with every gate and the failure that put it there,
is **[THE\_LOOP.md](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/THE_LOOP.md)**.
One page per tool in the field, with its setup traps, is
**[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head)**.
The pre-registration files with their commit timestamps, the arm-parity rules and
the statistical tests are in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**, which also
holds every raw run permanently, including the invalidated ones with their
invalidation notes attached.

Tool versions as measured: CodeGraph 1.5.0, Graphify 0.9.31, Serena 1.6.2.dev0,
code-review-graph 2.3.7.

## See also

- [The five intelligence layers](layers/INTELLIGENCE_LAYERS.md)
- [Code health methodology](layers/CODE_HEALTH.md)
- [MCP tool reference](agent/MCP_TOOLS.md)
