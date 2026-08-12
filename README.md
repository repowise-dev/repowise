<!-- mcp-name: dev.repowise/repowise -->

<div align="center">

<a href="https://www.repowise.dev"><img src=".github/assets/banner.png" alt="repowise: the codebase intelligence layer for your AI coding agent" width="100%" /></a>

<p align="center">
  <a href="https://www.repowise.dev"><img src="https://img.shields.io/badge/LIVE_DEMO-repowise.dev-F59520?style=for-the-badge&labelColor=0A0A0A" alt="Live demo: repowise.dev" /></a>
</p>

<p align="center">
  <a href="https://repowise.dev/repo/repowise-dev/repowise"><img src="https://api.repowise.dev/badge/wiki/repowise-dev/repowise.svg?style=for-the-badge" alt="repowise: explore code" /></a>
  <a href="https://repowise.dev/repo/repowise-dev/repowise/code-health"><img src="https://api.repowise.dev/badge/health/repowise-dev/repowise.svg?style=for-the-badge" alt="Code health" /></a>
  <a href="https://github.com/repowise-dev/repowise/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/repowise-dev/repowise/ci.yml?branch=main&style=for-the-badge&label=CI&labelColor=0A0A0A" alt="CI status" /></a>
  <a href="https://pypi.org/project/repowise/"><img src="https://img.shields.io/pypi/v/repowise?style=for-the-badge&color=1E293B&labelColor=0A0A0A&logo=pypi&logoColor=white" alt="PyPI version" /></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL--v3-059669?style=for-the-badge&labelColor=0A0A0A" alt="License: AGPL v3" /></a>
  <a href="https://github.com/repowise-dev/repowise/stargazers"><img src="https://img.shields.io/github/stars/repowise-dev/repowise?style=for-the-badge&logo=github&color=1E293B&labelColor=0A0A0A&logoColor=white" alt="GitHub stars" /></a>
</p>

<p align="center">
  <a href="https://www.repowise.dev/#contact"><strong>Hosted for teams →</strong></a> ·
  <a href="https://docs.repowise.dev"><strong>Docs</strong></a> ·
  <a href="https://discord.gg/cQVpuDB6rh"><strong>Discord</strong></a> ·
  <a href="mailto:hello@repowise.dev"><strong>Contact</strong></a>
</p>

<p align="center"><sub>
  <a href="#your-agent-stops-guessing">For your agent</a> ·
  <a href="#what-one-index-actually-builds">The five layers</a> ·
  <a href="#stop-paying-for-output-nobody-reads">Distill</a> ·
  <a href="#know-whats-dangerous-before-you-merge">Change risk</a> ·
  <a href="#-know-exactly-what-to-fix">Code health</a> ·
  <a href="#see-all-of-it">Dashboard</a> ·
  <a href="#past-one-repo">Workspaces</a> ·
  <a href="#quickstart-under-5-minutes-no-api-key">Quickstart</a> ·
  <a href="#supported-agents">Agents</a> ·
  <a href="#the-ten-mcp-tools">MCP tools</a> ·
  <a href="#measured-against-the-field">Benchmarks</a> ·
  <a href="#how-it-compares-on-capability">Comparison</a> ·
  <a href="#for-teams--enterprises">Teams</a>
</sub></p>

---

### Your AI agent burns most of its budget rediscovering your codebase. Index it once, and it never has to again.

<table align="center">
<tr>
<td align="center" width="250"><h2>#1 of 6</h2></td>
<td align="center" width="250"><h2>−31.6%</h2></td>
<td align="center" width="250"><h2>97%</h2></td>
</tr>
<tr>
<td align="center" valign="top"><sub><strong>at finding the right files.</strong><br />0.876 file coverage against the<br />next tool's 0.610, on a <strong>sealed</strong><br />42-instance split. <em>p=0.00004</em></sub></td>
<td align="center" valign="top"><sub><strong>of your agent's own output tokens,</strong><br />reached in 3.8 tool calls where a<br />bare agent needed 7.2. <em>n=43,<br />p&lt;0.0001, leaner on 37 of 44</em></sub></td>
<td align="center" valign="top"><sub><strong>fewer tokens to load a commit.</strong><br />393 instead of 13,984 raw, counted<br />with deterministic tiktoken across<br />30 commits. <em>35.6x, pooled</em></sub></td>
</tr>
</table>

<sub>Measured head to head against the open-source agent-context field, on instances held out
from every improvement round. Defect risk validated separately at <strong>ROC AUC 0.737</strong>
across 21 repos and 9 languages, leakage-free. Every layer computed with <strong>zero LLM
calls</strong>. <strong>We publish the rows we lose</strong>, and we are the slowest indexer here.
<a href="docs/BENCHMARKS.md"><strong>All of it, including the losses →</strong></a><br />
Free and self-hosted, runs on your machine, and the first index needs no API key.</sub>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/one-index-dark.svg" />
  <img src=".github/assets/one-index.svg" alt="One index producing code health, a dependency graph, git history, generated docs, architectural decisions, and ten MCP tools" width="100%" />
</picture>

</div>

---

Every question your agent asks about your repo has an answer that could have been
computed ahead of time. *Who calls this function? What breaks if I change it? Why is
it written this way? Which of these files is actually dangerous?* Instead, agents
rediscover it from scratch on every task: grep, read, re-read, forget.

repowise computes those answers once and keeps them current on every commit. Your
agent reads the answer instead of the codebase, and the same index gives your team a
defect-validated health score, change-risk scoring on every PR, and a local dashboard
for all of it. One `pip install`, no cloud, your code never leaves your machine.

---

## Your agent stops guessing

repowise exposes **ten task-shaped MCP tools** to Claude Code, Codex, Cursor, VS Code
and anything else that speaks MCP. Most tools are built around data entities (one
file, one symbol), which forces agents into long chains of sequential calls. These are
built around **tasks**: pass several targets in one call, get complete context back.

<img src=".github/assets/demo.gif" alt="The repowise dashboard running locally on localhost:3000: health score, code health map, a break-cycle refactoring plan, the agent prompt it generates, change coupling, and the generated wiki" width="100%" />

<sub>The same index those tools read from, browsable at `localhost:3000`. Recorded on this
repository, no API key and nothing uploaded.</sub>

Because the exploration work is already done, that phase mostly disappears. Loading
one commit's context through `get_context` costs **393 tokens instead of 13,984**
raw, 35.6x fewer. In a measured agent loop, across 43 questions on `django/django`,
that is worth **-31.6% of the agent's own output tokens** (p&lt;0.0001), reached in
**3.8 tool calls against a bare agent's 7.2** — roughly one answered question
replacing six greps. The saving grows with how much of the codebase the task
touches. CodeGraph is a genuine second here at -24.4%: we lead a field in which
more than one tool works.

**And it arrives without being asked.** Optional [hooks](docs/agent/HOOKS.md) push
context into the session at the moment it matters: the governing architectural
decision when your agent edits a file that decision covers, a warning when it touches
a file with a run of recent bug fixes, a compact briefing at session start. repowise
also generates your `CLAUDE.md` and `AGENTS.md` from the real index, so even an agent
with no MCP support starts informed.

**It learns from how you actually work.** repowise reads your own agent transcripts
for the corrections you keep making ("use the shared HTTP client, not raw requests")
and turns the durable ones into tracked decisions it delivers back later. The wiki
generation budget tilts toward the modules you and your agent ask about most. All
local, all deterministic, no extra LLM calls.

---

## What one index actually builds

Five layers, built in a single pass and kept in sync on every commit. Each one is
queryable from the CLI, the MCP tools, and the local dashboard.

| Layer | What it gives you | Edge |
|---|---|---|
| **◈ Graph** | Dependency graph across 18 languages · file + symbol nodes · 3-tier call resolution · Leiden communities · PageRank and execution flows · route→handler edges across 22 frameworks | A real graph most tools never build |
| **◈ Git** | Hotspots (decayed churn + activity floors) · ownership % · co-change pairs (hidden coupling) · bus factor · which files actually get bug-fixed, and how recently | Behavioural signals static analysis cannot see |
| **◈ Docs** | A generated wiki page per module and file · rebuilt incrementally every commit · freshness and confidence scoring · hybrid search (full-text + vector) · selectable style and output language | Stays current instead of rotting |
| **◈ Decisions** | Architectural decisions mined from five sources, evidence-backed, each traced to a verbatim source span and stamped exact / fuzzy / unverified | **★ Captured nowhere else** |
| **★ Code health** | **49 deterministic detectors**, of which only 26 may move the number · 1 to 10 per file · three signals: defect risk · maintainability · performance · concrete refactoring plans (Extract Class / Method / Helper, Move Method, Break Cycle, Split File) · **zero LLM, under 30s** | **★ Defect-validated, with the fix attached** |

**The whole wiki is generated with no LLM, then upgraded to model-written prose on
demand.** `repowise init --no-prose` builds the graph, git, decision and health
layers and renders every wiki page from your code's structure, with no API key and
no spend. Convert any part of it to LLM-written prose whenever you want, one page,
one directory, or a ranked coverage slice at a time, and pay only for what you pick,
from the CLI or right in the dashboard with the cost shown before you confirm.
(Seven of the eight decision sources are deterministic too; only the one harvested
during doc generation needs a provider.)

Full detail on every layer: **[docs/layers/INTELLIGENCE_LAYERS.md →](docs/layers/INTELLIGENCE_LAYERS.md)**

---

## Stop paying for output nobody reads

Most of what an agent reads back from a shell command is noise: 300 lines of passing
tests wrapped around 4 failures, full commit bodies when it asked "what changed
recently". `repowise distill <cmd>` compresses command output **before the agent reads
it**, errors first, exit code preserved.

```bash
repowise distill pytest          # 61% fewer tokens, all 11 failure lines kept
repowise distill git log -50     # 89% fewer tokens
repowise saved                   # what distillation saved you, in tokens and dollars
```

Nothing is lost. Every omission leaves an inline `[repowise#<ref>]` marker that
`repowise expand <ref>` reverses in full, so the agent can always pull the detail back
without re-running the command. Small outputs pass through untouched. An opt-in hook
rewrites noisy commands automatically, shown to you for approval first.

<div align="center">
<img src=".github/assets/savings.png" alt="repowise Costs dashboard: tokens and dollars saved across distill and the MCP tools" width="100%" />
<p align="center"><sub>The <strong>Costs</strong> dashboard tallies both savings surfaces, priced at your own agent's model. Example from a week of heavy local use.</sub></p>
</div>

Full guide: **[docs/agent/DISTILL.md →](docs/agent/DISTILL.md)**

---

## Know what's dangerous before you merge

Three deterministic signals, all computed from the graph and git history, no LLM:

- **Change risk.** Score any commit or `base..HEAD` range **0-10** from the shape of
  the diff, ranked against your repo's own recent commits. PR mode returns directives
  rather than vibes: `will_break`, `missing_cochanges`, `missing_tests`, `tests_to_run`.
  One command: `repowise risk main..HEAD`. ([reference →](docs/layers/CHANGE_RISK.md))
- **Bug history.** Which files and symbols actually get bug-fixed, and how recently.
  Doc, test and config commits are filtered out so the count means what it says, and a
  file with a run of recent fixes gets flagged as a bug magnet while you edit it.
  ([reference →](docs/layers/BUG_HISTORY.md))
- **Test intelligence.** Ingest coverage, find untested hotspots, and run only the
  tests a diff actually exercises with `repowise impacted-tests HEAD~1`.
  ([reference →](docs/layers/TEST_INTELLIGENCE.md))

Plus the free **[Repowise PR Bot](#the-pr-bot)**, which puts all of it on every pull
request. Zero LLM calls.

---

## The PR bot

Install the [GitHub App](https://github.com/apps/repowise-bot) and the index shows up
where the decision actually gets made. One comment per pull request, edited in place on
every push rather than reposted, and **a green PR gets no comment at all**.

<sub>See a real comment on a real PR, not a mockup:
[repowise-dev/repowise#1204](https://github.com/repowise-dev/repowise/pull/1204).</sub>

What decides a review is inline. What is context sits behind one fold, so the comment
stays about seventeen rows whatever it finds.

- **Blast radius, at symbol level.** The contracts this PR changed and every caller of
  them in a file the PR does not touch. Importing a module says nothing about whether
  the function you changed is the one being called, so file-level impact is the wrong
  altitude for the question a reviewer actually has.
- **Before you merge.** The tests that import your changed files, and the files that
  changed alongside them in past commits but are missing here.
- **A Check Run that can gate the merge**, with annotations on the specific lines the
  PR added. Advisory by default.
- **Change risk**, scored against the repository's own commit distribution rather than
  an absolute scale, so it stays meaningful on a repo whose typical commit is large.
- **AI vs human authorship** of the changed files, with the average health of each.
- Then hotspots, hidden coupling, declining health, dead code and the change map, one
  fold down.

### And a page the comment links to

Markdown runs out. The comment shows three callers and says "+6 more"; the page shows
all nine. Public, no sign-in, on a repository the reader has never seen.

<img src=".github/assets/pr-bot/pr-page-blast-map.jpg" alt="The public per-PR analysis page: the whole repository drawn as a treemap with the pull request's files lit and their importers marked, and below it a focus frame zoomed into the directory the change landed in, with every filename legible" width="100%" />

<sub>Every file in the repo, grouped by directory and sized by lines. The frame below
zooms to where the change landed.
[See it live →](https://repowise.dev/pr/repowise-dev/repowise/1204)</sub>

**[Install the PR bot →](https://github.com/apps/repowise-bot)** ·
[how it works →](https://www.repowise.dev/bot)

---

## ★ Know exactly what to fix

A score that says *"this file is risky"* is where most tools stop. repowise scores
every file, locates where the risk concentrates, and then names the specific fix.

<div align="center">
<img src=".github/assets/health-loop.svg" alt="repowise code-health loop: deterministic markers fan into three signals, the graph and git history locate where risk concentrates, and refactoring intelligence emits concrete plans your agent executes" width="100%" />
</div>

Every file is scored 1-10 by **49 deterministic detectors** (McCabe complexity, brain
methods, LCOM4 cohesion, god classes, native Rabin-Karp clone detection, untested
hotspots, change entropy, prior-defect history and more), split into three lenses:
**defect risk**, **maintainability**, and **performance** — static N+1 and I/O-in-loop
risk traced *across* files through the call graph, where file-local linters found **0**
of the cross-function cases and repowise surfaced ~90. Only **26** of the 49 are
permitted to move the defect number, because that is the number carrying published
accuracy claims.

> **Zero LLM calls, zero cloud, zero new runtime dependencies.** Pure Python over
> tree-sitter and git data, **under 30 seconds** on a 3,000-file repo — a budget
> enforced by a CI test, not an estimate. Marker weights are **calibrated against a
> real defect corpus, not hand-tuned**: every file scored at a commit preceding the
> bug window so nothing leaks backward, and an L2-logistic fit with file size as an
> explicit control, so a marker only earns weight for defect lift *beyond* being big.
> Only the learned constants ship.

**It proves itself on your repo, not just on a benchmark.** After every index,
repowise checks its own flags against your git history and reports what it found:
*"16 of the 20 lowest-health files had a bug fix in the last 6 months, 3.3x the 24%
baseline."* If that number is bad on your codebase, you will see it. (It is an
association on your indexed history, not a forward prediction — the leakage-free
version is [in the benchmarks](docs/BENCHMARKS.md#5-code-health-predicts-defects).)

Then it names the fix. Not "this class is too big", but **Extract Class**, **Extract
Helper**, **Move Method**, **Break Cycle**, **Split File**, or **Extract Method**, with
the exact methods, edges and symbols that move, the **blast radius** of callers and
co-changing files that have to move with them, and a graph-aware ranking so a fix on a
central hub outranks the same fix on a leaf. Extract Method goes down to an
intra-procedural dataflow pass that lifts the exact span and infers a
behavior-preserving signature.

```bash
repowise health                        # KPIs and lowest-scoring files
repowise health --refactoring-targets  # ranked, concrete plans
repowise health --trend                # snapshots plus declining-health alerts
```

The dashboard renders each plan as a card with a copy-to-agent button. An optional LLM
step, never in the indexing path and only on request, expands any plan into generated
code and a unified diff.

<sub>Validated on <strong>21 open-source repos across 9 languages</strong> (2,826 files,
scored at a fixed point and checked against the following 6 months of bug fixes,
keyword-labelled): <strong>ROC AUC 0.737</strong> [0.683, 0.787]. The signal is
correlated with file size and weakens sharply within a fixed size band, which we report
rather than bury. Independently recomputed from the raw data.</sub>

<sub>Against <strong>CodeScene</strong>, the leading commercial code-health tool, on the
same 2,770 files and the same defect labels, ranking by repowise health surfaces
<strong>2.3x the defects under a fixed review budget</strong> (paired, p = 0.003).
<a href="docs/BENCHMARKS.md">Full head-to-head, methodology and limitations →</a></sub>

Guides: **[code health](docs/layers/CODE_HEALTH.md)** · **[refactoring](docs/layers/REFACTORING.md)**

---

## See all of it

`repowise serve` starts the full web dashboard next to the MCP server. No separate
setup, all local.

<table>
<tr>
<td width="50%"><img src=".github/assets/dashboard/architecture-page.png" alt="Architecture view: the dependency graph laid out and explorable, with a context drawer per node" width="100%" /><br/><sub><b>Architecture</b> · the dependency graph, laid out and explorable, with per-node context and change coupling</sub></td>
<td width="50%"><img src=".github/assets/dashboard/code-health-map.png" alt="Code health map: every file as a bubble, hover to inspect score, coverage and tests" width="100%" /><br/><sub><b>Code Health</b> · every file as a bubble, hover any one to inspect its score, size, coverage and findings</sub></td>
</tr>
<tr>
<td width="50%"><img src=".github/assets/dashboard/chat-page.png" alt="Chat view: ask questions against the indexed repo, with answers that cite the files and pages they came from" width="100%" /><br/><sub><b>Chat</b> · ask the codebase a question, answers cite the files and pages they came from</sub></td>
<td width="50%"><img src=".github/assets/dashboard/docs-page.png" alt="Docs view: auto-generated wiki pages with a tree, mermaid diagrams, and freshness badges" width="100%" /><br/><sub><b>Docs</b> · auto-generated wiki pages for the whole codebase, with confidence and freshness badges</sub></td>
</tr>
</table>

Also in there: **Chat** (ask the codebase in natural language) · **Docs** (the
generated wiki, with Mermaid and a graph sidebar) · **Architecture** and **C4**
(Context → Containers → Components) · **Knowledge Graph** plus a zoomable canvas map ·
**Risk**, **Hotspots**, **Coupling** and **Blast radius** · **Contributors** ·
**Decisions** (evidence drawer and evolution timeline) · **Symbols** · **Security** ·
**Dead code** · **Stats** · **Costs** · **Workspace**.

Every view and what each one answers: **[docs/start/DASHBOARD.md →](docs/start/DASHBOARD.md)**

---

## Past one repo

Real systems are not one repository, and the interesting failures live in the gaps
between them.

- **Workspaces.** Index many repos as one unit and get what only a cross-repo view can
  show: **contracts** matched between a producer and its consumers, so a breaking API
  change is caught before it ships, plus cross-repo **co-change** pairs, federated MCP
  that answers across the whole estate, and conformance checks.
  ([docs/scale/WORKSPACES.md →](docs/scale/WORKSPACES.md))
- **Worktrees just work.** Run `repowise init` or `repowise update` inside a linked git
  worktree and it detects the base checkout, seeds that worktree's index from it, and
  catches up incrementally. No flags, no second full index.
  ([docs/scale/WORKTREES.md →](docs/scale/WORKTREES.md))
- **Auto-sync.** Keep the index current with a post-commit hook, a file watcher
  (`repowise watch`), a webhook, or polling. An incremental update takes seconds.
  ([docs/scale/AUTO_SYNC.md →](docs/scale/AUTO_SYNC.md))

---

## In your editor

The **Repowise** VS Code extension puts the index where code actually gets written:
know what your change breaks before you push (riskiest files ranked, what is
downstream, forgotten companion files, missing tests, suggested reviewers), health in
the gutter and status bar, callers and ownership on hover, refactoring plans as
CodeLens, and the full dashboards inside the editor. One install also registers the MCP
server with VS Code, so the same local index serves both you and your agent, and
exposes six tools to GitHub Copilot. Quiet by default, everything toggleable, nothing
leaves your machine.

Install from the Marketplace (search **Repowise**) or Open VSX, then run **Repowise:
Set Up This Repository**. Guide: **[docs/agent/VSCODE.md →](docs/agent/VSCODE.md)**

---

## Supported agents

**Six agents wired end to end · two at the Full tier · every other MCP host one
paste away.**

<p>
  <strong>Full tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/Claude_Code-D97757?style=flat-square&logo=claude&logoColor=white" alt="Claude Code" />
  <img src="https://img.shields.io/badge/Codex_CLI-000000?style=flat-square&logo=openai&logoColor=white" alt="Codex CLI" />
</p>
<p>
  <strong>Good tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/VS_Code-007ACC?style=flat-square&logo=visualstudiocode&logoColor=white" alt="VS Code" />
  <img src="https://img.shields.io/badge/Cursor-000000?style=flat-square&logo=cursor&logoColor=white" alt="Cursor" />
  <img src="https://img.shields.io/badge/OpenCode-000000?style=flat-square&logo=opencode&logoColor=white" alt="OpenCode" />
  <img src="https://img.shields.io/badge/Hermes-000000?style=flat-square&logoColor=white" alt="Hermes" />
</p>

**Full** is every surface repowise has: MCP tools, skills, slash commands, a managed
instructions file, hook-level interception of tool calls, and transcript mining after
the session. **Good** is the honest half of that: MCP tools and the config to reach
them, but no hook-level interception and no transcript mining. A Good-tier agent can
ask repowise anything; repowise never sees the tool calls in between. The tier is
computed from what each integration actually wires, so this list cannot claim a depth
the code does not have.

Everything else that speaks MCP is one snippet away. `repowise agents print-config
claude-code` prints a stdio server entry to paste into Cline, Windsurf, Zed, Gemini
CLI or any other host that keys on `mcpServers`, and repowise writes nothing.

Adding an agent takes **one descriptor file and one registry line**, with no changes to
the orchestrators. Full matrix and the contributor recipe:
**[docs/agent/INTEGRATIONS.md →](docs/agent/INTEGRATIONS.md)**

---

## Supported languages

**18 languages parsed to AST · 13 at the Full tier · framework-aware across all of them.**

<p>
  <strong>Full tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black" alt="JavaScript" />
  <img src="https://img.shields.io/badge/Svelte-FF3E00?style=flat-square&logo=svelte&logoColor=white" alt="Svelte" />
  <img src="https://img.shields.io/badge/Vue-42B883?style=flat-square&logo=vuedotjs&logoColor=white" alt="Vue" />
  <img src="https://img.shields.io/badge/Java-ED8B00?style=flat-square&logo=openjdk&logoColor=white" alt="Java" />
  <img src="https://img.shields.io/badge/Kotlin-7F52FF?style=flat-square&logo=kotlin&logoColor=white" alt="Kotlin" />
  <img src="https://img.shields.io/badge/Go-00ADD8?style=flat-square&logo=go&logoColor=white" alt="Go" />
  <img src="https://img.shields.io/badge/Rust-000000?style=flat-square&logo=rust&logoColor=white" alt="Rust" />
  <img src="https://img.shields.io/badge/C++-00599C?style=flat-square&logo=cplusplus&logoColor=white" alt="C++" />
  <img src="https://img.shields.io/badge/C%23-512BD4?style=flat-square&logo=csharp&logoColor=white" alt="C#" />
  <img src="https://img.shields.io/badge/Scala-DC322F?style=flat-square&logo=scala&logoColor=white" alt="Scala" />
  <img src="https://img.shields.io/badge/Ruby-CC342D?style=flat-square&logo=ruby&logoColor=white" alt="Ruby" />
</p>
<p>
  <strong>Good tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/C-A8B9CC?style=flat-square&logo=c&logoColor=black" alt="C" />
  <img src="https://img.shields.io/badge/Swift-F05138?style=flat-square&logo=swift&logoColor=white" alt="Swift" />
  <img src="https://img.shields.io/badge/PHP-777BB4?style=flat-square&logo=php&logoColor=white" alt="PHP" />
  <img src="https://img.shields.io/badge/Dart-0175C2?style=flat-square&logo=dart&logoColor=white" alt="Dart" />
  &nbsp;<strong>· Partial &nbsp;</strong>
  <img src="https://img.shields.io/badge/Luau-00A2FF?style=flat-square&logo=lua&logoColor=white" alt="Luau" />
</p>

SQL and dbt projects get real `ref()` / `source()` lineage, shell scripts get
function-level symbols, HTML pages contribute their `<script src>` / `<link href>`
dependencies (including `index.html` → `src/main.ts`), and OpenAPI, Protobuf,
GraphQL, Dockerfile, Terraform and friends get dedicated handlers. Anything else is
still tracked through git history: blame, hotspots, co-change.

Adding a language takes **one `.scm` query file and one config entry**, with no changes
to the parser core. Full matrix and the contributor recipe:
**[docs/layers/LANGUAGE_SUPPORT.md →](docs/layers/LANGUAGE_SUPPORT.md)**

---

<a id="quickstart"></a>

## Quickstart (under 5 minutes, no API key)

**1. Install**

```bash
pip install repowise          # Windows: python -m pip install repowise
repowise --version
```

**2. Index your repo**

```bash
cd /path/to/your/repo
repowise init
```

Bare `init` asks. It scans the repo first, then offers three ways to index it:
everything (the wiki written by a model), no prose (the same wiki rendered from
your code's structure, no key and no spend), or advanced, which walks through the
indexing and generation knobs. Nothing is spent before you see an estimate and
confirm it.

If you would rather not answer questions, or you are scripting this, name the
mode and add `-y`:

```bash
repowise init --no-prose -y    # free, no key, no questions
repowise init --prose -y       # model-written subsystem pages, cost pre-approved
```

Either way you get the dependency graph, git history, code-health scores and
dead-code findings in seconds, plus a complete wiki: file, module, layer and cycle
pages, the architecture diagram, the repo overview, API and infra pages, and the
onboarding collection. On the keyless path every page carries a footer saying it
was derived from structure, and the repo overview describes composition, entry
points, clusters and dependencies rather than what the project does end to end,
because no template can derive that. Full-text search works on this index;
semantic search needs an embedder configured (Ollama is the keyless option).

Went keyless and want the wiki written by a model later? You do not have to decide
now. Upgrade it whenever you like with `repowise generate`, a page, a directory,
or the whole thing at a time, each behind a cost estimate:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # or OPENAI_API_KEY / GEMINI_API_KEY
repowise generate                       # write the unwritten subsystem pages, behind one cost estimate
repowise generate --path src/api        # or just one area first
repowise generate --all                 # or rewrite the prose on every subsystem page
```

Bare `repowise generate` prints the wiki's state and writes the unwritten
subsystem (concept) pages behind a single cost estimate. Every other page was
already rendered from structure at index time.

Or pick the provider for the first index directly with `repowise init --provider
gemini|anthropic|openai`.

**3. Connect your agent.** The MCP server is `repowise mcp`, served from the repo directory.

<details><summary><b>Claude Code</b></summary>

```bash
# Plugin (adds the tools, slash commands and skills):
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise

# ...or wire the MCP server directly:
claude mcp add repowise -- repowise mcp
```
Or commit a project `.mcp.json`:
```json
{ "mcpServers": { "repowise": { "command": "repowise", "args": ["mcp"] } } }
```
</details>

<details><summary><b>Codex CLI</b></summary>

Add to `~/.codex/config.toml`:
```toml
[mcp_servers.repowise]
command = "repowise"
args = ["mcp"]
```
Or: `codex mcp add repowise -- repowise mcp`
</details>

**4. First real call.** Ask your agent: *"Use repowise `get_overview` to summarize this
repo"*, or *"`get_context` for `src/auth.py`"*. You get graph-grounded architecture and
per-file triage instead of a flurry of greps.

> `get_overview` and `get_context` work in index-only mode with no key, synthesized
> from the graph, git and health layers. `search_codebase` and `get_answer` read the
> wiki, which index-only mode does build, but they answer from pages rendered from
> structure rather than model-written prose, and `search_codebase` is full-text only
> until you configure an embedder.

Full walkthrough: **[docs/start/QUICKSTART.md →](docs/start/QUICKSTART.md)**

---

## The ten MCP tools

Every response carries an `_meta` envelope with `index_age_days`, `indexed_commit`, and
a `stale_warning` that fires only when the indexed HEAD diverges from live `.git/HEAD`,
so your agent always knows how much to trust what it just read.

| Tool | What only this tool answers |
|---|---|
| `get_overview()` | Architecture summary, module map, entry points, git health. The first call on any unfamiliar codebase. |
| `get_answer(question)` | Hybrid retrieval (full-text plus vector via RRF), PageRank bias and 1-hop graph expansion into one cited answer with a calibrated `retrieval_quality`. Collapses search → read → reason into a single round-trip. |
| `get_context(targets, include?)` | Triage card for files, modules or symbols: summary, signatures, `hotspot` bit, governing decisions, `symbol_id`s. `include` opens callers, callees, ownership and metrics. Batch many targets in one call. |
| `get_symbol("file.py::Name")` | Source for one indexed symbol with exact line bounds. Cheaper and safer than `Read` plus offset math. |
| `search_codebase(query, kind?)` | Semantic search over the wiki, filterable by kind (implementation / test / config / doc), tagging each result's `search_method`. |
| `get_risk(targets, changed_files?)` | Hotspots, dependents, co-change partners, ownership, test gaps, bug history. Pass `changed_files` for PR mode and get a `directive` block back. |
| `get_change_risk(revspec)` | Pre-merge defect score for a whole commit or range from the shape of the diff, ranked as a percentile against recent commits, plus the tests coverage proves it touches. |
| `get_why(query?, targets?)` | Architectural decisions and their verbatim evidence spans, stamped exact / fuzzy / unverified. Falls back to git archaeology when no decisions exist. |
| `get_dead_code(...)` | Unreachable code by confidence tier with cleanup-impact estimates, and cross-repo consumer detection in workspace mode. |
| `get_health(targets?, include?)` | Per-file marker scores across all three signals. `include` opens coverage, trends, per-file signals, the accuracy self-check, and structured refactoring plans. |

Ten is a deliberate ceiling rather than a limit we ran into: a small, task-shaped
surface is easier for an agent to choose from than a large one. Worked example (*"add
rate limiting to all API endpoints"* in 5 calls instead of ~30 greps and reads), the
opt-in tools, and the full reference: **[docs/agent/MCP_TOOLS.md →](docs/agent/MCP_TOOLS.md)**

---

## Measured against the field

Six open-source agent-context tools, the same repositories, the same pinned
commits, the same questions, each one given its own full advertised tool surface.
The full page carries the rows we lose beside the rows we win.

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/bench/file-coverage-dark.svg" />
  <img src=".github/assets/bench/file-coverage.svg" alt="File coverage on 42 sealed ContextBench instances: repowise get_answer 0.876, repowise search_codebase 0.742, CodeGraph 0.610, Graphify 0.546, code-review-graph 0.445, cocoindex 0.361" width="100%" />
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/bench/agent-output-tokens-dark.svg" />
  <img src=".github/assets/bench/agent-output-tokens.svg" alt="Output tokens an agent writes to reach an answer across 43 django questions on Codex: repowise 1,250, CodeGraph 1,383, Serena 1,550, Graphify 1,658, code-review-graph 1,710, bare agent 1,828" width="100%" />
</picture>
</div>

- **Finds the right files.** 0.876 file coverage against CodeGraph's 0.610 on a
  **sealed** 42-instance split, held out from every improvement round. 19 wins,
  1 loss per instance. Deterministic grading, no LLM judge. *n=42, sign test
  p=0.00004.* CodeGraph scores the same on both halves to three decimals, so
  neither half is the easy one.
- **Less work in a real agent loop.** -31.6% output tokens against a bare agent,
  leaner on 37 of 44 questions. *n=43, p&lt;0.0001.* CodeGraph is a genuine second
  at -24.4%: more than one tool here works, and we lead the field rather than
  being alone in it.
- **Fewer steps to get there.** 3.8 tool calls where the bare agent needed 7.2,
  and 3.0 files opened instead of 7.2 — the mechanism behind the token saving,
  visible directly rather than inferred.

**[The full results, the methodology, and the rows we lose →](docs/BENCHMARKS.md)**

---

## How it compares on capability

No single product competes with all of this, so there is no single table. Three
axes, three sets of real peers. Rows marked *measured* are head-to-head numbers,
and they link to **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)** where the sample
sizes, the tests and the rows we lose all live.

### As an agent context layer

Against the tools doing the same job: index a repository, serve it to a coding
agent over MCP.

| | repowise | CodeGraph | Serena | DeepWiki |
|---|---|---|---|---|
| Self-hostable, open source | ✅ AGPL-3.0 | ✅ | ✅ | ❌ cloud only |
| Private repo, no cloud | ✅ | ✅ | ✅ | ❌ OSS forks only |
| MCP tools served | 11 | 1 | 29 | 3 |
| **Finds the gold files** *([measured](docs/BENCHMARKS.md#1-finding-the-right-files), n=42 sealed)* | ✅ **0.876** | 0.610 | not in this run | not measured |
| **Output tokens vs a bare agent** *([measured](docs/BENCHMARKS.md#2-what-changes-in-a-real-agent-loop), n=43)* | ✅ **-31.6%** | -24.4% | -14.8% | not measured |
| **Index time, django** *([measured](docs/BENCHMARKS.md#6-indexing-time-the-row-we-lose))* | ⚠️ **366.8s**, slowest here | ✅ **16.4s** | not measured | n/a, cloud |
| | *one-time; updates after it are incremental* | | | |
| Generated documentation | ✅ | ❌ | ❌ | ✅ |
| Proactive agent hooks | ✅ Claude + Codex | ❌ | ❌ | ❌ |
| Auto-generated AI instructions (`CLAUDE.md`, `AGENTS.md`) | ✅ | ❌ | ❌ | ❌ |
| Command-output distillation | ✅ reversible | ❌ | ❌ | ❌ |
| Learns from your usage (session-mined decisions, demand-weighted docs) | ✅ | ❌ | ❌ | ❌ |
| Architectural decision records | ✅ | ❌ | ❌ | ❌ |
| Multi-repo workspace intelligence | ✅ contracts, co-change, federated MCP | ❌ | ❌ | ❌ |

CodeGraph builds its index **22x faster than we do**, and if a call graph is all
you need, that is the right trade. With prose generation on, which is what a
default `repowise init` actually costs, it is **135x**. Graphify and
code-review-graph were in the same measured field and are on the benchmarks page.

<sub>Measured against CodeGraph 1.5.0, Graphify 0.9.31, Serena 1.6.2.dev0,
code-review-graph 2.3.7, on repowise `081a59fa` (between v0.37.0 and v0.38.0),
August 2026. Unmarked rows are capability presence, not measurements.</sub>

### As a code health tool

| | repowise | CodeScene |
|---|---|---|
| Self-hostable, open source | ✅ AGPL-3.0 | ⚠️ on-prem Docker, proprietary |
| Code health score (1-10) | ✅ 49 detectors, 26 scoring | ✅ 25-30 |
| Brain Method / LCOM4 / god class | ✅ | ✅ |
| **Defects found at a 20% review budget** *([measured](docs/BENCHMARKS.md#5-code-health-predicts-defects), 2,770 files)* | ✅ **0.173** | 0.074 |
| **Effort-aware ranking, Popt** *(measured, p=0.003)* | ✅ **0.607** | 0.462 |
| **Precision at that budget** *(measured)* | 0.580 | ✅ **0.636**, a shorter list |
| **Discrimination, ROC AUC** *(measured, paired)* | 0.731 | 0.705 — *p=0.054, not significant* |
| Defect-prediction AUC, published and reproducible | ✅ 0.737 over 21 repos, held-out 0.76-0.78 | ✅ Code Red study |
| Business impact (resolution time) | ❌ *we could not replicate this on open data* | ✅ Code Red study |
| Git intelligence (hotspots, ownership, co-change) | ✅ | ✅ |
| Pre-merge change-risk scoring | ✅ 0-10 + directives | ✅ |
| Health trend + declining alerts | ✅ rolling snapshots | ✅ |
| Bus factor analysis | ✅ | ✅ |
| Concrete cross-file refactoring plans | ✅ graph-aware + blast radius | ⚠️ within-function only |
| Dataflow-verified within-function plans | ✅ CFG + reaching definitions | ⚠️ LLM-generated, unverified |
| Test-coverage intelligence | ✅ LCOV/Cobertura/Clover | ❌ |
| Untested-hotspot detection | ✅ coverage × hotspot | ❌ |
| Dead code detection | ✅ | ❌ |
| Serves it to an AI agent over MCP | ✅ | ✅ |
| Local dashboard | ✅ | ✅ |

CodeScene is the only other vendor in this category with a published empirical
defect study, which is why it is the one we ran head to head against. It flags
about 27 files where we flag 132, so if you want a short list to action rather
than the ranking that catches the most defects, its threshold is the better fit.

### Documentation generators

DeepWiki, Google Code Wiki and Swimm generate documentation from a repository,
which overlaps one of our five layers. **We have not measured against them**, so
there is no table here rather than a table of checkmarks. DeepWiki appears above
because it also serves an agent over MCP, which is a job we can be measured on.

### The PR bot, against the LLM review bots

| | Repowise PR Bot | CodeRabbit | Greptile |
|---|---|---|---|
| LLM calls per PR | ✅ **zero** | ❌ every review | ❌ every review |
| Same diff, same review | ✅ deterministic | ❌ sampled output | ❌ sampled output |
| Your code sent to a model provider | ✅ never | ❌ yes | ❌ yes |
| Symbol-level blast radius (changed contracts → their callers) | ✅ call graph | ❌ | ⚠️ prose, from context |
| Co-change partners missing from the PR | ✅ git history | ❌ | ❌ |
| Change risk vs the repo's own distribution | ✅ 0-10 + percentile | ❌ | ❌ |
| Public analysis page per PR, no sign-in | ✅ | ❌ | ❌ |
| Silent on a clean PR | ✅ by default | ⚠️ configurable | ⚠️ configurable |
| Cost on public repos | ✅ free, uncapped | ⚠️ free tier | ⚠️ free tier |
| Self-hostable | ✅ AGPL-3.0 | ❌ | ❌ |

The axis where this is not close is the first two rows. An LLM reviewer is a different
product with a different failure mode: it can read intent, and it can also be wrong in a
new way on every run. This one does set arithmetic over a call graph and a git history,
so there is nothing to hallucinate and nothing to prompt-inject, and pushing the same
diff twice produces the same review twice.

**repowise is the intersection:** an agent-native context layer *and* behavioral git
intelligence *and* a defect-validated health score with the fix attached, all out of
one index, self-hostable and open source. Full side-by-side comparisons:
**[repowise.dev/compare →](https://www.repowise.dev/compare)**

---

## Who it's for

| | Start here |
|---|---|
| **Individual developers** | `pip install repowise` → `repowise init` → query from Claude Code, Cursor, or any MCP agent. Fully local, bring your own key, free under AGPL-3.0. [For developers →](https://www.repowise.dev/for/developers) |
| **Team leads** | Know which PRs to worry about before you merge: change-risk scoring plus the free [Repowise PR Bot](https://github.com/apps/repowise-bot). [For team leads →](https://www.repowise.dev/for/teams) |
| **Engineering leaders** | See how much of your code AI wrote and whether it is healthy: agent provenance, health trends and bus factor, straight from git history. [For engineering leaders →](https://www.repowise.dev/for/engineering-leaders) |
| **Security & compliance** | Reachability-aware CVE triage, secret detection across full git history, and SBOM, on your real dependency graph. [For security →](https://www.repowise.dev/for/security) · [security review →](docs/business/SECURITY_COMPLIANCE.md) |
| **Enterprises** | On-prem and air-gapped, SSO/SCIM, commercial licensing with no AGPL obligation, IP indemnification. [For enterprise →](https://www.repowise.dev/for/enterprise) · [docs/business/COMMERCIAL.md](docs/business/COMMERCIAL.md) |

---

## For teams & enterprises

[**repowise.dev**](https://www.repowise.dev) is the same engine, fully managed, at
feature parity with self-hosted: every CLI command, every MCP tool, the whole
dashboard. We run it on our own codebase in the open:
[live snapshot →](https://www.repowise.dev/s/5a6b93fa9a69) ·
[explore public repos →](https://www.repowise.dev/explore).

On top of self-hosting: managed deploys and webhooks with auto re-index on every
commit, a hosted MCP endpoint so any client can point at one URL with no local server,
a CVE-aware security layer, cross-repo intelligence at scale, and integrations (Slack,
Jira/Linear, Confluence/Notion, PagerDuty) *(rolling out)*.

What is GA versus in development, on-prem topology, SSO/SCIM/RBAC and pricing:
**[docs/business/COMMERCIAL.md](docs/business/COMMERCIAL.md)** ·
[Get in touch →](https://www.repowise.dev/#contact)

---

## Privacy

- **Self-hosted:** your code never leaves your infrastructure, so no code, file paths
  or repo names are ever sent. The CLI does report **anonymous, opt-out** usage
  telemetry (command names and coarse environment only) to help us prioritize; turn it
  off with `repowise telemetry disable`, `DO_NOT_TRACK=1`, or by running fully offline.
  [What's collected →](docs/reference/TELEMETRY.md)
- **Bring your own key:** we never see your LLM calls. Zero data retention via
  Anthropic's API policy.
- **What's stored:** the graph, embeddings (non-reversible vectors), generated wiki
  pages, git metadata. Raw source is processed transiently and never persisted.
- **Fully offline:** Ollama plus a local embedding model means zero external calls.

Doing a security review? **[docs/business/SECURITY_COMPLIANCE.md →](docs/business/SECURITY_COMPLIANCE.md)**

---

## CLI

```bash
repowise init [PATH]      # index a codebase (one-time; asks, or --no-prose -y needs no LLM)
repowise generate [PATH]  # write wiki pages with a model, on demand (upgrade a keyless wiki)
repowise serve [PATH]     # MCP server + local dashboard
repowise update [PATH]    # incremental update (seconds; --workspace for every repo)
repowise watch            # auto-sync daemon, re-index on file change
repowise search "<q>"     # hybrid search (fulltext / semantic / symbol / path)
repowise ask "<q>"        # a synthesized answer with citations
repowise context <files>  # triage card: layer, hotspot, fix history, freshness
repowise symbol <id>      # one symbol's body, with verified line bounds
repowise why <q|path>     # decisions, rationale, git archaeology
repowise health           # code-health KPIs and lowest-scoring files
repowise risk main..HEAD  # score a branch or PR range for defect risk
repowise risk -t <file>   # what history says about touching a file
repowise impacted-tests   # only the tests a diff actually exercises
repowise dead-code        # unreachable-code report
repowise decision list    # architectural decisions
repowise export --format structurizr  # the architecture as Structurizr DSL, no LLM
repowise distill pytest   # compact, errors-first, reversible command output
repowise saved            # tokens and dollars saved by distillation
repowise workspace add    # multi-repo workspace management
repowise doctor           # check setup, API keys, index drift
```

Every command and flag: **[docs/reference/CLI_REFERENCE.md](docs/reference/CLI_REFERENCE.md)** ·
config: **[docs/reference/CONFIG.md](docs/reference/CONFIG.md)** ·
examples: **[examples/](examples/)**

---

## Contributing

```bash
git clone https://github.com/repowise-dev/repowise
cd repowise
uv sync --all-packages
uv run repowise --version
uv run pytest tests/unit/
```

New here? You do not have to read 3,000 files to start. We keep a public index of this
repo built by repowise itself, re-indexed on every push:
[**explore repowise with repowise →**](https://repowise.dev/repo/repowise-dev/repowise)
(architecture, hotspots, ownership, decisions, and a ranked
[refactoring backlog](https://repowise.dev/repo/repowise-dev/repowise/refactoring) you
are welcome to pick from).

Full guide, including how to add languages and LLM providers:
[CONTRIBUTING.md](.github/CONTRIBUTING.md) · architecture:
[docs/architecture/](docs/architecture/README.md)

---

## License

AGPL-3.0. Free for individuals, teams and companies using repowise internally.

For commercial licensing (the enterprise security and compliance layer, SSO/SCIM, RBAC,
workflow integrations, priority support and SLA, or embedding repowise in a product
without AGPL obligations), see
**[docs/business/COMMERCIAL.md](docs/business/COMMERCIAL.md)** or contact
[hello@repowise.dev](mailto:hello@repowise.dev).

---

<div align="center">

<em>Built for engineers who got tired of watching their AI agent <code>cat</code> the same file for the fourth time.</em>

<p align="center"><sub>⭐ If repowise earns a place in your workflow, <strong>give it a star</strong>. It costs you nothing, and it's the signal that keeps a small team building this in the open.</sub></p>

<p align="center">
  <a href="https://repowise.dev"><strong>repowise.dev</strong></a> ·
  <a href="https://www.repowise.dev/explore"><strong>Explore →</strong></a> ·
  <a href="https://discord.gg/cQVpuDB6rh"><strong>Discord</strong></a> ·
  <a href="https://x.com/repowisedev"><strong>X</strong></a> ·
  <a href="mailto:hello@repowise.dev"><strong>hello@repowise.dev</strong></a>
</p>

</div>
