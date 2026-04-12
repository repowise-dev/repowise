<div align="center">

<img src=".github/assets/logo.png" width="280" alt="repowise" /><br />
**Codebase intelligence for AI-assisted engineering teams.**

Four intelligence layers. Seven MCP tools. Proactive hooks. One `pip install`.

[![PyPI version](https://img.shields.io/pypi/v/repowise?color=F59520&labelColor=0A0A0A)](https://pypi.org/project/repowise/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--v3-F59520?labelColor=0A0A0A)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/python-3.11%2B-F59520?labelColor=0A0A0A)](https://pypi.org/project/repowise/)
[![MCP](https://img.shields.io/badge/MCP-compatible-F59520?labelColor=0A0A0A)](https://modelcontextprotocol.io)
[![Stars](https://img.shields.io/github/stars/repowise-dev/repowise?color=F59520&labelColor=0A0A0A)](https://github.com/repowise-dev/repowise)

[**Live Demo →**](https://repowise.dev/examples) · [**Hosted for teams**](https://www.repowise.dev/#contact) · [**Docs**](https://repowise-dev.github.io) · [**Discord**](https://discord.gg/cQVpuDB6rh) · [**Contact**](mailto:hello@repowise.dev)

---

<img src=".github/assets/demo.gif" alt="repowise demo — repowise init → Claude Code querying via MCP tools" width="100%" />

---

</div>

Your AI coding agent reads files. It does not know who owns them, which ones change together, which ones are dead, or why they were built the way they were. It has the source code and zero institutional knowledge.

repowise fixes that. It indexes your codebase into four intelligence layers — dependency graph, git history, auto-generated documentation, and architectural decisions — and exposes them to Claude Code (and any MCP-compatible AI agent) through seven precisely designed tools. **27× fewer tokens per query. 36% cheaper. Same answer quality.**

The result: your agent answers *"why does auth work this way?"* instead of *"here is what auth.ts contains."*

---

## 🏆 Benchmarked against frontier LLMs

> **[repowise-bench →](https://github.com/repowise-dev/repowise-bench)** — an open SWE-QA benchmark that grades how well standard LLMs answer real software-engineering questions over real repositories.
>
> On 48 paired tasks from `pallets/flask` (`claude-sonnet-4-6`, end-to-end), repowise-augmented Claude Code matches baseline answer quality while being dramatically leaner:
>
> | Metric (per task, mean) | Baseline | **+ repowise** | Δ |
> |---|---|---|---|
> | 💰 Cost | $0.1396 | **$0.0890** | **−36 %** |
> | ⚡ Wall time | 41.7 s | **33.9 s** | **−19 %** |
> | 🛠️ Tool calls | 7.4 | **3.8** | **−49 %** |
> | 📄 Files read | 1.9 | **0.2** | **−89 %** |
>
> **32 / 48 (67 %)** tasks are cheaper with repowise — at parity quality (judge Δ ≈ −0.01).

### Token efficiency — because context windows aren't free

There's a small genre of "token efficiency" benchmarks going around. It would be impolite not to contribute one. Ours runs on the 30 most recent non-merge commits of `pallets/flask` and asks one question: *to understand a commit, how many tokens does each strategy ask the model to read?*

| Strategy | Tokens / commit |
|---|---|
| Naive (full contents of changed files) | 64,039 |
| `git diff` only | 14,888 |
| **`repowise get_context`** | **2,391** |

**209× less than naive (mean), 26.8× pooled, 1,214× best case. 41.7× less than git diff (mean), 6.2× pooled.** Same file list, same tokenizer (`cl100k_base`), no per-strategy fudge. We report mean, pooled, and median together because picking just one would be the kind of thing other people in this genre seem to do.

> Full methodology, per-task tables, and the actual SWE-QA evaluation (which has third-party ground truth and an independently-scored LLM judge — unlike this sanity-check): **[repowise-bench →](https://github.com/repowise-dev/repowise-bench)**

---

## What repowise builds

repowise runs once, builds everything, then keeps it in sync on every commit.

### ◈ Graph Intelligence
tree-sitter parses every file into symbols. NetworkX builds a two-tier dependency graph — file nodes for module-level relationships and symbol nodes (functions, classes, methods) for fine-grained call resolution. A 3-tier resolver links call sites to their targets with confidence scoring. Import aliases, barrel re-exports, and namespace imports all resolve correctly. Inheritance hierarchies are extracted across 11 languages (extends, implements, trait impls, mixins) and resolved to concrete symbol edges. Leiden community detection identifies logical modules at both file and symbol level — with cohesion scoring and heuristic labeling — even when your directory structure doesn't reflect them. Execution flow tracing discovers call paths from entry points through your codebase, classifying them as intra- or cross-community. PageRank, betweenness centrality, and SCC analysis identify your most central and most coupled code.

### ◈ Git Intelligence
500 commits of history turned into signals: hotspot files (high churn × high complexity), ownership percentages per engineer, co-change pairs (files that change together without an import link — hidden coupling), and significant commit messages that explain *why* code evolved.

### ◈ Documentation Intelligence
An LLM-generated wiki for every module and file, rebuilt incrementally on every commit. Coverage tracking. Freshness scoring per page. Semantic search via RAG. Confidence scores show how current each page is relative to the underlying code.

### ◈ Decision Intelligence
The layer nobody else has. Architectural decisions captured from git history, inline markers, and explicit CLI — linked to the graph nodes they govern, tracked for staleness as code evolves.

```python
# WHY: JWT chosen over sessions — API must be stateless for k8s horizontal scaling
# DECISION: All external API calls wrapped in CircuitBreaker after payment provider outages
# TRADEOFF: Accepted eventual consistency in preferences for write throughput
```

These become structured decision records, queryable by Claude Code via `get_why()`.

---

## Quickstart

```bash
pip install repowise
```

```bash
cd your-project
repowise init        # builds all four intelligence layers (~25 min first time)
repowise serve       # starts MCP server + local dashboard
```

That's it. `repowise init` automatically registers the MCP server, installs PreToolUse/PostToolUse hooks in `~/.claude/settings.json`, and generates `.mcp.json` at the project root.

To manually add the MCP server to another editor:

```json
{
  "mcpServers": {
    "repowise": {
      "command": "repowise",
      "args": ["mcp", "/path/to/your/project"]
    }
  }
}
```

> **Note on init time:** Initial indexing analyzes your entire codebase — AST parsing, 500-commit git history, LLM doc generation, embedding indexing, and decision archaeology. This is a one-time cost (~25 minutes for a 3,000-file project). Every subsequent update after a commit takes under 30 seconds.

---

## Seven MCP tools

Most tools are designed around data entities — one module, one file, one symbol — which forces AI agents into long chains of sequential calls. repowise tools are designed around **tasks**. Pass multiple targets in one call. Get complete context back.

| Tool | What it answers | When Claude Code calls it |
|---|---|---|
| `get_overview()` | Architecture summary, module map, entry points, git health, community summary | First call on any unfamiliar codebase |
| `get_answer(question)` | One-call RAG: retrieves over the wiki, gates on confidence, and synthesizes a cited 2–5 sentence answer. High-confidence answers cite directly; ambiguous queries return ranked excerpts. | First call on any code question — collapses search → read → reason into one round-trip |
| `get_context(targets, include?)` | The workhorse. Docs, symbols, ownership, freshness for any targets. `include` options: `"source"` (symbol body), `"callers"`/`"callees"` (call graph), `"metrics"` (PageRank, centrality), `"community"` (cluster membership). Batch multiple targets. | Before reading or modifying code. Pass all relevant targets in one call. |
| `search_codebase(query)` | Semantic search over the full wiki. Natural language. | When `get_answer` returned low confidence and you need to discover candidate pages by topic |
| `get_risk(targets?, changed_files?)` | Hotspot scores, dependents, co-change partners, blast radius, recommended reviewers, test gaps, security signals, 0–10 risk score | Before modifying files — understand what could break |
| `get_why(query?)` | Three modes: NL search over decisions · path-based decisions for a file · no-arg health dashboard | Before architectural changes — understand existing intent |
| `get_dead_code(min_confidence?, include_internals?)` | Unreachable code sorted by confidence tier with cleanup impact estimates | Cleanup tasks |

### Tool call comparison — a real task

*"Add rate limiting to all API endpoints."*

| Approach | Tool calls | Time to first change | What it misses |
|---|---|---|---|
| Claude Code alone (no MCP) | grep + read ~30 files | ~8 min | Ownership, prior decisions, hidden coupling |
| **repowise (7 tools)** | **5 calls** | **~2 min** | **Nothing** |

The 5 calls for that task:

```python
get_overview()                                         # orient: understand the architecture
get_context(["middleware", "api/routes", "payments"])  # understand 3 modules at once
get_risk(["middleware/auth.ts"])                       # assess: 47 dependents, co-changes
get_why("rate limiting")                               # check: any prior decision?
search_codebase("rate limit OR throttle OR retry")     # find: any prior implementation?
```

---

## How Claude Code uses it

```
User: Implement rate limiting on all API endpoints

Claude Code:
→ get_overview()
  "Express API. Entry points in api/routes/. Middleware in middleware/."

→ get_context(["middleware", "api/routes", "payments"])
  middleware/: existing chain is cors → auth → routes. Owner: @alex.
  api/routes/: 23 route files. No existing rate limiting.
  payments/: Owner @sarah (71%). Decision: all side effects must be idempotent.

→ get_why("rate limiting")
  "No prior decision found. No prior implementation detected."

→ get_risk(["middleware/auth.ts"])
  "47 files import this. Co-changes with all 4 service listeners.
   Risk summary: any interface change here touches 47 dependents."

→ search_codebase("rate limit throttle retry")
  "Found: payments/retry.ts already has RetryQueue class.
   Found: payments/middleware.ts has idempotency key middleware."

Implementing rate-limiting middleware, inserting after cors, before auth.
Will also update tests/middleware.test.ts — detected as historical co-change partner.
Flagging payments/ for @sarah review — hotspot, high ownership concentration.
```

This is what happens when an AI agent has real codebase intelligence.

---

## Local dashboard

`repowise serve` starts a full web UI alongside the MCP server. No separate setup — browse your codebase intelligence directly in the browser.

<img src=".github/assets/webui.gif" alt="repowise web UI" width="100%" />

| View | What it shows |
|---|---|
| **Chat** | Ask anything about your codebase in natural language |
| **Docs** | AI-generated wiki with syntax highlighting, Mermaid diagrams, and a graph intelligence sidebar showing PageRank/betweenness percentiles, community membership, and degree |
| **Graph** | Interactive dependency graph — handles 2,000+ nodes. Community color mode with real labels, community detail panel on click, path finder |
| **Search** | Full-text and semantic search with global command palette (Ctrl+K) |
| **Symbols** | Searchable index of every function, class, and method. Click any symbol for graph metrics, callers/callees, and class heritage |
| **Coverage** | Doc freshness per file with one-click regeneration |
| **Ownership** | Contributor attribution and bus factor risk |
| **Hotspots** | Ranked by trend-weighted score (180-day decay) and churn |
| **Dead Code** | Unused code with confidence scores and bulk actions |
| **Decisions** | Architectural decisions with staleness monitoring |
| **Costs** | LLM spend by day, model, or operation, with running session totals |
| **Blast Radius** | Paste a PR file list, see transitive impact, reviewers, and test gaps |
| **Knowledge Map** | Top owners, bus-factor silos, and onboarding targets on the dashboard |
| **Graph Intelligence** | Architecture communities with expandable detail, execution flows with call traces, community coupling analysis — all on the overview dashboard |
| **System Health** | SQL/vector/graph drift status from the atomic store coordinator |

---

## Proactive context enrichment — hooks

Most MCP tools are passive — the agent has to know to call them. repowise hooks are **active**. They inject graph context into every search automatically, so agents are smarter even when they don't explicitly ask for help.

### PreToolUse — every search gets graph context

When your AI agent runs `Grep` or `Glob`, repowise intercepts the call and enriches it with:

- **Matching files** from the wiki's full-text search index
- **Symbols** — functions, classes, and methods in those files
- **Importers** — who depends on this file (callers)
- **Dependencies** — what this file imports (callees)
- **Git signals** — hotspot status, bus factor, primary owner

Average latency: **24ms**. No LLM calls. No network. Pure local SQLite queries.

```
[repowise] 2 related file(s) found:

  src/auth/middleware.ts
    Symbols: class:AuthMiddleware, function:validateToken, function:refreshSession
    Imported by: api/routes/index.ts, api/routes/payments.ts, api/routes/admin.ts
    Depends on: auth/jwt.ts, db/sessions.ts, config/auth.ts
    Git: HOTSPOT, bus-factor=1, owner=@alex
```

### PostToolUse — auto-detect stale wiki

After a successful `git commit`, repowise checks whether the wiki is out of date and notifies the agent:

```
[repowise] Wiki is stale — last indexed at commit a1b2c3d4, HEAD is now f9a0499b.
Run `repowise update` to refresh documentation and graph context.
```

Hooks are installed automatically during `repowise init`. No manual configuration needed.

---

## Auto-generated CLAUDE.md

After every `repowise init` and `repowise update`, repowise regenerates your `CLAUDE.md` from actual codebase intelligence — not a template. No LLM calls. Under 5 seconds.

```bash
repowise generate-claude-md
```

The generated section includes: architecture summary, module map, hotspot warnings, ownership map, hidden coupling pairs, active architectural decisions, and dead code candidates. A user-owned section at the top is never touched.

```markdown
<!-- REPOWISE:START — managed automatically, do not edit -->
## Architecture
Monorepo with 4 packages. Entry points: api/server.ts, cli/index.ts.

## Hotspots — handle with care
- payments/processor.ts — 47 commits/month, high complexity, primary owner: @sarah
- shared/events/EventBus.ts — 23 dependents, co-changes with all service listeners

## Active architectural decisions
- JWT over sessions (auth/service.ts) — stateless required for k8s horizontal scaling
- CircuitBreaker on all external calls — after payment provider outages in Q3 2024

## Hidden coupling (no import link, but change together)
- auth.ts ↔ middleware/session.ts — co-changed 31 times in last 500 commits
<!-- REPOWISE:END -->
```

---

## Git intelligence

repowise mines your last 500 commits (configurable) to produce signals no static analysis can find.

**Hotspots** — files in the top 25% of both churn and complexity. These are where bugs live. Flagged in the dashboard, in CLAUDE.md, and surfaced by `get_risk()` before Claude Code touches them.

**Ownership** — `git blame` aggregated into ownership percentages per engineer. Know who to ping. Know where knowledge silos exist.

**Co-change pairs** — files that change together in the same commit without an import link. Hidden coupling that AST parsing cannot detect. `get_context()` surfaces co-change partners alongside direct dependencies.

**Bus factor** — files owned >80% by a single engineer. Shown in the ownership view. Surfaced in CLAUDE.md as knowledge risk.

**Significant commits** — the last 10 meaningful commit messages per file (filtered: no merges, no dependency bumps, no lint) are included in generation prompts. The LLM explains *why* code is structured the way it is.

---

## Dead code detection

Pure graph traversal and SQL. No LLM calls. Completes in under 10 seconds for any repo size.

```
repowise dead-code

  23 findings · 4 safe to delete

  ✓ utils/legacy_parser.ts          file      1.00   safe to delete
  ✓ auth/session.ts                 file      0.92   safe to delete
  ✓ helpers/formatDate              export    0.71   safe to delete
  ✓ types/OldUser                   export    0.68   safe to delete
  ✗ analytics/v1/tracker.ts         file      0.41   recent activity — review first
```

Conservative by design. `safe_to_delete` requires confidence ≥ 0.70 and excludes dynamically-loaded patterns (`*Plugin`, `*Handler`, `*Adapter`, `*Middleware`). Dynamic import detection (`importlib.import_module()`, `__import__()`) and framework decorator awareness (Flask/FastAPI/Django routes) further reduce false positives. repowise surfaces candidates. Engineers decide.

---

## Architectural decisions

```bash
repowise decision add              # guided interactive capture (~90 seconds)
repowise decision confirm          # review auto-proposed decisions from git history
repowise decision health           # stale, conflicting, ungoverned hotspots
```

```
repowise decision health

  2 stale decisions
    → "JWT over sessions" — auth/service.ts rewritten 3 months ago, decision may be outdated
    → "EventBus in-process only" — 8 of 14 governed files changed since recorded

  1 conflict
    → payments/: two decisions with overlapping scope and contradictory rationale

  1 ungoverned hotspot
    → payments/processor.ts — 47 commits/month, no architectural decisions recorded
```

Decisions are linked to graph nodes, tracked for staleness as code evolves, and surfaced by `get_why()` whenever Claude Code touches governed files.

When a senior engineer leaves, the "why" usually leaves with them. Decision intelligence keeps it in the codebase.

---

## How it compares

| | repowise | Google Code Wiki | DeepWiki | Swimm | CodeScene |
|---|---|---|---|---|---|
| Self-hostable, open source | ✅ AGPL-3.0 | ❌ cloud only | ❌ cloud only | ❌ Enterprise only | ✅ Docker |
| Auto-generated documentation | ✅ | ✅ Gemini | ✅ | ✅ PR2Doc | ❌ |
| Private repo — no cloud | ✅ | ❌ in development | ❌ OSS forks only | ✅ Enterprise tier | ✅ |
| Dead code detection | ✅ | ❌ | ❌ | ❌ | ❌ |
| Git intelligence (hotspots, ownership, co-changes) | ✅ | ❌ | ❌ | ❌ | ✅ |
| Bus factor analysis | ✅ | ❌ | ❌ | ❌ | ✅ |
| Architectural decision records | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP server for AI agents | ✅ 7 tools | ❌ | ✅ 3 tools | ✅ | ✅ |
| Proactive agent hooks | ✅ PreToolUse + PostToolUse | ❌ | ❌ | ❌ | ❌ |
| Auto-generated CLAUDE.md | ✅ | ❌ | ❌ | ❌ | ❌ |
| Doc freshness scoring | ✅ | ❌ | ❌ | ⚠️ staleness only | ❌ |
| Incremental updates on commit | ✅ <30s | ✅ | ❌ | ✅ | ✅ |
| Local dashboard / frontend | ✅ | ❌ | ❌ | ❌ IDE only | ✅ |
| Free for internal use | ✅ | ✅ public repos | ✅ public repos | ❌ | ❌ |

**The honest summary:**

- **vs Google Code Wiki** — Google's offering (launched Nov 2025) is cloud-only with no private repo support yet. Gemini-powered docs are strong, but there's no git behavioral intelligence, no dead code detection, no MCP server, and no architectural decisions.
- **vs DeepWiki** — Cloud-only, closed source (community self-hostable forks exist). Strong docs and Q&A, with a basic 3-tool MCP server. No git analytics, no dead code, no decisions.
- **vs Swimm** — Swimm's strength is keeping manually-written docs linked to code snippets with staleness detection. No graph, no git behavioral analytics, no dead code, no MCP by default. Enterprise pricing for private hosting.
- **vs CodeScene** — CodeScene has excellent git intelligence (hotspots, co-changes, ownership, bus factor). No documentation generation, no RAG, no architectural decisions. Closed source, per-author pricing.

repowise is the intersection: CodeScene-level git intelligence + auto-generated documentation + agent-native MCP + architectural decisions, self-hostable and open source.

---

## Hosted version — for teams

For teams that want repowise managed, we offer a hosted version. No self-hosting, no infrastructure to maintain — we handle deployment, updates, and webhooks. If your team wants shared codebase intelligence without the operational overhead, reach out.

Hosted adds what only makes sense in a managed, multi-user environment:

- **Shared team context layer** — one CLAUDE.md backed by the full graph and decision layer, auto-injected into every team member's Claude Code session via MCP
- **Session intelligence harvesting** — architectural decisions extracted from AI coding sessions and proposed to the team knowledge base automatically
- **Security vulnerability reporting** — repowise scans for known vulnerability patterns, dependency risks, and security anti-patterns across your codebase and surfaces them proactively. Not just `eval` calls — real CVE-aware analysis
- **Engineering leader dashboard** — bus factor trends, hotspot evolution over time, cross-repo dead code, ownership drift
- **Managed webhooks** — zero-configuration auto re-index on every commit to any branch
- **Integrations** — Slack alerts, Jira and Linear decision linking, Confluence and Notion doc sync, GitHub and GitLab webhooks, PagerDuty escalation routing
- **Cross-repo intelligence** — hotspots, dead code, and ownership across all your repositories at once

[Get in touch →](https://www.repowise.dev/#contact) · [hello@repowise.dev](mailto:hello@repowise.dev)

---

## CLI reference

```bash
# Core
repowise init [PATH]              # index codebase (one-time)
repowise init --index-only        # graph + git + dead code, no LLM, no cost
repowise init -x vendor/ -x proto/  # exclude patterns (gitignore syntax)
repowise init --include-submodules   # include git submodule directories
repowise update [PATH]            # incremental update (<30 seconds)
repowise serve [PATH]             # MCP server + local dashboard
repowise watch [PATH]             # auto-update on file save

# Query
repowise query "<question>"       # ask anything from the terminal
repowise search "<query>"         # semantic search over the wiki
repowise status                   # coverage, freshness, dead code summary

# Dead code
repowise dead-code                          # full report
repowise dead-code --safe-only              # only safe-to-delete findings
repowise dead-code --min-confidence 0.8     # raise the confidence threshold
repowise dead-code --include-internals      # include private/underscore symbols
repowise dead-code --include-zombie-packages  # include unused declared packages
repowise dead-code resolve <id>             # mark resolved / false positive

# Cost tracking
repowise costs                    # total LLM spend to date
repowise costs --by operation     # grouped by operation type
repowise costs --by model         # grouped by model
repowise costs --by day           # grouped by day

# Decisions
repowise decision add             # record a decision (interactive)
repowise decision list            # all decisions, filterable
repowise decision confirm <id>    # confirm a proposed decision
repowise decision health          # stale, conflicts, ungoverned hotspots

# Editor files
repowise generate-claude-md       # regenerate CLAUDE.md

# Hooks (auto-installed by init — not meant to be called manually)
repowise augment                  # enriches agent tool calls with graph context

# Utilities
repowise export [PATH]            # export wiki as markdown files
repowise export --full --format json  # full export with decisions, dead code, hotspots
repowise doctor                   # check setup, API keys, store drift
repowise doctor --repair          # check and fix detected store mismatches
repowise reindex                  # rebuild vector store (no LLM calls)
```

---

## Supported languages

| Tier | Languages | What works |
|------|-----------|------------|
| **Full** | Python · TypeScript · JavaScript · Java | AST parsing, import resolution, dependency graph edges, call resolution, heritage extraction |
| **Good** | Go · Rust · C++ · Kotlin · C# · Ruby | AST parsing, symbol extraction, heritage extraction (extends/implements/trait impls), partial import resolution |
| **Basic** | C | AST parsing (structs, functions), `#include` resolution with `compile_commands.json` |
| **Traversal** | Swift · Scala · PHP | Files indexed and searchable, but no AST symbol extraction yet |
| **Config / data** | OpenAPI · Protobuf · GraphQL · Dockerfile · Makefile · YAML · JSON · TOML · SQL · Terraform | Included in the file tree; special handlers extract endpoints/targets where applicable |

Dart and Elixir are on the roadmap. Adding a new language requires one `.scm` tree-sitter query file and one config entry. No changes to the parser core. PRs welcome. See [Adding a new language](docs/CONTRIBUTING.md#adding-a-new-language).

---

## Privacy

**Self-hosted:** Your code never leaves your infrastructure. No telemetry. No analytics. Zero.

**BYOK:** Bring your own Anthropic or OpenAI API key. We never see your LLM calls. Zero data retention via Anthropic's API policy — your code is never used to train any model.

**What is stored:** NetworkX graph (file and symbol relationships, communities, call edges with confidence), LanceDB embeddings (non-reversible vectors), generated wiki pages, git metadata. Raw source code is processed transiently and never persisted.

**Fully offline:** Ollama for LLM + local embedding models = zero external API calls.

---

## Configuration

`repowise init` generates `.repowise/config.yaml`. Key options:

```yaml
provider: anthropic               # anthropic | openai | ollama | litellm
model: claude-sonnet-4-5
embedding_model: voyage-3

git:
  co_change_commit_limit: 500
  blame_enabled: true

dead_code:
  enabled: true
  safe_to_delete_threshold: 0.7

maintenance:
  cascade_budget: 30              # max pages fully regenerated per commit
  background_regen_schedule: "0 2 * * *"
```

Full configuration reference: [docs/CONFIG.md](docs/CONFIG.md)

---

## Contributing

```bash
git clone https://github.com/repowise-dev/repowise
cd repowise
pip install -e "packages/core[dev]"
pytest tests/unit/
```

Full guide including how to add languages and LLM providers: [CONTRIBUTING.md](CONTRIBUTING.md)

---

## License

AGPL-3.0. Free for individuals, teams, and companies using repowise internally.

For commercial licensing — embedding repowise in a product, white-labeling, or SaaS use without AGPL obligations — contact [hello@repowise.dev](mailto:hello@repowise.dev).

---

<div align="center">

Built for engineers who got tired of watching their AI agent `cat` the same file for the fourth time.

[repowise.dev](https://repowise.dev) · [Live Demo →](https://repowise.dev/examples) · [Discord](https://discord.gg/cQVpuDB6rh) · [X](https://x.com/repowisedev) · [hello@repowise.dev](mailto:hello@repowise.dev)

</div>
