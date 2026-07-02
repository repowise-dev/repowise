<!-- mcp-name: dev.repowise/repowise -->

<div align="center">

<a href="https://www.repowise.dev"><img src=".github/assets/banner.png" alt="repowise: the codebase intelligence layer for your AI coding agent" width="100%" /></a>

<p align="center"><strong>Five intelligence layers · Nine MCP tools · 15 languages · Multi-repo workspaces · One <code>pip install</code></strong></p>

<p align="center">
  <a href="https://www.repowise.dev"><img src="https://img.shields.io/badge/LIVE_DEMO-repowise.dev-F59520?style=for-the-badge&labelColor=0A0A0A" alt="Live demo: repowise.dev" /></a>
  <a href="https://github.com/repowise-dev/repowise"><img src="https://img.shields.io/badge/Star_this_repo-1E293B?style=for-the-badge&logo=github&logoColor=white&labelColor=0A0A0A" alt="Star repowise on GitHub" /></a>
</p>

<p align="center">
  <a href="https://pypi.org/project/repowise/"><img src="https://img.shields.io/pypi/v/repowise?style=for-the-badge&color=1E293B&labelColor=0A0A0A&logo=pypi&logoColor=white" alt="PyPI version" /></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL--v3-059669?style=for-the-badge&labelColor=0A0A0A" alt="License: AGPL v3" /></a>
  <a href="https://pypi.org/project/repowise/"><img src="https://img.shields.io/badge/Python-3.11%2B-1E293B?style=for-the-badge&labelColor=0A0A0A&logo=python&logoColor=white" alt="Python 3.11+" /></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-1E293B?style=for-the-badge&labelColor=0A0A0A" alt="MCP compatible" /></a>
  <a href="https://github.com/repowise-dev/repowise/stargazers"><img src="https://img.shields.io/github/stars/repowise-dev/repowise?style=for-the-badge&logo=github&color=1E293B&labelColor=0A0A0A&logoColor=white" alt="GitHub stars" /></a>
</p>

<p align="center">
  <a href="https://www.repowise.dev/#contact"><strong>Hosted for teams →</strong></a> ·
  <a href="https://docs.repowise.dev"><strong>Docs</strong></a> ·
  <a href="https://discord.gg/cQVpuDB6rh"><strong>Discord</strong></a> ·
  <a href="mailto:hello@repowise.dev"><strong>Contact</strong></a>
</p>

<p align="center"><sub>
  <a href="#the-five-layers">Layers</a> ·
  <a href="#-code-health-the-layer-nobody-else-nails">Code Health</a> ·
  <a href="#refactoring-intelligence">Refactoring</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="#supported-languages">Languages</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#nine-mcp-tools">MCP tools</a> ·
  <a href="#how-it-compares">Comparison</a> ·
  <a href="#for-teams--enterprises">Hosted</a>
</sub></p>

---

<p align="center">
  <strong>measure, locate, and fix what your AI ships</strong><br/>
  <strong>code health that predicts real bugs</strong> &nbsp;·&nbsp; <strong>ROC AUC 0.74 across 21 repos</strong> &nbsp;·&nbsp; <strong>2.3×</strong> CodeScene's defects under a fixed review budget<br/>
  <strong>graph-aware refactoring plans</strong> your agent can execute &nbsp;·&nbsp; <strong>up to −96% context tokens</strong> &nbsp;·&nbsp; <strong>−70% agent tool calls</strong> at answer-quality parity
</p>

<p align="center"><sub>Measured, reproducible, on public codebases. <a href="#benchmarks">See the benchmarks ↓</a></sub></p>

<img src=".github/assets/demo.gif" alt="repowise demo: Claude Code querying the codebase through repowise's MCP tools, then a tour of the local dashboard" width="100%" />

---

</div>

AI now writes a large and growing share of the code, and the humans accountable
for it have to trust what ships. A score that says *"this file is risky"* isn't
enough: you need to know **where** the risk concentrates and **how** to fix it.

repowise closes that loop. It indexes your codebase once and scores **every file
for defect risk, maintainability, and performance** from 25 deterministic
markers, calibrated against a real defect corpus, no LLM, in under 30 seconds
([the proof ↓](#-code-health-the-layer-nobody-else-nails)). The same index then
**locates** the risk through a real dependency graph and git history, and
**generates the fix**: concrete, graph-aware refactoring plans (split this god
class, move this method, break this dependency cycle, dedup this clone) that
your coding agent can execute.

And because it is all one index, your agent gets the rest for free: **five
intelligence layers**: dependency graph, git history, auto-generated docs,
architectural decisions, and code health, exposed to Claude Code, Codex, and any
MCP-compatible agent through **nine task-shaped tools**. Your agent answers *"why
does auth work this way?"* instead of *"here is what `auth.ts` contains"*, with
fewer tool calls, fewer file reads, and lower cost per query, at comparable
answer quality ([benchmarks ↓](#benchmarks)). One index: context your agent can
use, signals your team can trust, and the fix it can apply.

---

## The five layers

repowise runs once, builds everything, then keeps it in sync on every commit.
Each layer is queryable from the CLI, the MCP tools, and the local dashboard.

| Layer | What it gives you | Edge |
|---|---|---|
| **◈ Graph** | tree-sitter dependency graph across 15 languages · two-tier file + symbol nodes · 3-tier call resolution · Leiden communities · PageRank / centrality / execution flows · framework-aware route→handler edges | A real graph most tools never build |
| **◈ Git** | hotspots (churn × complexity) · ownership % · co-change pairs (hidden coupling) · bus factor · contributor profiles · module health · reviewer suggestions | Behavioral signals static analysis can't see |
| **◈ Docs** | LLM-generated wiki per module/file · incremental on every commit · freshness + confidence scoring · hybrid RAG search (FTS + vector via RRF) · selectable wiki styles (comprehensive / reference / tutorial / caveman) | Stays current, rebuilt every commit |
| **◈ Decisions** | architectural decisions mined from **8 sources**, evidence-backed (verified / fuzzy / unverified), linked to graph nodes, connected by `supersedes`/`refines`/`conflicts_with` edges, tracked for staleness | **★ Captured nowhere else** |
| **★ Code Health** | **25 deterministic markers**, 1–10 per file · **three signals: defect risk · maintainability · performance** · coverage ingestion · trend alerts · **concrete graph-aware refactoring plans** (Extract Class / Helper / Move Method / Break Cycle / Split File) · **zero LLM, <30s** | **★ Defect-validated, with the fix attached. Our edge** |

Full deep-dive on every layer (graph, git, docs, decisions, hooks, auto-sync,
dead code, CLAUDE.md generation): **[docs/INTELLIGENCE_LAYERS.md →](docs/INTELLIGENCE_LAYERS.md)**

---

## ★ Code Health: the layer nobody else nails

Code health is repowise's deepest differentiator: the one layer with no real
equivalent, and **the only one we can prove predicts real bugs**. It runs as a
loop: **measure** every file across three signals, **locate** where the risk
concentrates through the graph and git history, then **fix** it with a concrete
refactoring plan your agent can execute.

<div align="center">
<img src=".github/assets/health-loop.svg" alt="repowise code-health loop: 25 deterministic markers fan into three signals (defect risk, maintainability, performance), the graph and git history locate where risk concentrates, and refactoring intelligence emits concrete plans (Extract Class, Extract Helper, Move Method, Break Cycle, Split File) your agent executes" width="100%" />
</div>

repowise scores **every file 1–10** from **25 deterministic markers**:
McCabe complexity, deep nesting, brain methods, class cohesion (LCOM4), god
classes, native Rabin–Karp clone detection, untested hotspots, function-level
churn, code-age volatility, ownership dispersion, change entropy, co-change
scatter, prior-defect history, test-quality smells, and more.

**Three signals, one index.** The headline 1–10 is **defect risk**: the
defect-calibrated, bug-predictive score in the table below. From the same
marker stream, repowise surfaces two co-equal companion views:
**maintainability** (cohesion, brain methods, DRY and god-class smells that
raise change-cost without predicting bugs) and **performance** (static N+1 /
I/O-in-loop risk, followed across files through the call graph: file-local
linters found 0 of those cross-function cases on a 12k-file benchmark where
repowise surfaced 557). They are separate lenses, never blended into the defect
headline, so the bug-predictive number stays clean.

> **Zero LLM calls. Zero cloud requirement. Zero new runtime dependencies.**
> Pure Python over tree-sitter + git data, finishing in **under 30 seconds** on
> a 3,000-file repo. The marker weights are **calibrated against a real defect
> corpus, not hand-tuned**; only the learned constants ship and the runtime
> stays fully deterministic.

```bash
repowise health                       # KPIs + lowest-scoring files
repowise health --coverage cov.lcov   # ingest LCOV/Cobertura/Clover → untested-hotspot
repowise health --refactoring-targets # ranked by impact / effort
repowise health --trend               # snapshots + declining / predicted-decline alerts
```

And it proves itself on *your* repo, not just a benchmark: after every index,
repowise checks its own flags against your git history and reports the hit rate
in the terminal and on the dashboard: *"16/20 lowest-health files had a bug fix
in the last 6 months, 3.3x the 24% baseline"*. See
[Does the score find the bugs?](docs/CODE_HEALTH.md#does-the-score-find-the-bugs).

**Does the score actually find bugs? Yes, and it out-ranks CodeScene**, the
leading commercial code-health tool. On the **same 2,770 files across 9
languages**, scored at the same leakage-free commit against the same defect
labels:

| Axis (head-to-head, paired tests) | repowise | CodeScene |
|---|---:|---:|
| **Recall @ 20%-of-lines budget** | **0.173** | 0.074 |
| **Effort-aware ranking (Popt)** | **0.607** | 0.462 |
| **Defect density, size-normalized (defects/KLOC, Alert:Healthy)** | **2.18×** | 0.56× |
| Discrimination (ROC AUC) | 0.731 | 0.705 |

Ranking by repowise health surfaces **2.3× the defects under a fixed review
budget** (Popt Δ +0.144, recall Δ +0.098, density Δ all p = 0.003, paired and
significant; the ROC AUC edge is marginal). [Full methodology & CIs →](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)

User guide & per-marker reference: **[docs/CODE_HEALTH.md](docs/CODE_HEALTH.md)**

### Refactoring intelligence

A health score tells you a file is in trouble. Every other tool stops there, or
prints the same static sentence for every god class in every repo. repowise names
the **specific** fix, computed deterministically from the graph, the class model,
and git co-change: **Extract Class**, **Extract Helper**, **Move Method**,
**Break Cycle**, and **Split File**. Each plan names the exact methods, edges, or
symbols that move, and carries its **blast radius** (the callers and co-changing
files that must move with it). Ranking is **graph-aware** (`impact × call-graph
centrality × blast radius`), so a fix on a central hub outranks the same fix on a
leaf. That is the wedge: CodeScene's AI refactoring stays within a single
function, where repowise names the cross-file move and the dependents it ripples
to.

The deterministic plan is the product; an optional LLM step (never in the
indexing path, only on explicit request) expands any plan into generated code
plus a unified diff, fed the graph and co-change context a bare codegen tool
throws away.

```bash
repowise health --refactoring-targets    # ranked plans; get_health(include=["refactoring"]) over MCP
```

The web **Refactoring** tab renders each plan as a card with a **copy-to-agent**
button and the opt-in **Generate code** diff view. Per-detector mechanics:
**[docs/CODE_HEALTH.md](docs/CODE_HEALTH.md#refactoring-targets)** · full
reference: **[docs/REFACTORING.md](docs/REFACTORING.md)**

---

## Change risk & agent provenance

Two more deterministic signals, built on the same graph and git history, for the
people who have to trust what ships:

- **★ Change risk:** score any commit or `base..HEAD` range **0–10** for defect
  risk from the shape of the diff (Kamei-style just-in-time metrics), with
  PR-mode directives (`will_break`, `missing_cochanges`, `missing_tests`). One
  command: `repowise risk main..HEAD`. Reference: **[docs/CHANGE_RISK.md](docs/CHANGE_RISK.md)**.
- **★ Agent provenance:** attribute commits to the AI agents that wrote them,
  straight from git history, so you can see how much of your codebase an agent
  produced and which of that code is a low-health hotspot owned by a single
  person. Risk management for AI-era codebases, not developer surveillance.

Both are zero-LLM and reproducible. Deep dives on the hosted site:
[change risk →](https://www.repowise.dev/features/change-risk) ·
[agent provenance →](https://www.repowise.dev/features/agent-provenance).

---

## Benchmarks

Reproducible, on public codebases. **[repowise-bench →](https://github.com/repowise-dev/repowise-bench)**

### 1 · Agent efficiency: repowise does the exploration once, offline

Most of a coding agent's spend goes to *exploration*: greping for symbols,
reading candidate files, re-reading them as context grows. repowise does that
work once so the agent skips it on every query. Paired SWE-QA runs on real
repositories (same model, same harness, with vs without repowise's MCP tools):

<div align="center">

**up to −96% tokens to load context&nbsp;&nbsp;·&nbsp;&nbsp;−89% file reads&nbsp;&nbsp;·&nbsp;&nbsp;−70% fewer tool calls&nbsp;&nbsp;·&nbsp;&nbsp;answer quality at parity**

</div>

The win is *context*: repowise hands the agent a curated answer instead of a
pile of files to read. Loading a commit's context via `get_context` costs
**2,391 tokens vs 64,039** raw, **~27× fewer (−96%)**. Across the two
benchmarks, agents read **−69% to −89% fewer files** and make **−49% to −70%
fewer tool calls** at answer quality on par with raw exploration; on a long,
multi-step investigation that compounds to **−41% of the context re-read across
the whole session**. Saved tokens are tokens you don't pay for, so dollar cost
drops too, though agent-side prompt caching now mutes the cost delta. Reports: [flask48](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_FLASK48.md) · [flask v3](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_FLASK_V3.md) · [sklearn48](https://github.com/repowise-dev/repowise-bench/blob/master/BENCHMARK_REPORT_SKLEARN48.md)

### 2 · Distill: index-aware output distillation

Most of what an agent reads from a shell command is noise: 300 lines of
passing tests around 4 failures, full commit bodies for "what changed
recently". `repowise distill <cmd>` compresses command output **before the
agent reads it**, errors-first, exit code preserved, and every omission
reversible via an inline `[repowise#<ref>]` marker (`repowise expand <ref>`).
Paired runs on a public OSS repo, per command:

| Command | Raw → distilled tokens | Saved |
|---|---|---:|
| `pytest -q` (11 failures) | 3,374 → 1,317 | **61%**, all 11 failure lines preserved |
| `git log -50` | 3,064 → 331 | **89%** |
| `git diff` (30 commits) | 62,833 → 8,635 | **86%** |

Small outputs pass through untouched (net-positive guard), and in an
end-to-end spot-check the agent reached the identical root-cause diagnosis
from distilled output as from raw. Opt-in Claude Code hook rewrites noisy
commands automatically (shown for approval); `repowise saved` tracks tokens
and dollars saved. Full guide: **[docs/DISTILL.md →](docs/DISTILL.md)**

<div align="center">
<img src=".github/assets/savings.png" alt="repowise Costs dashboard: tokens and dollars saved across distill and MCP tools" width="100%" />
<p align="center"><sub>The <strong>Costs</strong> dashboard tallies both savings surfaces: <code>repowise distill</code> (command output) and the MCP tools (each curated answer replacing the raw file reads it stood in for), priced at your coding agent's own model. Example shown from a week of heavy local use.</sub></p>
</div>

### 3 · Code health predicts real defects

Health scores are collected at a historical commit (T0); bug-fixing commits are
counted over the following 6 months; the two are correlated, with strictly no
leakage. Across **21 open-source repositories spanning all 9 Full-tier
languages**:

- **Cross-project mean ROC AUC 0.74** [95% CI 0.68–0.79] at identifying the files
  that go on to receive bug-fixes, up to **0.90** on individual repos.
- **Survives controlling for file size** (partial Spearman ρ = −0.16), so it is
  not just "flag the big files."
- **Significantly out-discriminates** recent churn (+0.10 AUC) and prior-defect
  history (+0.12 AUC), DeLong p < 1e-9.
- Holds up on an **external published dataset it has never seen** (PROMISE/jEdit
  CK-metrics: AUC 0.76–0.78, within ~0.03 of the dataset's own tuned model).

Full report: **[health-defect/BENCHMARK_REPORT.md →](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md)**

<div align="center">
<sub>⭐ <strong>Star the repo</strong> if repowise just saved your agent a few greps, it helps the next engineer find it and tells us to keep building.</sub>
</div>

---

## Local dashboard

`repowise serve` starts a full web UI alongside the MCP server, no separate
setup.

<img src=".github/assets/webui.gif" alt="repowise local dashboard: Overview, Knowledge Graph, Code Health map, Commits, Chat, and By the Numbers" width="100%" />

Highlights: **Chat** (natural-language Q&A) · **Docs** (wiki with Mermaid +
graph sidebar) · **Graph** (interactive, 2,000+ nodes, community coloring, path
finder) · **C4 Architecture** (Context → Containers → Components) · **Risk**
(hotspots, ownership heatmap, module health, dead code, blast radius) ·
**Contributors** (per-author profiles) · **Decisions** (evidence drawer,
evolution timeline, decision-graph) · **Health** (three signals: defect,
maintainability, performance; coverage, trends) · **Refactoring** (ranked plan
cards, blast radius, copy-to-agent, opt-in code-gen diff) · **Security** (local
pattern scan) · **Costs** · **Workspace**
(cross-repo contracts & co-changes). Full view-by-view list in
[docs/USER_GUIDE.md](docs/USER_GUIDE.md).

---

## VS Code extension

The **Repowise** extension puts the index where code gets written: inline health
diagnostics and gutter heat on the files you open, refactoring plans as CodeLens,
branch risk before you push, and the same dashboards (health, architecture,
knowledge graph, decisions, docs) inside the editor. One install also registers
the Repowise MCP server with VS Code, so the same local index serves both you and
your AI agent.

Install from the Marketplace (search **Repowise**) or Open VSX, then run
**Repowise: Set Up This Repository**. Full guide in
[docs/VSCODE.md →](docs/VSCODE.md).

---

## Supported languages

**15 languages parsed to AST · 9 at the Full tier · framework-aware across all of them.**

<p>
  <strong>Full tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black" alt="JavaScript" />
  <img src="https://img.shields.io/badge/Java-ED8B00?style=flat-square&logo=openjdk&logoColor=white" alt="Java" />
  <img src="https://img.shields.io/badge/Kotlin-7F52FF?style=flat-square&logo=kotlin&logoColor=white" alt="Kotlin" />
  <img src="https://img.shields.io/badge/Go-00ADD8?style=flat-square&logo=go&logoColor=white" alt="Go" />
  <img src="https://img.shields.io/badge/Rust-000000?style=flat-square&logo=rust&logoColor=white" alt="Rust" />
  <img src="https://img.shields.io/badge/C++-00599C?style=flat-square&logo=cplusplus&logoColor=white" alt="C++" />
  <img src="https://img.shields.io/badge/C%23-512BD4?style=flat-square&logo=csharp&logoColor=white" alt="C#" />
</p>
<p>
  <strong>Good tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/C-A8B9CC?style=flat-square&logo=c&logoColor=black" alt="C" />
  <img src="https://img.shields.io/badge/Ruby-CC342D?style=flat-square&logo=ruby&logoColor=white" alt="Ruby" />
  <img src="https://img.shields.io/badge/Swift-F05138?style=flat-square&logo=swift&logoColor=white" alt="Swift" />
  <img src="https://img.shields.io/badge/Scala-DC322F?style=flat-square&logo=scala&logoColor=white" alt="Scala" />
  <img src="https://img.shields.io/badge/PHP-777BB4?style=flat-square&logo=php&logoColor=white" alt="PHP" />
  &nbsp;<strong>· Partial &nbsp;</strong>
  <img src="https://img.shields.io/badge/Luau-00A2FF?style=flat-square&logo=lua&logoColor=white" alt="Luau" />
</p>

| Tier | Languages | What works |
|------|-----------|------------|
| **Full** | Python · TypeScript · JavaScript · Java · Kotlin · Go · Rust · C++ · C# | AST parsing, import resolution, named bindings, call resolution, heritage extraction, docstrings; multi-project workspace resolvers; framework-aware edges; per-language dynamic-hint extractors; **code-health markers** |
| **Good** | C · Ruby · Swift · Scala · PHP | AST parsing, import resolution, named bindings, call resolution, heritage (mixins / derive / extensions / traits), docstrings; dedicated workspace-aware resolvers; Rails / Laravel / TYPO3 framework edges; dynamic-hint extractors |
| **Config / data** | OpenAPI · Protobuf · GraphQL · Dockerfile · Makefile · YAML · JSON · TOML · SQL · Terraform · Markdown · Shell | Included in the file tree; special handlers extract endpoints / targets where applicable |
| **Git-blame only** | Objective-C · Elixir · Erlang · Dart · Zig · Julia · Clojure · Haskell · OCaml · F# · … | Tracked in git history (blame, hotspots, co-change); no AST parsing yet |

Adding a language needs **one `.scm` query file and one config entry**, with no
changes to the parser core. Full per-language matrix, code-health checklist, and
the contributor recipe: **[docs/LANGUAGE_SUPPORT.md →](docs/LANGUAGE_SUPPORT.md)**

---

## Who it's for

| | Start here |
|---|---|
| **Individual developers** | `pip install repowise` → `repowise init` → query from Claude Code, Cursor, or any MCP agent. 100% local, BYO API key, free under AGPL-3.0. [For developers →](https://www.repowise.dev/for/developers) |
| **Team leads** | Know which PRs to worry about before you merge: change-risk scoring plus the free [**Repowise PR Bot**](https://github.com/apps/repowise-bot) that posts one deterministic comment per PR (hotspots, hidden coupling, declining health), zero LLM. [For team leads →](https://www.repowise.dev/for/teams) |
| **Engineering leaders** | See how much of your code AI wrote and whether it is healthy: agent provenance, code-health trends, and bus factor, from git history. [For engineering leaders →](https://www.repowise.dev/for/engineering-leaders) |
| **Security & compliance** | Reachability-aware CVE triage, secret detection across full git history, and SBOM, on your real dependency graph. [For security →](https://www.repowise.dev/for/security) |
| **Enterprises** | On-prem / air-gapped, SSO/SCIM, commercial licensing (no AGPL obligation), and IP indemnification. [For enterprise →](https://www.repowise.dev/for/enterprise) · [docs/COMMERCIAL.md](docs/COMMERCIAL.md) |

---

## Quickstart

```bash
pip install repowise          # or: uv tool install repowise
```

### Single repo

```bash
cd your-project
repowise init        # builds all five intelligence layers (one-time)
repowise serve       # starts MCP server + local dashboard
```

### Multi-repo workspace

```bash
cd my-workspace/     # parent dir containing backend/, frontend/, shared-libs/
repowise init .      # scans for git repos, indexes each, runs cross-repo analysis
repowise serve       # workspace dashboard, Live System Map + per-repo pages
```

The workspace **Live System Map** renders your services and their typed
relationships (HTTP / gRPC / events / package deps / co-change) as a
code-derived, always-current diagram, health-colored, filterable, with
drill-down to the underlying contracts. See
[Workspaces](docs/WORKSPACES.md#live-system-map).

`repowise init` automatically registers the MCP server, installs a PostToolUse
hook in `~/.claude/settings.json`, generates `.mcp.json` at the project root, and
offers a post-commit hook that keeps everything in sync. If the Codex CLI is
installed and logged in, interactive runs also offer to write project-local
`.codex/config.toml`, `.codex/hooks.json`, and a managed `AGENTS.md`;
non-interactive runs require `--codex`. Skip Codex setup with `--no-codex`; force or
skip `AGENTS.md` with `--agents` / `--no-agents`.

**Claude Code plugin.** Prefer a one-command setup? Install the plugin from the
marketplace: it registers the MCP server and hook and adds `/repowise:*` slash
commands (`init`, `health`, `risk`, `dead-code`, `decision`, …):

```text
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise
```

To add the MCP server to another editor manually:

```json
{
  "mcpServers": {
    "repowise": { "command": "repowise", "args": ["mcp", "/path/to/your/project"] }
  }
}
```

> **Init time:** the graph, git, dead-code, and code-health layers build in
> minutes with **zero LLM calls**; run `repowise init --index-only` for a
> queryable index almost immediately. The one-time cost is the documentation
> layer (LLM-generated wiki pages, can run in the background). After that, every
> commit-triggered update takes **under 30 seconds** and only regenerates the
> pages your change touched.

**Docs:** [Quickstart](docs/QUICKSTART.md) · [User Guide](docs/USER_GUIDE.md) · [CLI Reference](docs/CLI_REFERENCE.md) · [Codex](docs/CODEX.md) · [MCP Tools](docs/MCP_TOOLS.md) · [Distill](docs/DISTILL.md) · [Workspaces](docs/WORKSPACES.md) · [Auto-Sync](docs/AUTO_SYNC.md) · [Upgrading](docs/UPGRADING.md) · [Config](docs/CONFIG.md)

---

## Nine MCP tools

Most tools are designed around data entities (one module, one file, one symbol),
forcing agents into long chains of sequential calls. repowise tools are designed
around **tasks**: pass multiple targets in one call, get complete context back.
Every response carries an `_meta` envelope with `index_age_days`,
`indexed_commit`, and a `stale_warning` that fires only when the indexed HEAD
diverges from live `.git/HEAD`.

| Tool | What only this tool answers |
|---|---|
| `get_overview()` | Architecture summary, module map, entry points, git health, community summary. First call on any unfamiliar codebase. |
| `get_answer(question)` | Hybrid retrieval (FTS + vector via RRF) + PageRank bias + 1-hop graph expansion → a cited answer with calibrated `retrieval_quality`. Returns structured `best_guesses` on low confidence. Collapses search → read → reason into one round-trip. |
| `get_context(targets, include?)` | Triage card for files / modules / symbols: title, summary, signatures, `hotspot` bit, `governing_decisions`, and `symbol_id`s. `include` opens callers/callees, ownership, metrics, decisions, full_doc. Batch many targets. |
| `get_symbol("file.py::Name")` | Raw source bytes for one indexed symbol with exact line bounds, cheaper and safer than `Read` + offset math. |
| `search_codebase(query, kind?)` | Semantic search over the wiki, filterable by `kind` (implementation / test / config / doc), tagging each result's `search_method`. |
| `get_risk(targets, changed_files?)` | Hotspot scores, dependents, co-change partners, ownership, test gaps, security signals. Pass `changed_files` for PR mode → a `directive` block (`will_break`, `missing_cochanges`, `missing_tests`, `governance_risk`). |
| `get_why(query?, targets?)` | Architectural decision records, status, evidence spans, and the supersession **lineage chain**. Falls back to git archaeology when no ADRs exist. |
| `get_dead_code(...)` | Unreachable code by confidence tier with cleanup-impact estimates; cross-repo consumer detection in workspace mode. |
| `get_health(targets?, include?)` | Marker scores per file across three signals (defect · maintainability · performance). Dashboard mode → KPIs + lowest-scoring files + module rollup; targeted mode → per-file findings. Self-check before a PR via `include`: `accuracy` (does the score find the bugs), `signals` (per-file churn / owners / prior defects), `churn_complexity`, a dimension name to filter findings, plus `coverage`, `trend`, and `refactoring` → **structured, graph-aware refactoring plans** (split groups, move target, cut edges + blast radius), not template strings. |

Worked example (*"Add rate limiting to all API endpoints"* in 5 calls instead of
~30 greps+reads) and the full reference: **[docs/MCP_TOOLS.md →](docs/MCP_TOOLS.md)**

---

## How it compares

| | repowise | Google Code Wiki | DeepWiki | Swimm | CodeScene |
|---|---|---|---|---|---|
| Self-hostable, open source | ✅ AGPL-3.0 | ❌ cloud only | ❌ cloud only | ❌ Enterprise only | ✅ Docker |
| Private repo, no cloud | ✅ | ❌ in development | ❌ OSS forks only | ✅ Enterprise tier | ✅ |
| Auto-generated documentation | ✅ | ✅ Gemini | ✅ | ✅ PR2Doc | ❌ |
| MCP server for AI agents | ✅ 9 tools | ❌ | ✅ 3 tools | ✅ | ✅ |
| Proactive agent hooks | ✅ Claude + Codex hooks | ❌ | ❌ | ❌ | ❌ |
| Auto-generated AI instructions (`CLAUDE.md`, `AGENTS.md`) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Code health score (1–10) | ✅ 25 markers | ❌ | ❌ | ❌ | ✅ 25–30 |
| Brain Method / LCOM4 / god class | ✅ | ❌ | ❌ | ❌ | ✅ |
| Test-coverage intelligence | ✅ LCOV/Cobertura/Clover | ❌ | ❌ | ❌ | ❌ |
| Untested-hotspot detection | ✅ coverage × hotspot | ❌ | ❌ | ❌ | ❌ |
| Health trend + declining alerts | ✅ rolling snapshots | ❌ | ❌ | ❌ | ✅ |
| Refactoring recommendations | ✅ deterministic | ❌ | ❌ | ❌ | ✅ |
| Concrete cross-file refactoring plans (Extract Class / Move Method / Break Cycle) | ✅ graph-aware + blast radius | ❌ | ❌ | ❌ | ⚠️ within-function only |
| Git intelligence (hotspots, ownership, co-change) | ✅ | ❌ | ❌ | ❌ | ✅ |
| Bus factor analysis | ✅ | ❌ | ❌ | ❌ | ✅ |
| Dead code detection | ✅ | ❌ | ❌ | ❌ | ❌ |
| Architectural decision records | ✅ | ❌ | ❌ | ❌ | ❌ |
| Multi-repo workspace intelligence | ✅ co-changes, contracts, federated MCP | ❌ | ❌ | ❌ | ❌ |
| Local dashboard | ✅ | ❌ | ❌ | ❌ IDE only | ✅ |

**repowise is the intersection:** behavioral git intelligence + a defect-validated
code-health score *with the graph-aware fix attached* + auto-generated docs +
agent-native MCP + architectural decisions + multi-repo workspace intelligence,
self-hostable and open source.
Full side-by-side comparisons (CodeScene, DeepWiki, Sourcegraph, Cursor, GitClear):
**[repowise.dev/compare →](https://www.repowise.dev/compare)**.

---

## For teams & enterprises

[**repowise.dev**](https://www.repowise.dev) is the same engine, fully managed,
at feature parity with self-hosted: every CLI command, every MCP tool, the full
dashboard. We dogfood it on our own codebase: [live snapshot →](https://www.repowise.dev/s/5a6b93fa9a69) · [explore public repos →](https://www.repowise.dev/explore).

**On top of self-hosting:**
- **Zero ops**: managed deploys & webhooks, auto re-index on every commit.
- **Hosted MCP endpoint**: point any MCP client at one URL, no local server.
- **Repowise PR Bot**: free GitHub App, one deterministic comment per PR
  (hotspot touches, hidden coupling, declining health, dead code), zero LLM calls.
  [Install →](https://github.com/apps/repowise-bot) · [Learn more →](https://www.repowise.dev/bot)
- **CVE-aware security layer**, **cross-repo intelligence at scale**, and
  **integrations** (Slack, Jira/Linear, Confluence/Notion, PagerDuty) *(rolling out)*.

What's GA / in development / planned, on-prem topology, SSO/SCIM/RBAC, and
pricing: **[docs/COMMERCIAL.md](docs/COMMERCIAL.md)** · [Get in touch →](https://www.repowise.dev/#contact)

---

## Privacy

- **Self-hosted:** your code never leaves your infrastructure, so no code, file paths, or repo names are ever sent. The CLI does report **anonymous, opt-out** usage telemetry (command names + coarse environment only) to help us prioritize; turn it off with `repowise telemetry disable`, `DO_NOT_TRACK=1`, or by running fully offline. [What's collected →](docs/TELEMETRY.md)
- **BYOK:** bring your own Anthropic / OpenAI key. We never see your LLM calls. Zero data retention via Anthropic's API policy.
- **What's stored:** the NetworkX graph, LanceDB embeddings (non-reversible vectors), generated wiki pages, git metadata. Raw source is processed transiently and never persisted.
- **Fully offline:** Ollama + a local embedding model = zero external API calls.

---

## CLI & configuration

```bash
repowise init [PATH]      # index codebase (one-time; --index-only skips LLM)
repowise serve [PATH]     # MCP server + local dashboard
repowise update [PATH]    # incremental update (<30s; --workspace for all repos)
repowise query "<q>"      # ask anything from the terminal
repowise health           # code-health KPIs + lowest-scoring files
repowise risk main..HEAD  # score a branch / PR range for defect risk
repowise dead-code        # unreachable-code report
repowise distill pytest   # compact errors-first output (reversible), saves 60–90% tokens
repowise saved            # tokens & dollars saved by distillation
repowise doctor           # check setup, API keys, store drift
```

`repowise init` generates `.repowise/config.yaml` (provider, model, embedder,
reasoning mode, exclude patterns, git commit depth). Full command set:
**[docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md)** · config reference:
**[docs/CONFIG.md](docs/CONFIG.md)**.

---

## Contributing

```bash
git clone https://github.com/repowise-dev/repowise
cd repowise
uv sync --all-packages
uv run repowise --version
uv run pytest tests/unit/
```

Full guide, including how to add languages and LLM providers:
[CONTRIBUTING.md](.github/CONTRIBUTING.md).

---

## License

AGPL-3.0. Free for individuals, teams, and companies using repowise internally.

For commercial licensing (the enterprise security & compliance layer, SSO/SCIM,
RBAC, workflow integrations, priority support and SLA, or embedding repowise in a
product without AGPL obligations), see **[docs/COMMERCIAL.md](docs/COMMERCIAL.md)**
or contact [hello@repowise.dev](mailto:hello@repowise.dev).

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
