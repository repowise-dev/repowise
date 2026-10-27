# Benchmarks

Repowise reads a codebase once, on your machine, and answers questions about it
for developers and their AI coding agents. This page shows how well it does
that, measured on public code against answer keys we did not write. Every
sample is published in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**, so anyone
can rerun it.

## The results in one minute

| | Result | What it means |
|---|---|---|
| **Half the work for your agent** | **3.8 tool calls instead of 7.2**, **3.0 files opened instead of 7.2**, **31.6% less** output | An AI agent with repowise reaches the same answer with about half the searching and reading. That is the largest saving of the five context tools we tested. [Details](#2-what-changes-in-a-real-agent-loop) |
| **Points at the right code** | **88%** of the files a real fix needed, against **61%** for the next tool | Before an agent edits, it has to find the right files. Repowise finds most of them in one call. [Details](#1-finding-the-right-files) |
| **Up to 99% less noise** | `npm ci` **99.5%**, `git log` **89%**, `git diff` **86%**, test runs **61%** smaller | Command output is trimmed before the agent reads it, and every error line is kept. [Details](#4-command-output-compression) |
| **Finds the code that will break** | **2.3x** as many later-buggy files as CodeScene, in the same review budget | Its health score flags the files that go on to need bug fixes, across 21 open-source projects. [Details](#5-code-health-predicts-defects) |
| **A map the compiler agrees with** | **95% to 99.6%** of the calls it draws are confirmed by the language's own compiler | Go, TypeScript, Dart, C and Python, each graded by its own toolchain. [Details](#accuracy-by-language) |
| **Knows which tests matter** | **97.5%** correct on which tests to run for a change, with no coverage run | Teams can run the tests a change touches instead of the whole suite. [Details](#test-intelligence) |
| **Runs on a laptop at any size** | **15 million lines** (dotnet/runtime) indexed on one laptop, with no API key | Updates after that take seconds. Building the map uses **10x less memory** than the next tool. [Details](#scale) |

### Words used on this page

| Term | Plain meaning |
|---|---|
| Agent | An AI coding assistant such as Claude Code, Codex or Cursor |
| Call graph | A map of which function calls which. It is how a tool can say what breaks when code changes |
| Precision | Of the things a tool reports, the share that are correct |
| Recall | Of everything that is really there, the share the tool found |
| Held out, sealed | Test cases set aside before any tuning, so they show what you will see on your own code |
| p-value | The chance a difference this size is luck. Below 0.05 is the usual bar, and most results here are far below it |

### One index, the work of several tools

one local index, and most of it needs no model key.

| Category | Tools teams use today | What repowise does | Evidence |
|---|---|---|---|
| Code health | CodeScene, SonarQube maintainability rating | 53 detectors score every file on defect risk, maintainability and performance, then rank what to fix first | [Measured](#5-code-health-predicts-defects): 2.3x CodeScene's recall of later defects at a fixed review budget |
| Code graph and search for agents | Sourcegraph, CodeGraph, Serena | Call graph, hybrid search and answers served to agents over MCP | [Measured](#1-finding-the-right-files): retrieval, agent loop and graph accuracy against CodeGraph and Serena |
| Change risk and test impact | Codecov gates, test-impact tools | Where a change ranks among the repository's recent commits, which tests to run, coverage gates, all in the GitHub Action | [Measured](#test-intelligence): test selection precision |
| Dead code | Knip, vulture, ts-prune | Unreachable files, unused exports and unused packages, each with a confidence tier and its evidence, across languages | Capability |
| Documentation | DeepWiki, Swimm | A generated wiki per module and file (needs a model key), plus checks that flag docs the code no longer supports (no key) | Capability |
| Decision records | ADR files kept by hand | Finds decisions in ADRs, comments, commits and PR bodies, ties each to the code it governs, flags stale ones | Capability |
| Multi-repo contracts | Contract tests, hand-kept API catalogs | Matches HTTP, gRPC, message-topic and data contracts between repositories in a workspace, and flags breaking changes | Capability |

Repowise complements a security scanner such as Semgrep and an AI code reviewer
such as CodeRabbit. In open source, the pull-request surface is the GitHub
Action (gates, annotations, SARIF, GitLab Code Quality); the PR comment bot is a
separate free hosted GitHub App.

---

<a id="2-what-changes-in-a-real-agent-loop"></a>

## Half the work in a real agent loop

**What we asked.** Give an AI agent real questions about a large codebase
(`django/django`), once with no tool and once with each context tool. Does the
tool save it work, and does the answer stay as good?

**What we found.** With repowise the agent made **3.8 tool calls instead of
7.2**, opened **3.0 files instead of 7.2**, and wrote **31.6% less**. Answer
quality did not change. The saving grows on harder questions (34.3%), and on a
small local model it reaches **47.9% less output and 41.5% less time**.

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../.github/assets/bench/agent-output-tokens-dark.svg" />
  <img src="../.github/assets/bench/agent-output-tokens.svg" alt="Output tokens an agent writes to reach an answer across 43 django questions on Codex: repowise 1,250, CodeGraph 1,383, Serena 1,550, Graphify 1,658, code-review-graph 1,710, bare agent 1,828 baseline" width="100%" />
</picture>
</div>

| Tool | Agent used it | Output tokens | vs bare agent | Tool calls | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|
| **repowise** | **44 / 44** | **1,250** | **-31.6%** | **3.8** | **37 of 44** | **<0.0001** |
| CodeGraph | 44 / 44 | 1,383 | -24.4% | 4.0 | 37 of 44 | <0.0001 |
| Serena | 43 / 43 | 1,550 | -14.8% | 10.1 | 35 of 43 | <0.0001 |
| Graphify | 43 / 43 | 1,658 | -8.9% | 7.4 | 31 of 43 | 0.003 |
| code-review-graph | 43 / 43 | 1,710 | -6.0% | 7.2 | 26 of 43 | 0.046 |
| *bare agent (control)* | 0 / 44 | 1,828 | baseline | 7.2 | n/a | n/a |

Same prompt for every tool, each tool's full advertised surface, a fresh index on
the same pinned commit. Codex (`gpt-5.6-sol`), August 2026, the 43 questions all
six arms completed. After correcting for five simultaneous comparisons, three of
the five reductions remain clearly significant, repowise's among them.

**On a local model.** With `qwen3:8b` under Ollama (15 questions), repowise cuts
output by 40.8% and wall clock by 27.5%. With only its local tools switched on
(`get_answer` off), the cut is 47.9% in output and 41.5% in wall clock, at about
the same answer quality as the bare agent.

<details>
<summary><b>Method</b></summary>

- **Work saved; quality unchanged.** No tool measurably changes answer
  quality: blind-judge scores sit within 0.04 to 0.25 points of the bare agent
  on a 10-point scale, inside the 0.69-point rerun noise.
- **Output tokens, not dollars.** Prompt caching makes whichever arm runs first
  look cheaper; output tokens are never cached, so those are what we publish.
- **Claude Code** (`claude-sonnet-5`, 15 questions, one run): output down
  15.9% (p = 0.035) when the agent calls repowise. Claude Code loads tool
  schemas on demand, so how often the agent reaches for a tool varies by run.

Raw data, every cell including failures:
**[rung9](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung9)**
(Codex) and
**[rung6](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung6)**
(15-question runs). Per-tool setup notes:
[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head).

</details>

---

<a id="1-finding-the-right-files"></a>

## Pointing the agent at the right files

**What we asked.** For a real bug, the benchmark (ContextBench) lists the files
the fix actually changed. Does the tool return them?

**What we found.** Repowise's answer tool found **88% of the needed files**. The
next best tool found 61%. Case by case it won 19 times and lost once. No AI judge
is involved: a file is either in the answer or it is not. The 112 cases were
split before any work started, and every figure below comes from the 42 that
stayed sealed until the final run (August 2026).

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../.github/assets/bench/file-coverage-dark.svg" />
  <img src="../.github/assets/bench/file-coverage.svg" alt="File coverage on 42 sealed ContextBench instances: repowise get_answer 0.876, repowise search_codebase 0.742, CodeGraph 0.610, Graphify 0.546, code-review-graph 0.445, cocoindex 0.361" width="100%" />
</picture>
</div>

| Tool | File coverage | n | Precision | Files served |
|---|---:|---:|---:|---:|
| **repowise** (`get_answer`) | **0.876** | 42 | 0.087 | 19.2 |
| **repowise** (`search_codebase`) | **0.742** | 42 | 0.168 | 8.2 |
| CodeGraph | 0.610 | 42 | 0.093 | 14.0 |
| Graphify | 0.546 | 42 | 0.033 | 34.5 |
| code-review-graph | 0.445 | 42 | **0.240** | 5.4 |
| cocoindex | 0.361 | 41 | 0.092 | 7.1 |

Per instance against CodeGraph: `get_answer` **19 wins, 1 loss, 22 ties, sign
test p = 0.00004**; `search_codebase` 13 wins, 3 losses, 26 ties, p = 0.021.

The two repowise tools have different profiles. `get_answer` finds the most,
from about 19 files. `search_codebase` finds 0.742 from 8.2 files, the best
coverage per file served in the table. code-review-graph returns the fewest
files and so leads precision.

<details>
<summary><b>Method</b></summary>

**The sealed half confirms it.** Overfitting makes the unseen half score worse.
Ours scores better on it, and CodeGraph, which nobody tuned against either
half, scores the same on both, so the halves are equally hard.

| | Other half (n=70) | **Sealed half (n=42)** |
|---|---:|---:|
| repowise (`get_answer`) | 0.810 | **0.876** |
| repowise (`search_codebase`) | 0.684 | 0.742 |
| CodeGraph | 0.6093 | 0.6095 |

File coverage compares returned paths with the gold files and nothing else.
cocoindex's n is 41 because one instance returned no answer. Its placing was
[pre-registered before its index existed](https://github.com/repowise-dev/repowise-bench/tree/master/configs).
This measures retrieval; the next section measures the agent loop.

Raw cells:
**[rung8](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung8)**.

</details>

---

<a id="4-command-output-compression"></a>
<a id="command-output-compression"></a>

## Up to 99% less command noise

**What we asked.** When an agent runs tests, installs or git commands, how much of
what it reads is noise?

**What we found.** Most of it. `repowise distill` trims output before the agent
reads it, keeps every error line, and restores anything it cut with
`repowise expand`.

| Command | Before | After | Saved |
|---|---:|---:|---|
| `npm ci` (1,344 packages) | full log | trimmed | **99.5%** |
| `git log -50` | 3,064 tokens | 331 | **89%** |
| `git diff` (30 commits of history) | 62,833 tokens | 8,635 | **86%** |
| `terraform plan` | full log | trimmed | **80.2%** |
| `pytest -q` (11 failures) | 3,374 tokens | 1,317 | **61%**, all 11 `FAILED` lines kept |
| `git status` (clean) | 83 tokens | 83 | 0%, already small, passed through |

Across the core filters the median saving is at least 60% on test, build and lint
output, with no error line lost, checked in CI on every change. Small output
passes through untouched, so distill never makes anything bigger.
Guide: [docs/agent/DISTILL.md](agent/DISTILL.md).

---

<a id="5-code-health-predicts-defects"></a>
<a id="code-health-predicts-defects"></a>

## Code health that predicts bugs

**What we asked.** A health score is only useful if the files it flags are the
files that later break. We scored each file at a past commit and counted the bug
fixes it received over the next six months. Nothing after the scoring commit
feeds the score.

**What we found.** Across **21 projects, 9 languages and 2,826 files**, the score
separates buggy from clean files well (**ROC AUC 0.737**, 95% CI 0.683 to 0.787).
It beats recent churn by +0.100 and past bugs by +0.117 (DeLong p < 1e-9), and
holds at 0.76 to 0.78 on PROMISE/jEdit, a dataset it never saw.

**Against CodeScene**, the closest commercial product, on the same **2,770 files**
with the same bug labels: ranking by repowise finds **2.3x the later-buggy files**
within the same review budget (the top 20% of lines).

| Paired test | repowise | CodeScene | |
|---|---:|---:|---|
| Recall at a 20%-of-lines review budget | **0.173** | 0.074 | p = 0.003 |
| Effort-aware ranking (Popt) | **0.607** | 0.462 | p = 0.003 |
| Defect density, size-normalized | **2.18x** | 0.56x | p = 0.003 |
| Discrimination (ROC AUC) | 0.731 | 0.705 | p = 0.054 |
| Precision at a 20%-of-lines review budget | 0.580 | **0.636** | p = 0.64, a tie |

CodeScene flags about 27 files where we flag 132: a shorter list at a similar
precision, for a team that wants to work through it quickly.

<details>
<summary><b>Method</b></summary>

**What the score adds over file size.** File size alone discriminates about as
well (AUC 0.742). The score's advantage is effort-aware ranking, Popt +0.134
(95% CI +0.080 to +0.198), and that it says why a file is at risk.

**Weights are fitted, not hand-set:** a size-controlled logistic fit on files
scored before their bug window, so a detector earns weight only for defect lift
beyond being large. Of the 53 detectors, 25 move the defect score; the others
feed the maintainability or performance score, or are advisory.

Reports:
**[BENCHMARK\_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md)** ·
**[COMPARISON\_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)**

</details>

---

<a id="by-language"></a>
<a id="accuracy-by-language"></a>

## Call graph accuracy by language

**What we asked.** When repowise says "function A calls function B", is that true?
The judge is each language's own compiler, which resolves every call exactly. We
count how many of our calls it confirms (precision) and how many of its calls we
found (recall). Measured October 2026 on repowise main, each repository freshly
indexed.

**What we found.** **95% to 99.6% precision** on Go, TypeScript, Dart, C and three
of four Python projects, 87% to 95% on Java and 94% on Kotlin applications.

| Language | Repository | Set | Answer key | Compiler edges | Precision | Recall |
|---|---|---|---|---:|---:|---:|
| Go | gitleaks | dev | scip-go (Go compiler) | 1,596 | **0.996** | 0.985 |
| TypeScript | zod | dev | `tsc` | 1,269 | **0.995** | 0.796 |
| TypeScript | hono | dev | `tsc` | 706 | **0.976** | 0.806 |
| Dart | pub | dev | Dart analyzer | 2,311 | **0.994** | 0.827 |
| Python | httpx | dev | jedi | 1,753 | **0.994** | 0.838 |
| Python | click | dev | jedi | 2,246 | **0.959** | 0.745 |
| Python | typer | held out | jedi | 2,099 | **0.963** | 0.846 |
| Python | attrs | held out | jedi | 1,328 | 0.495 | 0.370 |
| C | redis | dev | scip-clang (clang) | 34,349 | **0.950** | 0.709 |
| C | git | held out | scip-clang (clang) | 28,118 | **0.979** | 0.830 |
| Java | jsoup | dev | scip-java (javac) | 4,332 | **0.953** | 0.926 |
| Java | gson | dev | scip-java (javac) | 1,760 | **0.920** | 0.908 |
| Java | jackson-core | held out | scip-java (javac) | 4,832 | **0.918** | 0.898 |
| Java | javapoet | held out | scip-java (javac) | 698 | 0.884 | 0.865 |
| Java | joda-time | held out | scip-java (javac) | 8,164 | 0.872 | 0.843 |
| Kotlin | nowinandroid | dev | Kotlin K2 compiler | 643 | **0.944** | 0.652 |
| Kotlin | ktor-samples | dev | Kotlin K2 compiler | 347 | **0.936** | 0.628 |
| Kotlin | okio (JVM) | dev | Kotlin K2 compiler | 1,468 | 0.675 | 0.379 |
| C# | FluentValidation | dev | scip-dotnet (Roslyn) | 629 | **0.916** | 0.506 |
| C# | AutoMapper | held out | scip-dotnet (Roslyn) | 3,157 | 0.863 | 0.321 |
| C# | Polly (4 projects) | dev | scip-dotnet (Roslyn) | 1,171 | 0.724 | 0.233 |
| Rust | fd | dev | rust-analyzer | 231 | 0.899 | 0.619 |
| Rust | ripgrep | dev | rust-analyzer | 4,555 | 0.852 | 0.532 |
| C++ | fmt | dev | scip-clang (clang) | 1,636 | 0.777 | 0.385 |
| Apex | 7 public Salesforce repositories | dev | census of literal references, 90 hand-graded edges | 40,907 | **90/90** | 0.999 |

**Held out means never tuned on.** Dev repositories are the ones accuracy work
is developed against. Held-out repositories are chosen in advance and never used
to pick a fix, so they are the fairest read of what you will see on your own
code. Java's held-out repositories read 0.87 to 0.92, within a few points of its
dev repositories.

**How it is scored.** An edge matches when the caller and the callee land on
the same declaration line the compiler names. Apex has no public compiler index,
so its recall is read against a census of literal `new X(` and `X.m(` references
to classes in other files, and its precision from 30 hand-graded edges on each of
three repositories. Python has no compiler that
resolves calls, so its reference is jedi, the standard static analyser for
Python. Every cell carries a 95% confidence interval in the published
artifacts.

**Reading the lower rows.** Two libraries are structurally unusual: okio is a
multiplatform library whose `expect` and `actual` declarations share names with
the standard library, and attrs exports its API through chained module aliases
(`s = attributes = attrs`). Application-shaped code in the same languages reads
0.94 (Kotlin) and 0.96 to 0.99 (Python). In Java and C#, a large share of what
remains is the choice between overloads of the right method: counting any overload of the
right method as correct, Java reads 0.92 to 0.98 and Polly 0.89.

**Recall** depends on how many entry points a codebase has and how much it
dispatches dynamically, so compare it within a language, not across languages.

Reference versions: scip-go 0.2.7, scip-java 0.13.1, scip-dotnet 0.2.14,
scip-clang 0.3.1, rust-analyzer 1.96.0, jedi 0.20.0, Dart analyzer 14.5.0,
kotlinc 2.4.21. Per-repository scores, confidence intervals and the scoring
code: **[graph/](https://github.com/repowise-dev/repowise-bench/tree/master/graph)**.

---

<a id="8-the-same-question-against-an-answer-key-we-do-not-control"></a>
<a id="is-the-call-graph-correct"></a>
<a id="call-graph-against-a-compiler-five-tools"></a>

## Call graph against four other tools

The same compiler check, run on every tool. The answer key is the **Go team's own
call graph** (`golang.org/x/tools`, RTA, over the fully type-checked program) and,
on TypeScript, **`tsc`'s own resolution** of every call site. Each cell is
precision / recall.

| Cell | repowise | CodeGraph 1.5.0 | codebase-memory-mcp 0.10.8 | Graphify 0.9.31 | code-review-graph 2.3.7 |
|---|---|---|---|---|---|
| cobra (with tests) | **0.995** / 0.705 | 0.929 / 0.763 | 0.912 / 0.743 | 0.971 / 0.433 | 0.997 / 0.174 |
| gitleaks (no tests) | **0.991** / **0.971** | 0.972 / 0.920 | 0.934 / 0.967 | 0.997 / 0.886 | 0.759 / 0.026 |
| gitleaks (with tests) | **0.989** / 0.939 | 0.971 / 0.895 | 0.922 / 0.945 | 0.995 / 0.832 | 0.800 / 0.032 |
| syft (no tests) | **0.979** / 0.539 | 0.872 / 0.508 | 0.635 / 0.542 | 0.771 / 0.447 | 0.968 / 0.201 |
| syft (with tests) | **0.982** / 0.341 | 0.864 / 0.338 | 0.673 / 0.361 | 0.802 / 0.273 | 0.966 / 0.086 |
| zod (no tests) | **0.995** / **0.796** | 0.729 / 0.373 | 0.987 / 0.694 | 0.825 / 0.248 | 0.932 / 0.652 |
| hono (no tests) | **0.976** / **0.806** | 0.805 / 0.684 | 0.949 / 0.686 | 0.980 / 0.688 | 0.966 / 0.691 |

> **In all seven cells, no tool that finds as much of the call graph as we do
> gets more of it right.**

Every tool more precise than us finds less of the graph, and every tool that
finds more is less precise. Repowise leads recall outright on both TypeScript
cells and on gitleaks without tests.

<details>
<summary><b>Method</b></summary>

**Two methods agree.** On Go, a hand-graded audit read 29/30 for us and 29/30
for CodeGraph; the compiler, over about 1,600 edges on the same repository, read
97.6% and 97.2%. A person reading source and a type checker land within about a
point on both tools.

**Where recall goes.** On syft without tests, 44% of missed edges are dynamic
dispatch and a further 39% dispatch with a closure at one end. Interface
dispatch averages 6.5 possible targets per call site there, so matching a
higher-recall tool means emitting six edges where one is right.

**Recall varies by entry points.** It runs from 0.03 to 0.97 across rows, driven
by how many entry points the compiler had (4 on gitleaks, 268 on syft with
tests), so compare recall within a row.

- Edges the compiler cannot judge are charged to nobody (16% to 46% of a tool's
  output on TypeScript, where the pinned corpus installs no dependencies, so
  TypeScript is scored without tests).
- A library has no `main`, so cobra is analysed through its test binaries.
- Graphify tags 93% of its call edges `INFERRED` and we score all of them;
  code-review-graph is scored on resolved rows only.
- Competitor columns are August 2026 runs at the pinned versions above; the
  repowise column is October 2026 main.

Full method, per-cell artifacts and the pre-registration:
**[graph/experiments/g4-oracle-anchored](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g4-oracle-anchored)**.

</details>

---

<a id="7-edge-precision"></a>
<a id="call-edges-hand-graded-across-nine-languages"></a>

## Call edges read by hand, nine languages

People read 280 call edges per tool (560 in all) across nine languages, each with
its imports and surrounding code open, against CodeGraph 1.5.0 (August 2026).

| | Correct / n | 95% CI |
|---|---|---|
| **repowise** | **240/280 = 85.7%** | [81.1, 89.3] |
| CodeGraph 1.5.0 | 164/280 = 58.6% | [52.7, 64.2] |

| Language | repowise | CodeGraph | |
|---|---|---|---|
| TypeScript | 29/30 | 7/30 | separates |
| Go | 29/30 | 29/30 | tie |
| C# | 30/30 | 20/30 | separates |
| Python | 28/30 | 19/30 | separates |
| Kotlin | 27/30 | 13/30 | separates |
| Swift | 23/30 | 19/30 | tie |
| C++ | 22/30 | 16/30 | tie |
| Rust | 22/30 | 13/30 | tie |
| Java *(n=40)* | 30/40 | 28/40 | tie |

Four of nine cells separate at 95% confidence and five are ties; at n=30 the
interval is about ±16 points near 60%. Compiler-graded results for the same
languages are in [accuracy by language](#accuracy-by-language): Java 0.87 to
0.95, Kotlin applications 0.94, C# 0.72 to 0.92. Where the two methods differ,
as in C#, the compiler figure is the stricter read.

<details>
<summary><b>Method</b></summary>

30 rows per language per tool, seed 2026, stratified by resolution strategy.
Java is read at 40 rows across two repositories. C++ is read at 50 rows per
side and enters the pooled figure at a seeded 30, so every language carries the
same weight. All 600 graded rows are published, each with the call site, the
declaration it was bound to, the verdict and the reason, with a script that
rebuilds every table.

**[graph/experiments/g1-edge-precision/rows](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g1-edge-precision/rows)**

</details>

---

<a id="test-intelligence"></a>

## Which tests cover a change

Repowise links tests to the code they exercise from the call graph, with no
coverage run needed. We checked those links against real per-test coverage on
this repository, over a slice with complete per-test attribution (37 test files,
159 production files).

| Question | Precision | Sample |
|---|---:|---|
| Which tests reach this file | **95.7%** | 44 of 46 claims |
| Which tests to run for a change | **97.5%** | 47 targets, all answered |

Inferred answers are file-level; measured coverage, when you ingest it, is
line-level, and repowise labels which kind each answer is. Details:
[Test intelligence](layers/TEST_INTELLIGENCE.md#accuracy-and-limits).

---

<a id="scale"></a>

## Scale

Large public repositories, indexed end to end on one Windows laptop (31 GB RAM),
keyless (`repowise init --no-prose`), limited to 8 CPUs and 2 parse workers.
October 2026.

| Repository | Tracked files | Lines of code | First index | Peak memory | Index on disk |
|---|---:|---:|---:|---:|---:|
| dotnet/aspnetcore v8.0.31 | 15,510 | 1.5M | 20 min | 4.0 GiB | 1.4 GiB |
| elastic/elasticsearch v8.19.22 | 34,315 | 4.0M | 52 min | 7.8 GiB | 4.2 GiB |
| dotnet/runtime v8.0.31 | **58,924** | **15.0M** | **120 min** | **11.7 GiB** | 7.7 GiB |

Lines of code are non-blank lines in tracked source files (C#, IL, C, C++, Java,
TypeScript, JavaScript, Python, shell, assembly and similar), with data, markup and
docs left out. Peak memory is the whole process tree, one run per repository.

The first index is a one-time cost: it builds the call graph, git history, health,
tests, dead code and docs together. After that, each update re-analyses only what
changed. On aspnetcore, an update with no changes takes about 4 seconds.

---

<a id="6-indexing-time-the-row-we-lose"></a>
<a id="what-it-costs-to-run"></a>

## What it costs to run

Building the call graph, 35 repositories, five tools: 175 cells, none failed,
three timed runs each after a discarded warmup, nothing restored from cache.

| Tool | Median build | Median peak memory | Fastest on |
|---|---:|---:|---:|
| **repowise** | **2.77 s** | **75 MB** | 14 of 35 |
| CodeGraph | 3.65 s | 757 MB | **16 of 35** |
| codebase-memory-mcp | 6.21 s | 1,113 MB | 5 of 35 |
| code-review-graph | 9.97 s | 361 MB | 0 |
| Graphify | 12.23 s | 860 MB | 0 |

**Memory: lowest on 35 of 35 repositories**, about 10x below CodeGraph and 5x
below the next-lowest tool. The
gap widens with size: 64 MB against CodeGraph's 749 MB under 1,000 files, 152 MB
against 1,164 MB above, which leaves more room for the rest of a CI job. Speed
is level with CodeGraph: we lead under 1,000 files and
it leads above, where we do more resolution work per file.

<details>
<summary><b>Building the whole index</b></summary>

A full `repowise init` builds five layers, not one, so it takes longer than a
graph-only tool: a graph-only tool builds in 16 to 45 seconds, repowise builds
five layers in about 6 minutes without prose. On `django/django`:

| Tool | Index time | What it builds |
|---|---:|---|
| CodeGraph | 16.4 s | call graph |
| code-review-graph | 44.8 s | call graph |
| Graphify | 141.5 s | call graph, communities |
| **repowise** (`--no-prose`) | **366.8 s** | graph, git history, search index, decisions, health |
| **repowise** (default, prose on) | **1,058 s** | the same, plus generated documentation |

The call graph itself takes about 3 seconds. The rest builds a 90,477-edge graph
with communities and flow tracing, git history across 2,630 files, 3,392 wiki
pages embedded for search, architectural decisions, and 5,317 health findings.
Updates after the first index are incremental.

The graph table is construction only and the index table is a full
`repowise init`, so the two are not comparable with each other. Our graph is
built in memory, while CodeGraph writes a SQLite index; our memory figures come
from a subprocess arm built so peak RSS is read the same way as every
competitor's. Single machine and OS. A fitted build-cost curve for all five
tools across a 12x size range is in
[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head#the-result-nobody-in-the-field-had-published-a-build-cost-curve).

Per-repository tables:
**[graph/experiments/g6-build-cost](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g6-build-cost)**.

</details>

---

<a id="3-loading-one-commits-context-the-easy-number"></a>
<a id="loading-one-commits-context-the-easy-number"></a>

## Loading a commit's context

How many tokens an agent reads to take in one commit, over the 30 most recent
non-merge commits of `pallets/flask`, counted with `tiktoken` (`cl100k_base`):

| Strategy | Tokens per commit |
|---|---:|
| Full contents of every changed file | 13,984 |
| **`get_context` on the changed files** | **2,586** |
| `git diff` only | 1,412 |

`get_context` is 5.4x smaller than reading the changed files and returns every
symbol in each file with its signature and location, which the raw diff does
not.

---

## How we measure

- **Pre-register before running**, as its own commit, so a good result cannot
  become a different question afterwards.
- **Seal a held-out set**, split before any work begins and evaluated once.
- **Show precision and recall together**, and files served beside coverage.
- **Prove a tool was running before recording a zero.**
- **Use answer keys we did not write**: compilers, published benchmarks and
  real defect history.

Retrieval and the agent loop are measured on Python and Go; graph accuracy on
ten languages against their compilers (jedi for Python) and nine hand-graded. The method end to
end:
**[THE\_LOOP.md](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/THE_LOOP.md)**.

## Where the depth is

The evidence is in **[repowise-bench](https://github.com/repowise-dev/repowise-bench)**:
every graded cell, every pre-registration, and what each run cost.

| Start here | For |
|---|---|
| **[graph/](https://github.com/repowise-dev/repowise-bench/tree/master/graph)** | Graph accuracy: per-language compiler scores, the five-tool comparison, hand-graded precision across nine languages, build cost |
| **[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head)** | Retrieval and agent-loop results and per-tool setup notes |
| **[health-defect](https://github.com/repowise-dev/repowise-bench/tree/master/health-defect)** | The defect-prediction study and the CodeScene comparison |
| **[results/bakeoff\_2026\_08](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08)** | Every graded cell and every verbatim response |
| **[repro/README.md](https://github.com/repowise-dev/repowise-bench/blob/master/repro/README.md)** | Per claim: what it costs to reproduce |

Want your tool in the field? Adding a competitor is a YAML block. See
[CONTRIBUTING.md](https://github.com/repowise-dev/repowise-bench/blob/master/CONTRIBUTING.md).

<sub>Tool versions as measured: CodeGraph 1.5.0, Graphify 0.9.31, Serena 1.6.2.dev0,
code-review-graph 2.3.7, cocoindex as of 2026-08-09, codebase-memory-mcp 0.10.8.</sub>

## See also

- [The intelligence layers](layers/INTELLIGENCE_LAYERS.md)
- [Code health](layers/CODE_HEALTH.md)
- [Test intelligence](layers/TEST_INTELLIGENCE.md)
- [MCP tool reference](agent/MCP_TOOLS.md)
- [Roadmap](../ROADMAP.md)
