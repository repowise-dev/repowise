<div align="center">

<img src=".github/assets/logo.png" width="280" alt="repowise" /><br />
**Codebase intelligence for AI-assisted engineering teams.**

Four intelligence layers. Eight MCP tools. One `pip install`.

[![PyPI version](https://img.shields.io/pypi/v/repowise?color=F59520&labelColor=0A0A0A)](https://pypi.org/project/repowise/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--v3-F59520?labelColor=0A0A0A)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/python-3.11%2B-F59520?labelColor=0A0A0A)](https://pypi.org/project/repowise/)
[![MCP](https://img.shields.io/badge/MCP-compatible-F59520?labelColor=0A0A0A)](https://modelcontextprotocol.io)
[![Stars](https://img.shields.io/github/stars/repowise-dev/repowise?color=F59520&labelColor=0A0A0A)](https://github.com/repowise-dev/repowise)

[**Live Demo →**](https://repowise.dev/examples) · [**Hosted for teams**](https://repowise.dev) · [**Docs**](https://repowise-dev.github.io) · [**Discord**](https://discord.gg/repowise)

---

<img src=".github/assets/demo.gif" alt="repowise demo — repowise init → Claude Code querying via MCP tools" width="100%" />

---

</div>

When Claude Code reads a 3,000-file codebase, it reads files. It does not know who owns them, which ones change together, which ones are dead, or why they were built the way they were.

repowise fixes that. It indexes your codebase into four intelligence layers — dependency graph, git history, auto-generated documentation, and architectural decisions — and exposes them to Claude Code (and any MCP-compatible AI agent) through eight precisely designed tools.

The result: Claude Code answers *"why does auth work this way?"* instead of *"here is what auth.ts contains."*

---

## What's new

### Faster indexing
Indexing is now fully parallel. A `ProcessPoolExecutor` distributes AST parsing across all CPU cores. Graph construction and git history indexing run concurrently via `asyncio.gather`. Per-file git history is fetched through a thread executor with a semaphore to cap concurrency — full parallelism without overwhelming the system. Large repos index noticeably faster.

### RAG-aware documentation generation
Every wiki page is generated with richer context: before calling the LLM, repowise fetches the already-generated summaries of each file's direct dependencies from the vector store and injects them into the prompt. Generation is topologically sorted so leaf files are always written first. The LLM sees what its dependencies actually do, not just their names — producing more accurate, cross-referenced documentation.

### Atomic three-store transactions
`AtomicStorageCoordinator` buffers writes across the SQL database, the in-memory dependency graph, and the vector store, then flushes them in a single coordinated operation. If any store fails, all three are rolled back — no partial writes, no silent drift. Run `repowise doctor` to inspect drift across all three stores and repair mismatches.

### Dynamic import hints
The dependency graph now captures edges that pure AST parsing misses:
- Django `INSTALLED_APPS`, `ROOT_URLCONF`, and `MIDDLEWARE` settings
- pytest fixture wiring through `conftest.py`
- Node/TypeScript path aliases from `tsconfig.json` `paths` and `package.json` `exports`

These edges appear in `get_context`, `get_risk`, and `get_dependency_path` like any other dependency.

### Temporal hotspot decay
Hotspot scoring now uses an exponentially time-decayed score with a 180-day half-life layered on top of the raw 90-day churn count. A commit from a year ago contributes roughly 25% as much as a commit from today. The score reflects recent activity, not just total volume. Surfaced in `get_overview` and `get_risk`.

### Percentile ranks via SQL window function
Incremental updates now recompute global percentile ranks for every file using a single `PERCENT_RANK()` SQL window function. Previously this required loading all rows into Python. The new approach is both faster and correct on large repos — no sampling, no approximation.

### PR blast radius
`get_risk(changed_files=[...])` now returns a full blast-radius report: transitive affected files, co-change warnings for historical co-change partners not included in the PR, recommended reviewers ranked by temporal ownership, test gap detection, and an overall 0–10 risk score. Same eight tools — substantially more signal per call.

### Knowledge map in `get_overview`
`get_overview` now surfaces: top owners across the codebase, "bus factor 1" knowledge silos (files where one person owns >80% of commits), and onboarding targets — high-centrality files with the weakest documentation coverage. Useful for team planning and risk review.

### Test gaps and security signals in `get_risk`
`get_risk` now includes a `test_gap` flag per file (no test file co-changes detected) and `security_signals` — static pattern detection for common risk categories: authentication bypass patterns, `eval`-family calls, raw SQL string construction, and weak cryptography. Signals appear alongside the existing hotspot and ownership data.

### LLM cost tracking
Every LLM call is logged to a new `llm_costs` table with operation type, model, token counts, and estimated cost. A new `repowise costs` CLI command lets you group spending by operation, model, or day. The indexing progress bar now shows a live `Cost: $X.XXX` counter next to the spinner.

### Configurable dead-code sensitivity
The `repowise dead-code` command and the `get_dead_code` MCP tool now expose sensitivity controls: `--min-confidence` (default 0.70), `--include-internals` (include private/underscore-prefixed symbols), and `--include-zombie-packages` (packages present in `package.json` / `pyproject.toml` but unused in the graph). Tune the output to your cleanup goals.

---

## What repowise builds

repowise runs once, builds everything, then keeps it in sync on every commit.

### ◈ Graph Intelligence
tree-sitter parses every file into symbols. NetworkX builds a dependency graph — files, classes, functions, imports, inheritance, and call relationships. PageRank identifies your most central code. Community detection finds logical modules even when your directory structure doesn't reflect them.

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

Add to your Claude Code config (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "repowise": {
      "command": "repowise",
      "args": ["mcp", "--path", "/path/to/your/project"]
    }
  }
}
```

**Via Claude plugin store:** Install the repowise plugin, then tell Claude: *"Set up repowise for this project."* Claude handles `pip install`, `repowise init`, and MCP configuration automatically.

> **Note on init time:** Initial indexing analyzes your entire codebase — AST parsing, 500-commit git history, LLM doc generation, embedding indexing, and decision archaeology. This is a one-time cost (~25 minutes for a 3,000-file project). Every subsequent update after a commit takes under 30 seconds.

---

## Eight MCP tools

Most tools are designed around data entities — one module, one file, one symbol — which forces AI agents into long chains of sequential calls. repowise tools are designed around **tasks**. Pass multiple targets in one call. Get complete context back.

| Tool | What it answers | When Claude Code calls it |
|---|---|---|
| `get_overview()` | Architecture summary, module map, entry points | First call on any unfamiliar codebase |
| `get_context(targets, include?)` | Docs, ownership, decisions, freshness for any targets — files, modules, or symbols | Before reading or modifying code. Pass all relevant targets in one call. |
| `get_risk(targets?, changed_files?)` | Hotspot scores, dependents, co-change partners, blast radius, recommended reviewers, test gaps, security signals, 0–10 risk score | Before modifying files — understand what could break |
| `get_why(query?)` | Three modes: NL search over decisions · path-based decisions for a file · no-arg health dashboard | Before architectural changes — understand existing intent |
| `search_codebase(query)` | Semantic search over the full wiki. Natural language. | When you don't know where something lives |
| `get_dependency_path(from, to)` | Connection path between two files, modules, or symbols | When tracing how two things are connected |
| `get_dead_code(min_confidence?, include_internals?, include_zombie_packages?)` | Unreachable code sorted by confidence and cleanup impact | Cleanup tasks |
| `get_architecture_diagram(module?)` | Mermaid diagram for the repo or a specific module | Documentation and presentation |

### Tool call comparison — a real task

*"Add rate limiting to all API endpoints."*

| Approach | Tool calls | Time to first change | What it misses |
|---|---|---|---|
| Claude Code alone (no MCP) | grep + read ~30 files | ~8 min | Ownership, prior decisions, hidden coupling |
| repowise (old 16-tool design) | 16 sequential calls | ~15 min | Nothing — but slow |
| **repowise (8 tools)** | **5 calls** | **~2 min** | **Nothing** |

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
| **Docs** | AI-generated wiki with syntax highlighting and Mermaid diagrams |
| **Graph** | Interactive dependency graph — handles 2,000+ nodes |
| **Search** | Full-text and semantic search with global command palette (Ctrl+K) |
| **Symbols** | Searchable index of every function, class, and method |
| **Coverage** | Doc freshness per file with one-click regeneration |
| **Ownership** | Contributor attribution and bus factor risk |
| **Hotspots** | Ranked high-churn files with commit history |
| **Dead Code** | Unused code with confidence scores and bulk actions |
| **Decisions** | Architectural decisions with staleness monitoring |

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

Conservative by design. `safe_to_delete` requires confidence ≥ 0.70 and excludes dynamically-loaded patterns (`*Plugin`, `*Handler`, `*Adapter`, `*Middleware`). repowise surfaces candidates. Engineers decide.

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
| MCP server for AI agents | ✅ 8 tools | ❌ | ✅ 3 tools | ✅ | ✅ |
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

The self-hosted OSS version is complete and production-ready. A hosted version for engineering teams is in active development.

Hosted adds what only makes sense in a managed, multi-user environment:

- **Shared team context layer** — one CLAUDE.md backed by the full graph and decision layer, auto-injected into every team member's Claude Code session via MCP
- **Session intelligence harvesting** — architectural decisions extracted from AI coding sessions and proposed to the team knowledge base automatically
- **Engineering leader dashboard** — bus factor trends, hotspot evolution over time, cross-repo dead code, ownership drift
- **Managed webhooks** — zero-configuration auto re-index on every commit to any branch
- **Integrations** *(coming)* — Slack alerts, Notion sync, Confluence sync, Jira and Linear decision linking
- **Cross-repo intelligence** — hotspots, dead code, and ownership across all your repositories at once

[Join the hosted waitlist →](https://repowise.dev) · [Contact us for enterprise](mailto:hello@repowise.dev)

---

## CLI reference

```bash
# Core
repowise init [PATH]              # index codebase (one-time)
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

# Utilities
repowise export [PATH]            # export wiki as markdown files
repowise doctor                   # check setup, API keys, store drift
repowise doctor --repair          # check and fix detected store mismatches
repowise reindex                  # rebuild vector store (no LLM calls)
```

---

## Supported languages

**Code:** Python · TypeScript · JavaScript · Go · Rust · Java · C · C++ · Ruby · Kotlin

**Config / contracts:** OpenAPI · Protobuf · GraphQL · Dockerfile · GitHub Actions YAML · Makefile

Adding a new language requires one `.scm` tree-sitter query file and one config entry. No changes to the parser. See [Adding a new language](docs/CONTRIBUTING.md#adding-a-new-language).

---

## Privacy

**Self-hosted:** Your code never leaves your infrastructure. No telemetry. No analytics. Zero.

**BYOK:** Bring your own Anthropic or OpenAI API key. We never see your LLM calls. Zero data retention via Anthropic's API policy — your code is never used to train any model.

**What is stored:** NetworkX graph (file and symbol relationships), LanceDB embeddings (non-reversible vectors), generated wiki pages, git metadata. Raw source code is processed transiently and never persisted.

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
git clone https://github.com/repowise/repowise
cd repowise
pip install -e "packages/core[dev]"
pytest tests/unit/
```

Good first issues: [`good first issue`](https://github.com/repowise/repowise/labels/good%20first%20issue)

Full guide including how to add languages and LLM providers: [CONTRIBUTING.md](CONTRIBUTING.md)

---

## License

AGPL-3.0. Free for individuals, teams, and companies using repowise internally.

For commercial licensing — embedding repowise in a product, white-labeling, or SaaS use without AGPL obligations — contact [commercial@repowise.dev](mailto:commercial@repowise.dev).

---

<div align="center">

Built for engineers who got tired of asking *"why does this code exist?"*

[repowise.dev](https://repowise.dev) · [Live Demo →](https://repowise.dev/examples) · [Discord](https://discord.gg/repowise) · [X](https://x.com/repowisedev)

</div>
