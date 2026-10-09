# Benchmarks

Repowise indexes a repository once, on your machine, into a call graph, git
history, code health, test links, decisions and documentation, and serves that
index to developers and their coding agents. Every number below was measured on
public repositories at pinned commits, against answer keys and samples we
publish in **[repowise-bench](https://github.com/repowise-dev/repowise-bench)**.
Where another tool beats us, the row is on this page too. The research each method comes from is listed in
[LINEAGE.md](LINEAGE.md).

## At a glance

### The headline numbers

| Claim | Number | Sample | Measured on |
|---|---|---|---|
| [Call graph checked by the compiler](#8-the-same-question-against-an-answer-key-we-do-not-control) (Go, TypeScript) | No tool that finds as much of the graph gets more of it right, in **7 of 7** cells. Our precision **0.976 to 0.995** | 7 cells, 37,853 compiler edges, 5 tools | repowise main `4fccb31ec`, 2026-10-03; competitors August 2026 |
| [Call graph accuracy by language](#accuracy-by-language) | Precision: C **0.95 to 0.98**, Python **0.96 to 0.98** (attrs 0.50), Rust **0.85 to 0.90**, C# **0.73 to 0.92**, C++ **0.75**, Java **0.73 to 0.78** | 16 repositories, 6 languages, recall beside every figure | repowise main `4fccb31ec`, 2026-10-03 |
| [Finding the right files](#1-finding-the-right-files) | **0.876** of the needed files found, against **0.610** for the next tool | 42 sealed instances, p = 0.00004 | repowise `081a59fa`, August 2026 |
| [Work saved in an agent loop](#2-what-changes-in-a-real-agent-loop) | **31.6% less** agent output than no tool, 3.8 tool calls against 7.2 | 43 django questions on Codex, p < 0.0001 | repowise `081a59fa`, August 2026 |
| [Health score against CodeScene](#5-code-health-predicts-defects) | **2.3x** the later-defective files inside a 20% review budget | 2,770 files, 21 repositories, p = 0.003 | May 2026 |
| [Which tests cover a file, with no coverage run](#test-intelligence) | **95.7%** precision on what reaches a file, **97.5%** on the list of tests to run | 46 claims and 47 targets, checked against per-test coverage | this repository, August 2026 |
| [Largest repository indexed](#scale) | **58,924 files** (dotnet/runtime) in 120 minutes at 11.7 GiB peak | one keyless run on a 31 GB laptop | pre-release build, 2026-10-02; its fixes are on main |
| [Memory to build the call graph](#what-it-costs-to-run) | **75 MB** median against 757 MB for the next tool, lowest on **35 of 35** repositories | 35 repositories, 5 tools | August 2026 |

The rows we lose are below with the rest: two tools draw a bigger call graph
than we do, CodeScene keeps a shorter and slightly more precise review list,
and a full index takes much longer than a graph-only tool because it builds far
more. Each one sits in its section with its numbers.

### One index, the work of several tools

Teams usually assemble these from separate products. Repowise builds them from
one local index, and most of it needs no model key.

| Category | Tools teams use today | What repowise does | Evidence |
|---|---|---|---|
| Code health | CodeScene, SonarQube maintainability rating | 53 detectors score every file on defect risk, maintainability and performance, then rank what to fix first | [Measured](#5-code-health-predicts-defects): predicts later defects, 2.3x CodeScene's recall at a fixed review budget |
| Dead code | Knip, vulture, ts-prune | Unreachable files, unused exports and unused packages, each with a confidence tier and its evidence, across languages | Capability, not measured on this page |
| Code graph and search for agents | Sourcegraph, CodeGraph, Serena | Call graph, hybrid search and answers served to agents over MCP | [Measured](#1-finding-the-right-files) against CodeGraph and Serena: retrieval, agent loop, graph accuracy. Sourcegraph not measured |
| Documentation | DeepWiki, Swimm | A generated wiki per module and file (needs a model key), plus checks that flag docs the code no longer supports (no key) | Capability, not measured |
| Change risk and test impact | Codecov gates, test-impact tools | Where a change ranks among the repository's recent commits, which tests to run, coverage gates, all in the GitHub Action | [Measured](#test-intelligence): test selection precision. Risk ranking: capability, not measured here |
| Decision records | ADR files kept by hand | Finds decisions in ADRs, comments, commits and PR bodies, ties each to the code it governs, flags stale ones | Capability, not measured |
| Multi-repo contracts | Contract tests, hand-kept API catalogs | Matches HTTP, gRPC, message-topic and data contracts between repositories in a workspace, and flags breaking changes | Capability, not measured |

Repowise does not replace a security scanner such as Semgrep or an AI code
reviewer such as CodeRabbit. In open source, the pull-request surface is the
GitHub Action (gates, annotations, SARIF, GitLab Code Quality). The PR comment
bot is a separate free hosted GitHub App.

---

<a id="by-language"></a>
<a id="accuracy-by-language"></a>

## Accuracy by language

A call graph is a map of which function calls which. It is what lets a tool say
what breaks when you change something. We check ours against each language's
own compiler: the compiler resolves every call site, and we count how many of
our edges it confirms (**precision**) and how many of its edges we found
(**recall**). We did not write any of these answer keys and cannot tune them.

Repowise main `4fccb31ec`, freshly indexed from source for every cell,
2026-10-03. No competitor was run in this section; the five-tool comparison is
the [next section](#8-the-same-question-against-an-answer-key-we-do-not-control).

| Language | Repository | Set | Answer key | Compiler edges | Precision | Recall |
|---|---|---|---|---:|---:|---:|
| Go | gitleaks | dev | scip-go (Go compiler) | 1,596 | **0.996** | 0.985 |
| TypeScript | zod | dev | `tsc` | 1,269 | **0.995** | 0.796 |
| TypeScript | hono | dev | `tsc` | 706 | **0.976** | 0.806 |
| Java | jsoup | dev | scip-java (javac) | 4,329 | 0.781 | 0.682 |
| Java | gson | dev | scip-java (javac) | 1,760 | 0.737 | 0.701 |
| Java | javapoet | held out | scip-java (javac) | 698 | 0.725 | 0.695 |
| C# | FluentValidation | dev | scip-dotnet (Roslyn) | 629 | **0.916** | 0.506 |
| C# | Polly (4 projects) | dev | scip-dotnet (Roslyn) | 1,172 | 0.731 | 0.200 |
| C# | AutoMapper | held out | scip-dotnet (Roslyn) | 3,157 | 0.863 | 0.321 |
| C | redis | dev | scip-clang (clang) | 34,349 | **0.950** | 0.709 |
| C | git | held out | scip-clang (clang) | 28,118 | **0.979** | 0.830 |
| C++ | fmt | dev | scip-clang (clang) | 1,596 | 0.750 | 0.368 |
| Rust | ripgrep | dev | rust-analyzer | 4,555 | 0.852 | 0.532 |
| Rust | fd | dev | rust-analyzer | 231 | 0.899 | 0.619 |
| Python | httpx | dev | jedi (not a compiler) | 1,753 | **0.983** | 0.696 |
| Python | click | dev | jedi (not a compiler) | 2,246 | **0.958** | 0.717 |
| Python | typer | held out | jedi (not a compiler) | 2,099 | **0.960** | 0.782 |
| Python | attrs | held out | jedi (not a compiler) | 1,328 | 0.500 | 0.368 |

**Dev and held out.** Dev repositories are the ones accuracy fixes were
developed against. Held-out repositories were chosen in advance and never used
to pick a fix, so they are the fairer read of what you will see on your own
code. On every held-out repository except attrs, main is more precise than the
released 0.54.0 build was: javapoet by 30 points, AutoMapper by 4, typer by 2.
On git it is level within 0.2 points while recall rose 21 points.

**How it is scored.** An edge matches when the caller and the callee land on the
same declaration line the compiler names. The SCIP indexers come from each
language's own compiler toolchain. Python has no compiler that resolves calls,
so the reference is jedi, a static analyser: it is the closest available
reference, and a call that resolves only at run time is outside what it can
see. Every cell carries a 95% confidence interval in the published artifacts.

**Limits, stated plainly:**

- **C# Polly lost precision.** It fell from 0.774 on 0.54.0 to 0.731 on main,
  while recall rose from 0.125 to 0.200. Constructor calls now become edges, and
  repowise keeps one graph node per overload set, so a call to the right
  constructor can land on the wrong overload and the compiler counts it as
  wrong. Polly is also measured on 4 of its projects only.
- **Python attrs is low at 0.50 precision.** attrs exports its API through
  chained module aliases (`s = attributes = attrs`), and calls through those
  aliases bind to the alias line. That gap is open.
- **Overloads explain much of the Java and C# gap.** Counting a call that lands
  on any overload of the right method as correct, Java reads 0.76 to 0.80 and C#
  0.83 to 0.93.
- **Recall is below 0.80 almost everywhere** outside Go and TypeScript. Dynamic
  dispatch is the main reason, as the next section shows for Go.
- **Not reported:** TypeScript through SCIP (that method did not agree with
  `tsc`, so TypeScript comes from `tsc` directly), Kotlin (not run), C++ and
  Rust held-out repositories (no answer key could be built), Ruby and PHP (no
  answer key exists).
- Do not compare recall across languages. It depends on how many entry points a
  codebase has and how much it dispatches dynamically.

Reference versions: scip-go 0.2.7, scip-java 0.13.1, scip-dotnet 0.2.14,
scip-clang 0.3.1, rust-analyzer 1.96.0, jedi 0.20.0. Per-repository scores,
confidence intervals and the scoring code:
**[graph/](https://github.com/repowise-dev/repowise-bench/tree/master/graph)**.

---

<a id="8-the-same-question-against-an-answer-key-we-do-not-control"></a>
<a id="is-the-call-graph-correct"></a>

## Call graph against a compiler, five tools

There are two ways to get a call graph wrong: miss calls that are real, or
invent calls that are not there. Either number alone is easy to game. Draw an
edge between everything and you find every real call. Draw one edge you are
sure of and every edge you drew is correct. So we report the pair.

The answer key is the **Go team's own call graph** (`golang.org/x/tools`, RTA,
over the fully type-checked program) and, on TypeScript, **`tsc`'s own
resolution** of every call site. Anyone with the toolchain can regenerate both.
Five tools, seven cells, **37,853 compiler edges**. Each cell is
precision / recall.

| Cell | repowise main (2026-10-03) | CodeGraph 1.5.0 | codebase-memory-mcp 0.10.8 | Graphify 0.9.31 | code-review-graph 2.3.7 |
|---|---|---|---|---|---|
| cobra (with tests) | **0.995** / 0.705 | 0.929 / 0.763 | 0.912 / 0.743 | 0.971 / 0.433 | 0.997 / 0.174 |
| gitleaks (no tests) | **0.991** / **0.971** | 0.972 / 0.920 | 0.934 / 0.967 | 0.997 / 0.886 | 0.759 / 0.026 |
| gitleaks (with tests) | **0.989** / 0.939 | 0.971 / 0.895 | 0.922 / 0.945 | 0.995 / 0.832 | 0.800 / 0.032 |
| syft (no tests) | **0.979** / 0.539 | 0.872 / 0.508 | 0.635 / 0.542 | 0.771 / 0.447 | 0.968 / 0.201 |
| syft (with tests) | **0.982** / 0.341 | 0.864 / 0.338 | 0.673 / 0.361 | 0.802 / 0.273 | 0.966 / 0.086 |
| zod (no tests) | **0.995** / **0.796** | 0.729 / 0.373 | 0.987 / 0.694 | 0.825 / 0.248 | 0.932 / 0.652 |
| hono (no tests) | **0.976** / **0.806** | 0.805 / 0.684 | 0.949 / 0.686 | 0.980 / 0.688 | 0.966 / 0.691 |

The competitor columns are August 2026 measurements at the pinned versions
above; they were not re-run. The repowise column is main, freshly indexed, and
its recall is higher than our August figure in all seven cells.

> **In all seven cells, no tool that finds as much of the call graph as we do
> gets more of it right.**

Read across any row. Every tool more precise than us finds less of the graph,
and every tool that finds more is less precise. The claim names no threshold,
so it cannot be tuned by picking a cutoff, and adding a competitor can only
break it. Two competitors were added after it was first written, and it held.

**Where we lose.** We are not the tool that draws the biggest map. We lead
recall in three cells (both TypeScript cells and gitleaks without tests) and
trail in four. Across 35 repositories of cross-file coverage, measured in August,
codebase-memory-mcp is ahead of us on 15 and we are ahead on none. We are the
most precise tool outright in three cells; Graphify is more precise on both
gitleaks cells and on hono, and code-review-graph on cobra.

**What a bigger map costs.** On syft, more than a third of what the coverage
leader emits is a call the Go compiler says does not exist. The highest
precision anywhere in the table, 0.997, comes from a graph holding 17% of the
calls in the repository. Graphify's trade is a real one: 89% recall against our
97% on gitleaks, at slightly higher precision.

<details>
<summary><b>Method and limits</b></summary>

**Two methods agree.** On Go, the hand-graded audit below read 29/30 for us and
29/30 for CodeGraph. The compiler, over about 1,600 edges on the same
repository in August, read 97.6% and 97.2%. A person reading source and a type
checker land within about a point on both tools.

**Where our misses go.** On syft without tests, in August, 44% of our missed
edges were dynamic dispatch and a further 39% dispatch with a closure at one end
(the buckets overlap). Interface dispatch averages 6.5 possible targets per call
site there. Matching the coverage leader's recall means emitting six edges where
one is right, which is the behaviour the precision column charges for.

**Do not compare recall across rows.** It runs from 0.03 to 0.97, driven by how
many entry points the compiler had (4 on gitleaks, 268 on syft with tests), not
by tool quality.

- **Two languages, seven cells, five repositories.** This five-tool comparison
  is not a claim about other languages. The [per-language table](#accuracy-by-language)
  covers six more for repowise alone, and the hand-graded audit below is the
  nine-language comparison against another tool.
- **A contradicted edge is strong evidence, not proof.** RTA is unsound under
  reflection and `go:linkname`. That applies to every tool equally, and the gaps
  are far larger than it could explain.
- **Edges the compiler cannot judge are charged to nobody**: 0.4% to 11.1% of a
  tool's output on Go, 16% to 46% on TypeScript, where the pinned corpus installs
  no dependencies. For the same reason the TypeScript with-tests variants are
  void and quoted nowhere.
- **A library has no `main`**, so cobra is analysed through its test binaries.
- **Two arms are read through an adapter we wrote.** Graphify tags 93% of its
  call edges `INFERRED` and we score all of them, the choice least favourable to
  us. code-review-graph is scored on resolved rows only, the choice most
  favourable to it.

Full method, per-cell artifacts and the pre-registration, including the two
predictions that missed:
**[graph/experiments/g4-oracle-anchored](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g4-oracle-anchored)**.

</details>

---

<a id="7-edge-precision"></a>

## Call edges hand-graded across nine languages

The compiler sections reach more tools or more languages. This one reaches both,
by having people read the source: 280 call edges per tool (560 in all), across
nine languages, every row read with its imports and enclosing scope open.
Measured in August 2026 against CodeGraph 1.5.0.

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

**Four of nine cells separate and five are ties**, and we report the ties as
ties. At n=30 the interval is about ±16 points near 60%, so C++ is a tie despite
looking like a 20-point lead.

**Read our number the other way round: about one call edge in seven is wrong.**
Rust, C++ and Java are where that concentrates. The compiler-graded table above
agrees for those three, and adds C#: the hand audit read C# at 30/30, while the
compiler reads 0.73 to 0.92 across three repositories. For C#, plan against the
compiler figure.

<details>
<summary><b>Method and limits</b></summary>

30 rows per language per tool, seed 2026, stratified by resolution strategy.
Java is read at 40 rows because its first repository was an outlier, so a second
was added. C++ is read at 50 rows per side and enters the pooled figure at a
seeded 30, so every language carries the same weight. All 600 graded rows are
published, each with the call site, the declaration it was bound to, the verdict
and the reason, with a script that rebuilds every table and fails if it
disagrees.

**One repository goes to CodeGraph.** On `seastar` CodeGraph reads 6/10 against
our 5/10. Our misses there are chained calls on an untyped receiver. On `aria2`
both read 10/10 and CodeGraph resolves 24,950 distinct call edges to our 9,486,
so precision is not the only reading.

**We graded both sides.** That is what the compiler sections exist to check.

**Measured at one commit.** Diffed site by site against a later tip, 12 of the
280 graded rows moved, and every one of them had been graded wrong. Re-reading
at the tip could only raise the figure, so 85.7% is a floor.

**[graph/experiments/g1-edge-precision/rows](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g1-edge-precision/rows)**

</details>

---

<a id="1-finding-the-right-files"></a>

## Finding the right files

Before a tool can save an agent work, it has to point at the right code.
ContextBench ships the files a fix actually needed, and a tool either returns
them or it does not. No LLM judge is involved, which makes this the most
reproducible result on the page.

The 112 instances were split 70 / 42 by instance id before any work started, and
the 42 stayed sealed until the final measurement. Every figure below is from the
sealed 42. Measured August 2026 on repowise `081a59fa`.

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
from a list of about 19 files. `search_codebase` finds 0.742 from 8.2 files, the
best coverage per file served in the table. Precision is not our column:
code-review-graph's 0.240 is the highest, partly because precision rises for
whoever returns the fewest files.

<details>
<summary><b>Method and limits</b></summary>

**Why this is not tuned to the questions.** Our first run came last, at 0.228,
and we published it. A query-time gate was discarding most candidates before
ranking; fixing it is what moved the number, for every user. The split is the
check:

| | Other half (n=70) | **Sealed half (n=42)** |
|---|---:|---:|
| repowise (`get_answer`) | 0.810 | **0.876** |
| repowise (`search_codebase`) | 0.684 | 0.742 |
| CodeGraph | 0.6093 | 0.6095 |

Overfitting makes the unseen half score worse. Ours scores better, and CodeGraph,
which nobody tuned against either half, scores the same on both, so the halves
are equally hard. We do not quote a pooled 112-instance figure.

**The grader never reads `confidence`.** File coverage compares returned paths
with the gold files and nothing else. A `get_answer` reply can cover every gold
file and report `confidence: low`, or the reverse, without moving this number.
The 0.876 says nothing about how well that field is calibrated.

**cocoindex was measured on 2026-08-09**, the others between 2026-08-02 and
2026-08-06, on the same instances and grading. Its n is 41 because one instance
never answered; excluding it is kinder to it than scoring a zero (0.361 against
0.353). Its last place was
[pre-registered before its index existed](https://github.com/repowise-dev/repowise-bench/tree/master/configs).

**This is retrieval, not task success.** It shows we find the right files, not
that an agent using us writes better code.

Raw cells:
**[rung8](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung8)**.

</details>

---

<a id="2-what-changes-in-a-real-agent-loop"></a>

## What changes in a real agent loop

Every question in `django/django`'s question set, across five question shapes.
Six arms: repowise, four competing tools, and a bare agent with no tools. Same
prompt, each tool's full advertised surface, a fresh index on the same pinned
commit. Codex (`gpt-5.6-sol`), August 2026, repowise `081a59fa`. Five of the 48
questions hit an API usage cap in every arm, so the figures cover the 43 all six
arms completed.

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

A third less output than working with no tool, in 3.8 tool calls against 7.2,
opening 3.0 files instead of 7.2. CodeGraph is a genuine second at -24.4%; more
than one tool in this field works. Correcting for testing five tools at once,
three reductions are solid and two are marginal. The saving grows with the work:
on the harder half of questions it was 34.3%, on the easier half 27.2% (a
post-hoc split).

<details>
<summary><b>Other harnesses, and what this does not show</b></summary>

**Claude Code (`claude-sonnet-5`), 15 questions.** repowise was called on 15 of
15 and cut output by 15.9% (p = 0.035); no other tool reached significance.
Whether an agent calls a tool at all was unstable on this harness: reruns on
later days returned 4 of 15 and then 3 of 15 for us, and 2 of 14 for CodeGraph.
Claude Code loads tool schemas on demand, so the agent has to go looking first.
A run on Opus failed its own control, so no token figure is quoted from it.

**Local `qwen3:8b` under Ollama, 15 questions.** Every cell called its tool.

| Row | Output tokens | vs bare | Leaner on | Wall clock | vs bare |
|---|---:|---:|---:|---:|---:|
| repowise, full surface | 1,319 | -40.8% | 15 of 15 | 117 s | -27.5% |
| repowise, local-only tools | 1,172 | -47.9% | 15 of 15 | 96 s | -41.5% |
| *bare agent* | 2,336 | baseline | n/a | 171 s | baseline |

`get_answer` writes its answer with a hosted model, so the local-only row
switches it off; we confirmed the agent could not reach it. On a GPU, reading
one large payload is cheap and generating text over several rounds is not, which
is why the win shows up as time.

**What this section does not claim:**

- **Work saved, not quality.** A blind judge scored every tool, ours included,
  within 0.04 to 0.25 points of the bare agent on a 10-point scale, inside the
  0.69 points this benchmark moves when rerun unchanged. No tool measurably
  changed answer quality. On the local run, the local-only configuration
  answered about as well as a bare agent in about half the wall clock.
- **One repository, one commit, one prompt**, and `django` is in every model's
  training data. Nothing here measures a multi-hour engineering task.
- **No dollar figure.** Prompt caching made whichever arm ran first look cheap:
  arm position correlated -0.487 with dollar cost. Output tokens are never cached
  and correlated +0.010, so those are what we publish.

Raw data, every cell including failures:
**[rung9](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung9)**
(Codex) and
**[rung6](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08/rung6)**
(the 15-question runs). Setup traps per tool and what the runs cost:
[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head).

</details>

---

<a id="5-code-health-predicts-defects"></a>
<a id="code-health-predicts-defects"></a>

## Code health predicts defects

A health score is worth something only if the files it flags are the files that
break. Each file is scored at a historical commit, bug fixes are counted over the
following six months, and nothing after the scoring commit feeds the score.
Measured May 2026.

Across **21 repositories, 9 languages and 2,826 files**: **ROC AUC 0.737** (95%
CI 0.683 to 0.787), 0.55 to 0.86 per repository. It beats recent churn by +0.100
AUC and prior-defect history by +0.117 (DeLong p < 1e-9). On PROMISE/jEdit, a
dataset it never saw, it holds at 0.76 to 0.78.

**Against CodeScene**, the closest commercial product. Both tools scored the same
**2,770 files** (the files CodeScene also scores) at the same commit against the
same defect labels:

| Paired test | repowise | CodeScene | |
|---|---:|---:|---|
| Recall at a 20%-of-lines review budget | **0.173** | 0.074 | p = 0.003 |
| Effort-aware ranking (Popt) | **0.607** | 0.462 | p = 0.003 |
| Defect density, size-normalized | **2.18x** | 0.56x | p = 0.003 |
| Discrimination (ROC AUC) | 0.731 | 0.705 | p = 0.054, not significant |
| Precision at a 20%-of-lines review budget | 0.580 | **0.636** | p = 0.64, a tie |

Ranking by repowise health surfaces **2.3x the later-defective files** within the
same review budget.

**Where CodeScene is ahead.** It flags about 27 files where we flag 132, so its
list is shorter and slightly more precise: a deliberate operating point that a
team can work through quickly. CodeScene also reports a -0.58 correlation between
its score and issue-resolution time on proprietary data. We could not replicate
that on open data (GitHub merge time, 17 repositories, 271 files: -0.09, interval
spanning zero), so the business-impact axis stays CodeScene's.

<details>
<summary><b>Method and limits</b></summary>

**Not better than file size at discrimination.** Lines of code alone score 0.742
against our 0.737 (p = 0.92, a tie). The gain is in effort-aware ranking, Popt
+0.134 (95% CI +0.080 to +0.198), and unlike a line count the score says why.

**Weak among files of similar size.** Holding size fixed, AUC runs 0.525 / 0.572 /
0.593 / 0.718 across size quartiles. A prior-defects baseline still beats us on
Popt by 0.085 while losing on AUC.

**Weights are fitted, not hand-set:** a size-controlled logistic fit on files
scored before their bug window, so a detector earns weight only for defect lift
beyond being large. Of the 53 registered detectors, 25 move the defect score;
the others feed the maintainability or performance score, or are advisory.

Reports:
**[BENCHMARK\_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md)** ·
**[COMPARISON\_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)**

</details>

---

<a id="scale"></a>

## Scale

Three large public repositories, indexed end to end on one laptop. Keyless
index only (`repowise init --no-prose`, no model calls, a stand-in embedder),
one run each, on a Windows 11 laptop with 31 GB of RAM and 32 hardware threads,
under a guard that limited the run to 8 CPUs and 2 parse workers.

| Repository | Tracked files | First index | Peak memory | Index on disk | Build |
|---|---:|---:|---:|---:|---|
| dotnet/aspnetcore v8.0.31 | 15,510 | 20 min | 4.0 GiB | 1.4 GiB | 0.54.0 release |
| elastic/elasticsearch v8.19.22 | 34,315 | 54 min | 9.8 GiB | 4.2 GiB | 0.54.0 release |
| elastic/elasticsearch v8.19.22 | 34,315 | 52 min | 7.8 GiB | 4.2 GiB | pre-release, with two memory fixes |
| dotnet/runtime v8.0.31 | **58,924** | **120 min** | **11.7 GiB** | 7.7 GiB | pre-release, with four memory and speed fixes |

Measured 2026-10-01 and 2026-10-02. The pre-release builds were main at that
time plus memory fixes that have since merged; as of 2026-10-03, main contains
all of them, and three further memory fixes merged after these runs that no
number here includes. Peak memory is the whole process tree. The pre-release
build produced byte-identical outputs to main on a determinism check.

**Updates.** On aspnetcore (0.54.0), an update with no changes took 4.1 s. An
update for a one-file commit took 469 s at 3.8 GiB, because the health pass
re-scores the whole repository, so its cost follows repository size, not change
size. Updates were not measured on the larger two.

**What we have not measured.** Nothing larger than 58,924 files; the Linux kernel
(92k files) was not run. Only Windows. One run per configuration, and walls on
this machine move by up to 2x between runs.

---

<a id="test-intelligence"></a>

## Test intelligence

Repowise links tests to the code they exercise from the call graph, with no
coverage run needed. To check those inferred links, we compared them against a
real `coverage run --contexts=test` on this repository, over a slice with
complete per-test attribution (37 test files, 159 production files). Measured
August 2026.

| Question | Precision | Sample | Recall |
|---|---:|---|---|
| Which tests reach this file | **95.7%** | 44 of 46 claims | 27.7%; 77% among files where most executed lines are inside function bodies |
| Which tests to run for a change | **97.5%** | 47 targets, all answered | n/a |

Recall reads low because coverage counts a file as run when it was merely
imported. Code that a framework calls by convention, with no static caller, is
not reached. Inferred answers are file-level; measured coverage, when you ingest
it, is line-level. Repowise labels which kind each answer is, and an empty answer
means unknown, never "no tests". Details:
[Test intelligence](layers/TEST_INTELLIGENCE.md#accuracy-and-limits).

---

<a id="6-indexing-time-the-row-we-lose"></a>
<a id="what-it-costs-to-run"></a>

## What it costs to run

Two different questions. One is the cost of building a call graph, against tools
that build only a call graph. The other is the cost of building everything
repowise builds. We lead the first and lose the second. Both measured August
2026.

### Building the call graph

Graph construction only, 35 repositories, five tools: 175 cells, none failed,
three timed runs each after a discarded warmup, nothing restored from cache.

| Tool | Median build | Median peak memory | Fastest on |
|---|---:|---:|---:|
| **repowise** | **2.77 s** | **75 MB** | 14 of 35 |
| CodeGraph | 3.65 s | 757 MB | **16 of 35** |
| codebase-memory-mcp | 6.21 s | 1,113 MB | 5 of 35 |
| code-review-graph | 9.97 s | 361 MB | 0 |
| Graphify | 12.23 s | 860 MB | 0 |

**Memory: lowest on 35 of 35 repositories, about 10x below the next tool.** The
gap widens with size: 64 MB against CodeGraph's 749 MB under 1,000 files, 152 MB
against 1,164 MB above. Our worst repository is 468 MB; one other tool reaches
5,523 MB on that same repository. That is the difference between fitting in a
normal CI container and not.

**Speed is roughly tied.** CodeGraph is fastest on 16 repositories to our 14: we
lead under 1,000 files and trail above, because we do more resolution work per
file. The precision tables are the other half of that trade.

### Building the whole index

This is what `repowise init` costs, and here we are the slowest tool measured.
On `django/django`:

| Tool | Index time | What it builds |
|---|---:|---|
| CodeGraph | 16.4 s | call graph |
| code-review-graph | 44.8 s | call graph |
| Graphify | 141.5 s | call graph, communities |
| **repowise** (`--no-prose`) | **366.8 s** | graph, git history, search index, decisions, health |
| **repowise** (default, prose on) | **1,058 s** | the same, plus generated documentation |

That is **22x** CodeGraph like for like, and 135x with prose on. The call graph
itself takes about 3 seconds. The rest builds a 90,477-edge graph with
communities and flow tracing, git history across 2,630 files, 3,392 wiki pages
embedded for search, architectural decisions, and 5,317 health findings. If a
call graph is all you need, CodeGraph builds one in 16 seconds. Updates after the
first index are incremental.

<details>
<summary><b>Method and limits</b></summary>

**Never quote the two tables against each other.** The graph table is
construction only; the index table is a full `repowise init`.

**One caveat favours us on the graph table.** Our graph is built in memory and
discarded, so serialisation is excluded from our column, while CodeGraph writes a
real SQLite index. Our memory figures come from a subprocess arm built so peak
RSS is read the same way as every competitor's.

**CodeGraph's headline speed claim is incremental re-sync**, about 0.3 s to fold
one file into a 4,400-file project. That is not measured here, and we expect to
lose it.

Single machine, single OS: the memory ratios are unlikely to invert, but
absolute numbers will move. The [scale section](#scale) covers repositories far
larger than this corpus, for repowise alone. A fitted build-cost curve for all
five tools across a 12x size range is in
[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head#the-result-nobody-in-the-field-had-published-a-build-cost-curve);
our exponent is 0.906, which we read as sublinear work, not efficiency, because
symbol density halves across the range.

Per-repository tables:
**[graph/experiments/g6-build-cost](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g6-build-cost)**.

</details>

---

<a id="4-command-output-compression"></a>
<a id="command-output-compression"></a>

## Command-output compression

`repowise distill <cmd>` compresses command output before the agent reads it:
errors first, exit code preserved, every omission recoverable through an inline
`[repowise#<ref>]` marker. One run per command on one repository, July 2026.

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%**, all 11 `FAILED` lines kept |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits) | 62,833 | 8,635 | 86%, **needs re-measurement, see below** |
| `git log --oneline -30` | 321 | 321 | 0%, already compact |
| `git status` (clean) | 83 | 83 | 0%, too small to distill |

**The `git diff` row overstates the saving.** A saving may only count output the
agent would have received, and the host truncates a command result at 30,000
characters; 62,833 raw tokens is far past that cap. Treat that 86% as unsupported
until re-measured. The `pytest` and `git log` rows sit under the cap.

The 0% rows show the guard working: distill never inflates small output.
[RTK](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) does
the same job and we have not run it head to head, so this table is a
before-and-after of our own output. Guide: [docs/agent/DISTILL.md](agent/DISTILL.md).

---

<a id="3-loading-one-commits-context-the-easy-number"></a>
<a id="loading-one-commits-context-the-easy-number"></a>

## Loading a commit's context

How many tokens an agent reads to take in one commit. Over the 30 most recent
non-merge commits of `pallets/flask`, counted with `tiktoken` (`cl100k_base`), on
repowise main `4fccb31ec` with a keyless index, 2026-10-03:

| Strategy | Tokens per commit |
|---|---:|
| Full contents of every changed file | 13,984 |
| **`get_context` on the changed files** | **2,586** |
| `git diff` only | 1,412 |

`get_context` is 5.4x smaller than reading the changed files, and larger than the
raw diff, because it returns every symbol in each file with its signature and
location. One payload is not a session; the agent-loop section is the measure
of work saved.

An earlier version of this page reported 393 tokens per commit. That figure came
from a harness run that was receiving error responses in place of context, and it
has been withdrawn.

---

## Limits that apply to the whole page

- **Retrieval and agent-loop numbers are Python and Go**, and the agent loop is
  one repository at one commit. The graph sections are wider: nine languages
  hand-graded, Go and TypeScript against a compiler across five tools, and six
  more languages against a compiler for repowise alone. A JavaScript and
  TypeScript retrieval corpus is built, but its sealed half is unrun, so nothing
  from it is quoted.
- **We index code files only.** On repositories where documentation or JSON
  files are part of the answer, that lowers our retrieval score by construction.
- **Dates differ by section.** Graph accuracy is current main; retrieval, agent
  loop and cost are August 2026; health is May 2026. Each section says which.
- **Command-output compression has no head-to-head**, and one row needs
  re-measuring.

## How to read a number on this page

In this category, advertised savings often shrink when someone else reruns them.
In July 2026 JetBrains reran two token-saving tools on real agent work:
[Caveman](https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/)
advertised 65% and measured 8.5%;
[RTK](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/)
advertised 60 to 90% and measured 7.6% more expensive. Shrinking one payload is
easy to measure. A whole agent session is harder, because agents re-read and
re-explore.

Four rules apply to every number here:

- **Pre-register before running**, as its own commit, so a good result cannot
  become a different question afterwards.
- **Seal a held-out set**, split before any work begins and evaluated once.
- **Show precision and recall together**, and files served beside coverage.
  Never average them into one figure.
- **Prove a tool was running before recording a zero.** A dead server and a bad
  tool score the same.

The method end to end:
**[THE\_LOOP.md](https://github.com/repowise-dev/repowise-bench/blob/master/head-to-head/THE_LOOP.md)**.

## Where the depth is

This page is the summary. The evidence is in
**[repowise-bench](https://github.com/repowise-dev/repowise-bench)**: every graded
cell, every pre-registration, the runs we invalidated with their notes, and what
each run cost.

| Start here | For |
|---|---|
| **[graph/](https://github.com/repowise-dev/repowise-bench/tree/master/graph)** | Graph accuracy: hand-graded precision across nine languages, the compiler comparison on Go and TypeScript, per-language scores for repowise, cross-file coverage and build cost |
| **[head-to-head](https://github.com/repowise-dev/repowise-bench/tree/master/head-to-head)** | Retrieval and agent-loop results, per-tool setup notes, and what producing them cost |
| **[health-defect](https://github.com/repowise-dev/repowise-bench/tree/master/health-defect)** | The defect-prediction study and the CodeScene comparison |
| **[results/bakeoff\_2026\_08](https://github.com/repowise-dev/repowise-bench/tree/master/results/bakeoff_2026_08)** | Every graded cell and every verbatim response |
| **[repro/README.md](https://github.com/repowise-dev/repowise-bench/blob/master/repro/README.md)** | Per claim: what it costs to reproduce and which ones need credentials |

Found a problem with a number, or want your tool in the field? Adding a
competitor is a YAML block. See
[CONTRIBUTING.md](https://github.com/repowise-dev/repowise-bench/blob/master/CONTRIBUTING.md).

<sub>Tool versions as measured: CodeGraph 1.5.0, Graphify 0.9.31, Serena 1.6.2.dev0,
code-review-graph 2.3.7, cocoindex as of 2026-08-09, codebase-memory-mcp 0.10.8.</sub>

## See also

- [The intelligence layers](layers/INTELLIGENCE_LAYERS.md)
- [Code health](layers/CODE_HEALTH.md)
- [Test intelligence](layers/TEST_INTELLIGENCE.md)
- [MCP tool reference](agent/MCP_TOOLS.md)
- [Roadmap](../ROADMAP.md)
