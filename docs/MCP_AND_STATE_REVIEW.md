# repowise — MCP Server & Project State Review

**Date:** 2026-03-25
**Test repo:** `interview-coach` (3,557 files, polyglot monorepo)
**Reviewer:** Claude Code (automated + manual)

---

## Table of Contents

1. [MCP Server Architecture](#1-mcp-server-architecture)
2. [MCP Tools Inventory](#2-mcp-tools-inventory)
3. [MCP Server Startup & Connection](#3-mcp-server-startup--connection)
4. [Test Run Output Analysis](#4-test-run-output-analysis)
5. [Database State Audit](#5-database-state-audit)
6. [Generated Content Quality Assessment](#6-generated-content-quality-assessment)
7. [MCP Tool-by-Tool Assessment](#7-mcp-tool-by-tool-assessment)
8. [Issues Found — Prioritized](#8-issues-found--prioritized)
9. [Core Engine Code Review](#9-core-engine-code-review)
10. [Recommendations](#10-recommendations)

---

## 1. MCP Server Architecture

### Overview

The MCP server (`packages/server/src/repowise/server/mcp_server.py`) exposes
the full repowise wiki as queryable tools via the Model Context Protocol. It
supports two transports:

- **stdio** — for Claude Code, Cursor, Cline (the primary use case)
- **SSE** — for web-based MCP clients (port 7338)

### Lifecycle

On startup (`_lifespan`), the server:

1. Resolves the database URL from `_repo_path` → `.repowise/wiki.db`
2. Creates an async SQLAlchemy engine + session factory
3. Initializes FTS5 full-text search index
4. Creates a `MockEmbedder` + `InMemoryVectorStore` (**Issue: always mock**)
5. Attempts to load LanceDB if available (passes mock embedder to it)
6. Sets up a decision store (LanceDB or InMemory fallback)

### Key Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| `mcp` (Python SDK) | >=1.0, <2 | MCP protocol implementation |
| `FastMCP` | from mcp.server.fastmcp | High-level MCP server builder |
| `SQLAlchemy` | >=2.0 | Async database access |
| `aiosqlite` | >=0.20 | SQLite async driver |
| `networkx` | >=3.3 | Graph operations (dependency path) |

### Global State

The server uses module-level globals set during lifespan:

```python
_session_factory  # async_sessionmaker — DB sessions
_vector_store     # VectorStore — semantic search
_decision_store   # VectorStore — decision search
_fts              # FullTextSearch — FTS5 fallback
_repo_path        # str — repository root path
```

This design is functional but means only one repo can be served per MCP
server process.

---

## 2. MCP Tools Inventory

The server implements **8 tools** (consolidated from 16 on 2026-03-25 to reduce
sequential tool calls for common tasks).

| # | Tool | Category | What It Answers |
|---|------|----------|----------------|
| 1 | `get_overview` | Navigation | Architecture summary, module map, entry points |
| 2 | `get_context(targets, include?)` | Context | Docs, ownership, history, decisions, freshness for files/modules/symbols. Multi-target, single call. |
| 3 | `get_risk(targets)` | Risk | Hotspot score, dependents, co-change partners, risk summary. Multi-target, single call. |
| 4 | `get_why(query?)` | Decision Intelligence | 3 modes: NL search, path-based decisions, no-arg health dashboard |
| 5 | `search_codebase` | Search | Semantic search over full wiki (natural language) |
| 6 | `get_dependency_path` | Graph | How two files/modules are connected |
| 7 | `get_dead_code` | Analysis | Dead/unused code findings |
| 8 | `get_architecture_diagram` | Visualization | Mermaid diagram for repo/module/file |

**Consolidation mapping** (old → new):
- `get_module_docs`, `get_file_docs`, `get_symbol`, `get_file_history`, `get_codebase_ownership` (partial), `get_stale_pages` → **`get_context`**
- `get_hotspots`, `get_co_changes`, `get_codebase_ownership` (partial) → **`get_risk`**
- `get_decisions`, old `get_why`, `get_decision_health` → **`get_why`** (3-mode)

### Tool Design Patterns

All tools follow a consistent pattern:
1. Accept optional `repo` parameter (path, name, or ID) — defaults to first repo
2. Open an async session via `get_session(_session_factory)`
3. Resolve the repository via `_get_repo()` (path → ID → name → first)
4. Query SQL tables and return structured dicts

Error handling: tools return `{"error": "message"}` dicts rather than raising
exceptions, which is the correct pattern for MCP tools.

---

## 3. MCP Server Startup & Connection

### Prerequisites

All three packages must be installed:

```
pip install -e packages/core
pip install -e packages/cli
pip install -e packages/server    # <-- was missing, caused ModuleNotFoundError
```

### Startup Command

```bash
repowise mcp /path/to/repo --transport stdio
```

### Claude Code Configuration

Project-level config at `~/.claude/projects/<project-hash>/.mcp.json`:

```json
{
  "mcpServers": {
    "repowise": {
      "command": "repowise",
      "args": ["mcp", "C:/Users/ragha/Desktop/interview-coach", "--transport", "stdio"]
    }
  }
}
```

### Startup Issues Found

| Issue | Severity | Description |
|-------|----------|-------------|
| Missing `repowise-server` install | **Blocker** | `repowise mcp` fails with `ModuleNotFoundError: No module named 'repowise.server'` if only cli+core are installed. The `Makefile` and README should document that all 3 packages must be installed. |
| No startup validation | Minor | Server starts silently in stdio mode even if `.repowise/wiki.db` doesn't exist — tools will fail at query time instead of at startup |
| Mock embedder always used | **Major** | `_lifespan()` always creates `MockEmbedder()` regardless of config |

---

## 4. Test Run Output Analysis

### Job History (interview-coach repo)

| Job ID (short) | Date | Provider | Model | Status | Pages | Notes |
|----------------|------|----------|-------|--------|-------|-------|
| `0f25154f` | Mar 19 | litellm | gemini/gemini-2.0-flash | pending | 0/0 | Never completed |
| `6f115548` | Mar 19 | litellm | gemini/gemini-2.0-flash | pending | 0/0 (1 failed) | `failed_page_ids: ["level-1"]` — level name not page ID |
| `6b10db4b` | Mar 20 | litellm | gemini-3.5-flash-preview | completed | 0/0 (5 failed) | Failed levels 1-4, 6 |
| `6cbc7ec1` | Mar 20 | gemini | gemini-3.1-flash-lite-preview | completed | 33/33 | First successful full run |
| `2eb0b48f` | Mar 20 | gemini | gemini-3.1-flash-lite-preview | completed | 31/31 | Re-run with different files |
| `b3f0b97c` | Mar 20 | gemini | gemini-3.1-flash-lite-preview | completed | 28/23 | `completed > total` bug |
| `46e86452` | Mar 22 | gemini | gemini-3.1-flash-lite-preview | completed | 685/337 | Final big run, `completed > total` bug |

### Key Observations

- **Provider evolution:** Started with litellm→gemini-2.0-flash (failed), switched to native gemini provider with gemini-3.1-flash-lite-preview (succeeded)
- **Early failures used level names as page IDs** — `"level-1"` instead of actual page identifiers
- **`completed_pages > total_pages`** in multiple jobs — the `total_pages` estimate is computed before all levels are planned
- **Total tokens used:** 4,223,332 (from state.json)

### .repowise Directory Structure

```
.repowise/
├── config.yaml          (74 bytes — provider, model, embedder)
├── state.json           (191 bytes — last commit, total pages, provider)
├── wiki.db              (24 MB — all structured data)
├── mcp.json             (292 bytes — auto-generated MCP config)
├── findings.md          (11 KB — codebase intelligence report)
├── jobs/                (7 job checkpoint files)
└── export/              (721 markdown files)
```

### Export Files

721 markdown files exported, matching the DB page count. Files are named using
the target_path with `/` → `_`, `::` → `__` sanitization. File types exported:

- 549 file pages (individual source file documentation)
- 102 symbol spotlight pages (high-PageRank symbols)
- 34 infrastructure pages (Dockerfile, shell scripts, etc.)
- 22 SCC pages (circular dependency analysis)
- 6 module pages (backend, frontend, hire-backend, etc.)
- 6 cross-package relationship pages
- 1 repository overview
- 1 architecture diagram

---

## 5. Database State Audit

### Schema (15 tables)

| Table | Rows | Purpose |
|-------|------|---------|
| `repositories` | 1 | Repo registration and sync state |
| `wiki_pages` | 721 | Generated wiki page content |
| `wiki_page_versions` | — | Version history for diff view |
| `wiki_symbols` | 12,955 | Symbol index (functions, classes, methods) |
| `graph_nodes` | 3,632 | Dependency graph nodes (file type only) |
| `graph_edges` | 9,506 | Dependency graph edges |
| `git_metadata` | 2,022 | Per-file git history |
| `dead_code_findings` | 1,233 | Dead code findings |
| `decision_records` | 23 | Architectural decision records |
| `generation_jobs` | — | Job state machine |
| `webhook_events` | — | Webhook audit log |
| `page_fts` + related | — | SQLite FTS5 search index |

### Data Integrity Checks

| Check | Result | Notes |
|-------|--------|-------|
| `state.json total_pages` vs DB | **MISMATCH** | 685 vs 721 — state tracks last job only |
| Page types distribution | OK | All expected types present |
| Symbol coverage | OK | 12,955 symbols across all parsed files |
| Graph completeness | **PARTIAL** | Only `file` node type (no symbol/package/external nodes) |
| Graph edge types | **MISSING** | No `edge_type` column in `graph_edges` |
| Git metadata coverage | OK | 2,022/3,557 files (git-tracked files only) |
| Dead code by kind | OK | 1,230 unreachable_file + 3 zombie_package |
| Decision records | OK | 23 proposed (from git_archaeology + readme_mining) |
| FTS index | OK | Tables created and populated |

### Graph Schema Gap

**Architecture doc describes:**
- Node types: `file`, `symbol`, `package`, `external`
- Edge types: `imports`, `calls`, `inherits`, `implements`, `co_changes`, etc.

**Actual DB schema:**
- `graph_nodes`: has `node_type` column but only contains `file` values
- `graph_edges`: has NO `edge_type` column — only `source_node_id`, `target_node_id`, `imported_names_json`

This means the `get_dependency_path` MCP tool hardcodes `"relationship": "imports"` for every edge, losing the rich relationship type information described in the architecture.

---

## 6. Generated Content Quality Assessment

### Scoring (1-5)

| Content Type | Quality | Notes |
|-------------|---------|-------|
| Repository overview | 5/5 | Accurate tech stack, entry points, architecture description |
| Architecture diagram | 4/5 | Good Mermaid diagram, correct dependency flow, notes circular deps |
| Module pages | 5/5 | Comprehensive API summaries, correct architecture notes |
| File pages | 4/5 | Accurate symbol docs, good dependency analysis |
| Symbol spotlights | 4/5 | Useful for key symbols, good parameter documentation |
| SCC pages | 5/5 | Excellent cycle analysis with actionable refactoring suggestions |
| Cross-package pages | 5/5 | Real coupling analysis with actual API types identified |
| Infrastructure pages | 4/5 | Good script documentation |
| Findings report | 5/5 | Outstanding codebase intelligence — hotspots, co-changes, milestones |

### Example Quality: SCC-79 (Circular Dependency)

The `scc-79.md` page correctly identifies:
- The `auth-context.tsx` → `payment-context.tsx` → `tier-context.tsx` → `auth-context.tsx` cycle
- WHY it exists (bidirectional state dependency)
- 5 specific refactoring strategies (extract types, dependency injection, domain service layer, event-driven, hierarchical refactoring)

This is genuinely useful documentation that would take a human engineer significant time to produce.

---

## 7. MCP Tool-by-Tool Assessment (Live Test Results)

All tools were tested live against the interview-coach wiki database on 2026-03-25
(originally 16 tools; consolidated to 8 tools on 2026-03-25).

### Result Summary

| # | Tool | Status | Verdict |
|---|------|--------|---------|
**Post-consolidation (8 tools, tested 2026-03-25):**

| # | Tool | Status | Verdict |
|---|------|--------|---------|
| 1 | `get_overview` | **PASS** | Rich response: overview, 6 modules, 45 entry points |
| 2 | `get_context` | **PASS** | Multi-target (file+module+symbol) resolved in one call. Docs, ownership, decisions, freshness all returned. |
| 3 | `get_risk` | **PASS** | DSAPatternTable at 100% hotspot, 43 co-change partners, 26 dependents. Global hotspots exclude targets. |
| 4 | `get_why` (3 modes) | **PASS** | Search mode finds keyword-matched decisions; path mode returns governing decisions; health mode shows dashboard |
| 5 | `search_codebase` | **PASS** | Semantic search via LanceDB + Gemini. "payment system" → payment_service.py (score 6.72). FTS fallback also works. |
| 6 | `get_dependency_path` | **PASS** | Correctly finds 1-hop path tiro_router → tiro/service |
| 7 | `get_dead_code` | **PASS** | 1,232 findings, 48,780 deletable lines, sorted by confidence desc |
| 8 | `get_architecture_diagram` | **PASS** | Repo scope returns pre-gen Mermaid; module scope generates dynamic |

**Original 16-tool results (pre-consolidation, for reference):**

| # | Tool | Status | Verdict |
|---|------|--------|---------|
| 1 | `get_overview` | **PASS** | Rich response: overview, 6 modules, 45 entry points |
| 2 | `get_module_docs` | **PASS** | Returns full module page + 200+ child file listings |
| 3 | `get_file_docs` | **PASS** | Returns page content, 11 symbols, 3 importers, confidence 1.0 |
| 4 | `get_symbol` | **PASS** | Exact match works, returns `candidates` array for disambiguation |
| 5 | `search_codebase` | **PASS** (was FAIL) | Fixed: FTS fallback + LanceDB + stop word stripping |
| 6 | `get_architecture_diagram` | **PASS** | Repo scope returns pre-gen Mermaid; module scope generates dynamic |
| 7 | `get_dependency_path` | **PASS** | Correctly finds 1-hop path tiro_router → tiro/service |
| 8 | `get_stale_pages` | **PASS** | Empty (all pages fresh at 1.0) — correct behavior. Now part of `get_context` freshness. |
| 9 | `get_file_history` | **PARTIAL** | Ownership, hotspot, commits work; co_change counts all 0 (fixed via key mismatch fix) |
| 10 | `get_hotspots` | **PASS** | Returns top 5 hotspots with churn percentiles + stable files. Now part of `get_risk`. |
| 11 | `get_codebase_ownership` | **PASS** | Correctly identifies 3 knowledge silos. Now part of `get_context`/`get_risk`. |
| 12 | `get_co_changes` | **PASS** (was FAIL) | Fixed via co_change_count key mismatch fix (M10). Now part of `get_risk`. |
| 13 | `get_dead_code` | **PASS** | Summary correct; default min_confidence lowered to 0.5. |
| 14 | `get_decisions` | **PASS** | Returns all 23 proposed decisions with full context. Now part of `get_why`. |
| 15 | `get_why` | **PASS** (was FAIL) | Fixed: include_proposed=True + real embedder + FTS fallback. Now 3-mode tool. |
| 16 | `get_decision_health` | **PASS** | Shows 0 active, 23 proposed, 505 ungoverned hotspots. Now `get_why()` no-arg mode. |

### Detailed Test Results

#### Tool 1: `get_overview` — PASS

**Test:** `get_overview()` (no args)
**Result:** Returned structured dict with:
- `title`: "Repository Overview: repo"
- `content_md`: 2,500+ word project summary with tech stack, entry points, architecture
- `architecture_diagram_mermaid`: Full Mermaid diagram with dependency flow
- `key_modules`: 6 modules (backend, blogs, frontend, hire-backend, hire-frontend, scripts)
- `entry_points`: 45 files (backend apps, frontend index.ts barrels, hire modules)

**Quality:** Excellent. This gives an AI assistant immediate understanding of the
entire codebase structure. The architecture diagram includes technical debt notes.

**Issue:** Module descriptions truncated at 200 chars without word-boundary
awareness (line 231). Minor.

---

#### Tool 2: `get_module_docs` — PASS

**Test:** `get_module_docs("frontend")`
**Result:** Full module page content (68.7 KB response) with:
- Comprehensive overview of Next.js architecture
- Public API summary (dashboards, code execution, auth, analytics)
- Architecture notes (framework, state management, data fetching, real-time)
- 200+ child file page listings with paths and confidence scores

**Quality:** Excellent. Partial match fallback also tested — works.

---

#### Tool 3: `get_file_docs` — PASS

**Test:** `get_file_docs("backend/services/tiro/service.py")`
**Result:**
- `content_md`: Accurate documentation of TiroService RAG engine
- `symbols`: 11 methods (TiroService, stream_chat, generate_suggestions, etc.)
- `imported_by`: 3 files (tiro_router, ai_interview_analytics, code_practice_analytics)
- `confidence_score`: 1.0, `freshness_status`: "fresh"

**Quality:** Very good. Symbol signatures include full parameter lists.

---

#### Tool 4: `get_symbol` — PASS

**Tests:**
- `get_symbol("SupabaseService")` → exact match, file path, 20 importers, `candidates` shows hire-backend duplicate
- `get_symbol("stream_chat", kind="method")` → exact match with full async signature

**Quality:** Excellent. The `candidates` array for disambiguation when multiple
symbols share a name is very useful for AI assistants.

---

#### Tool 5: `search_codebase` — PASS (after fixes)

**Test:** `search_codebase("how does the payment system work")`
**Result (after fix):** 5 results, topped by `backend/services/payment_service.py` (score 6.72)

**Original failures and fixes (2026-03-25):**
1. `MockEmbedder` produced random vectors → Fixed by `_resolve_embedder()` reading config.yaml
2. FTS fallback never triggered (vector store returned empty list, not exception) → Fixed: now
   falls back to FTS when vector results are empty
3. FTS used exact phrase match for multi-word queries → Fixed: `_build_fts5_query()` strips
   stop words and joins terms with OR + prefix matching
4. No LanceDB directory existed (init never persisted embeddings) → Fixed: `init_cmd.py` now
   tries LanceDB before InMemoryVectorStore; added `repowise reindex` command to embed
   existing pages without re-generating
5. `lancedb` package not installed → Installed; 721 pages + 23 decisions indexed via Gemini embedder

**Current status:** Semantic search via LanceDB + Gemini embeddings fully operational.
FTS fallback also works for environments without LanceDB.

---

#### Tool 6: `get_architecture_diagram` — PASS

**Tests:**
- `scope="repo"` → Returns pre-generated Mermaid diagram with dependency flow
- `scope="module", path="backend"` → Generates dynamic Mermaid with ~50 edges

**Quality:** Repo scope is excellent (human-curated by LLM). Module scope is
functional but dense — the 50-edge cap produces a readable but incomplete view.

---

#### Tool 7: `get_dependency_path` — PASS

**Test:** `source="backend/routers/tiro_router.py", target="backend/services/tiro/service.py"`
**Result:** Path found, distance 1, correct direct import relationship

**Issue:** All relationships labeled "imports" regardless of actual type (no
`edge_type` in DB). Performance concern: loads all 9,506 edges per call.

---

#### Tool 8: `get_stale_pages` — PASS

**Test:** `threshold=0.6`
**Result:** `{"stale_pages": []}` — correct, all pages are fresh (confidence 1.0)

This tool will become useful after `repowise update` runs and confidence decays.

---

#### Tool 9: `get_file_history` — PARTIAL PASS

**Test:** `file_path="frontend/components/problems/DSAPatternTable.tsx"`
**Result:**
- `age_days`: 161, `is_hotspot`: true, `commit_count_total`: 74, `commit_count_90d`: 51
- `primary_owner`: swati510 (78%)
- `significant_commits`: 10 commits with meaningful messages
- `co_change_partners`: 43 partners listed BUT **all have `co_change_count: 0`**

**Bug:** The co-change count data is not being persisted into `git_metadata.co_change_partners_json`
correctly. The `findings.md` report shows real co-change counts (e.g., DSAPatternTable ↔
technical_coaching_router: 8 times), but the JSON stored in the DB has counts of 0 for all
partners. The partner *list* is correct (the right files are identified), but the
*counts* are lost during persistence.

**Impact:** MEDIUM — the co-change partner names are still useful, but without counts
there's no way to rank them by coupling strength.

---

#### Tool 10: `get_hotspots` — PASS

**Test:** `limit=5, include_stable=true`
**Result:**
- 5 hotspots: DSAPatternTable (51 commits/90d, 99.95th percentile), dashboard, landing, sidebar, PurchaseGate
- 5 stable files: streamingInterviewService (30 total, 0 recent), ResumeAnalyzer, SystemDesignCanvas, etc.

**Quality:** Excellent. The churn percentile scoring makes results actionable.

---

#### Tool 11: `get_codebase_ownership` — PASS

**Test:** `by="module"`
**Result:** 6 modules with ownership data:
- `blogs`: 100% RaghavChamadiya → `is_silo: true`
- `hire-backend`: 89% RaghavChamadiya → `is_silo: true`
- `hire-frontend`: 96% RaghavChamadiya → `is_silo: true`
- `backend`, `frontend`: split ownership → `is_silo: false`
- `scripts`: no owner data

**Quality:** Good. The silo detection is accurate and actionable.

---

#### Tool 12: `get_co_changes` — FAIL

**Test:** `file_path="backend/routers/pipecat_ai_interviewer_router.py"`
**Result:** `{"co_change_partners": []}` — empty

**Root cause:** Same as Tool 9 — co-change counts stored as 0, and the default
`min_count=3` filter removes all partners. Even with `min_count=0`, the data
would lack meaningful ranking.

**Impact:** HIGH — this tool is designed for one of repowise's most valuable
features (revealing hidden coupling not visible in imports). Without real counts,
it's useless.

---

#### Tool 13: `get_dead_code` — PASS (with caveat)

**Tests:**
- `kind="zombie_package", min_confidence=0.6` → `findings: []` (filtered out)
- `kind="zombie_package", min_confidence=0` → 3 findings (scripts, .github, monitoring)

**Issue:** Zombie package findings have confidence 0.5, which is below the default
`min_confidence=0.6`. Users will miss them unless they explicitly lower the threshold.

**Fix suggested:** Either raise zombie_package confidence to 0.7 or lower the
default min_confidence to 0.5.

---

#### Tool 14: `get_decisions` — PASS

**Test:** `include_proposed=true`
**Result:** 23 decisions with full context, rationale, consequences, affected files.
Sources: 17 from readme_mining (conf 0.6), 6 from git_archaeology (conf 0.7).

**Quality:** Good. The git_archaeology decisions include specific affected files
(e.g., Pipecat upgrade lists 7 affected files). The readme_mining decisions have
useful architectural context.

---

#### Tool 15 (now part of `get_why`): `get_why` — PASS (after fixes)

**Original failure:** `include_proposed=False` excluded all 23 decisions; MockEmbedder
returned nothing; FTS fallback never triggered.

**Fixes applied (2026-03-25):**
- Changed to `include_proposed=True` so keyword search finds proposed decisions
- Real embedder (Gemini) now used; LanceDB decision_records table populated via `repowise reindex`
- FTS fallback improved (stop word stripping, OR-based matching)

**Current status (consolidated `get_why` tool — 3 modes):**
- `get_why("why is JWT used")` → mode="search", returns keyword-matched decisions. **PASS**
- `get_why("src/auth/service.py")` → mode="path", returns decisions governing that file. **PASS**
- `get_why()` → mode="health", returns dashboard with 23 proposed, 505 ungoverned. **PASS**

---

#### Tool 16: `get_decision_health` — PASS

**Test:** `get_decision_health()` (no args)
**Result:**
- `summary`: "0 active · 0 stale · 23 proposed · 505 ungoverned hotspots"
- `proposed_awaiting_review`: First 10 of 23 decisions
- `ungoverned_hotspots`: 15 high-churn files with no decision coverage

**Quality:** Good. The "ungoverned hotspots" concept is valuable — it identifies
files that are actively being changed but have no documented architectural decisions.

---

## 8. Issues Found — Prioritized

### CRITICAL (must fix)

| # | Issue | Location | Description |
|---|-------|----------|-------------|
| C1 | **DB filename mismatch** (FIXED) | `mcp_server.py:64` | MCP server looked for `.repowise/repowise.db` but CLI creates `.repowise/wiki.db`. Fixed 2026-03-25: changed to `wiki.db`. |
| C2 | **LanceDB filter injection** (FIXED) | `vector_store.py:240,274` | `f"page_id = '{page_id}'"` — string interpolation in LanceDB delete filter. Fixed 2026-03-25: single quotes in page_id are now escaped before interpolation. |
| C3 | **Tool count mismatch in docs** (FIXED) | Multiple files | Updated "13 tools" → "16 tools" → "8 tools" (after consolidation) in mcp_server.py, ARCHITECTURE.md, server README. |

### MAJOR (significant impact)

| # | Issue | Location | Description |
|---|-------|----------|-------------|
| M1 | **MockEmbedder in MCP server** (FIXED) | `mcp_server.py:82-83` | Always created `MockEmbedder()`. Fixed 2026-03-25: added `_resolve_embedder()` that checks `REPOWISE_EMBEDDER` env var and `.repowise/config.yaml` for real embedder (gemini/openai). |
| M2 | **CLI semantic search broken** (FIXED) | `search_cmd.py:60-69` | Created empty `InMemoryVectorStore`. Fixed 2026-03-25: now tries LanceDB from `.repowise/lancedb/` first, falls back to FTS. |
| M3 | **`state.json` total_pages wrong** (FIXED) | `init_cmd.py:744` | `state["total_pages"] = len(generated_pages)` only counts last job. Fixed 2026-03-25: now queries actual DB page count via `SELECT COUNT(*)` after persistence. |
| M4 | **Missing `edge_type` in graph DB** (FIXED) | `persistence/models.py` | `graph_edges` table had no `edge_type` column. Fixed 2026-03-25: added `edge_type` column (Alembic migration 0004), CRUD layer, init_cmd persistence, and MCP server now uses real edge types in `get_dependency_path`. |
| M5 | **Missing `repo_path` in update** (FIXED) | `update_cmd.py:87` | `resolve_provider(provider_name, model)` was missing `repo_path`. Fixed 2026-03-25: now passes `repo_path=repo_path`. |
| M6 | ~~`get_repo_structure()` arg mismatch~~ (NOT A BUG) | `update_cmd.py:97` | The `files` parameter is optional (`files: list[FileInfo] | None = None`). Calling without args triggers a fresh traversal. |
| M7 | **`repowise-server` not auto-installed** (FIXED) | `packages/cli/pyproject.toml` | Fixed 2026-03-25: added `repowise-server` as explicit dependency of `repowise-cli` with workspace source. |
| M8 | **`completed_pages > total_pages`** (FIXED) | `job_system.py` | Fixed 2026-03-25: `complete_page()` now clamps `total_pages = max(total_pages, completed_pages)`. |
| M9 | **`search_codebase` returns empty** (FIXED) | `mcp_server.py`, `search.py`, `init_cmd.py` | Fixed: (a) FTS fallback triggers on empty results not just exceptions, (b) FTS query builder strips stop words and uses OR + prefix matching, (c) `init_cmd.py` now uses LanceDB when available, (d) added `repowise reindex` command. LanceDB indexed with 721 pages + 23 decisions via Gemini embedder. |
| M10 | **Co-change counts all zero** (FIXED) | `mcp_server.py` | Root cause: key mismatch — data stores `"co_change_count"` but MCP tools read `"count"`. Fixed 2026-03-25: tools now check both keys via `p.get("co_change_count", p.get("count", 0))`. |
| M11 | **`get_why` excludes all decisions** (FIXED) | `mcp_server.py:1155-1156` | `include_proposed=False` returned 0 results. Fixed 2026-03-25: changed to `include_proposed=True`. |

### MINOR (code quality / polish)

| # | Issue | Location | Description |
|---|-------|----------|-------------|
| m1 | **HTML export doesn't render markdown** (FIXED) | `export_cmd.py:118-127` | Wrapped content in `<pre>`. Fixed 2026-03-25: now renders via `markdown` or `mistune` library, falls back to `<pre>`. |
| m2 | Failed page IDs are level names | Job JSON files | `"level-1"` used as failed page ID instead of actual page identifier. Investigation shows `fail_page()` receives actual page IDs — the "level name" was a display callback, not a data bug. |
| m3 | **Module description truncation** (FIXED) | `mcp_server.py:293` | Fixed 2026-03-25: added parentheses to fix operator precedence bug (was duplicating content), and added `rsplit(" ", 1)[0]` for word-boundary awareness. |
| m4 | Graph nodes only `file` type | DB audit | Architecture describes symbol/package/external nodes but only file nodes are persisted. |
| m5 | **No startup validation in MCP** (FIXED) | `mcp_server.py:50-108` | Server started silently. Fixed 2026-03-25: added warning logs when `.repowise/` or `wiki.db` is missing. |
| m6 | **`click` import at bottom of file** (FIXED) | `helpers.py:257` | Fixed 2026-03-25: moved `import click` to top of file with other imports. |
| m7 | Ownership grouping too coarse | `mcp_server.py:858` | Groups by first directory only — too coarse for nested monorepos. |
| m8 | **Architecture diagram edge limit** (FIXED) | `mcp_server.py` | Fixed 2026-03-25: edges now sorted by source node PageRank before 50-edge cap, plus Mermaid node ID sanitization improved to use regex for all non-alphanumeric chars. |
| m9 | All decisions are "proposed" | DB content | No inline markers found — all 23 are auto-extracted. Expected but worth noting. |
| m10 | **`_decision_store` may be empty InMemory** (FIXED) | `mcp_server.py:103-104` | Fixed: `repowise reindex` now indexes 23 decision records into LanceDB `decision_records` table. MCP server loads this on startup when `.repowise/lancedb/` exists. |
| m11 | **`get_dead_code` default hides zombies** (FIXED) | `mcp_server.py:982` | Default `min_confidence` was 0.6, hiding 0.5-confidence zombie findings. Fixed 2026-03-25: lowered default to 0.5. |
| m12 | **`repowise-server` not installed by default** (FIXED) | `packages/cli/pyproject.toml` | Fixed 2026-03-25: same as M7 — `repowise-server` is now a declared dependency of `repowise-cli`. |

---

## 9. Core Engine Code Review

### Ingestion Pipeline

| Component | Status | Notes |
|-----------|--------|-------|
| `FileTraverser` | Good | Multi-layer exclusion, binary detection, monorepo detection |
| `ASTParser` | Good | Single class for all languages via tree-sitter queries |
| `GraphBuilder` | Good | PageRank, betweenness, SCC, community detection |
| `ChangeDetector` | Good | Symbol rename detection, cascade budget |
| `GitIndexer` | Good | Parallel git log mining, co-change analysis |
| `SpecialHandlers` | Good | OpenAPI, Dockerfile, CI YAML, Makefile, Protobuf, GraphQL |

### Generation Engine

| Component | Status | Notes |
|-----------|--------|-------|
| `PageGenerator` | Good | Hierarchical 8-level generation order |
| `ContextAssembler` | Excellent | 9-priority context assembly with token budgeting |
| `JobSystem` | Adequate | JSON-based checkpointing, not database-backed |

### Persistence Layer

| Component | Status | Notes |
|-----------|--------|-------|
| `models.py` (ORM) | Good | Complete schema for all entities |
| `crud.py` | Good | Async CRUD with batch operations |
| `database.py` | Good | SQLite + PostgreSQL abstraction |
| `search.py` (FTS) | Good | SQLite FTS5 integration |
| `vector_store.py` | **Issues** | LanceDB filter injection (C1), mock embedder concerns |
| `embedder.py` | Good | Clean abstraction with Gemini + OpenAI implementations |

### Provider Layer

| Component | Status | Notes |
|-----------|--------|-------|
| `base.py` | Good | Clean abstract interface |
| `registry.py` | Good | Dynamic provider registration |
| `anthropic.py` | Good | Batch API + prompt caching support |
| `openai.py` | Good | Standard implementation |
| `gemini.py` | Good | Google GenAI SDK integration |
| `ollama.py` | Good | Local model support |
| `litellm.py` | Good | 100+ provider support via LiteLLM |
| `mock.py` | Good | Test double |
| `rate_limiter.py` | Adequate | Token-bucket with backoff, potential starvation risk |

### Analysis

| Component | Status | Notes |
|-----------|--------|-------|
| `dead_code.py` | Good | Graph-based detection, conservative confidence |
| `decision_extractor.py` | Good | 4 extraction sources, staleness tracking |

---

## 10. Recommendations

### Immediate Fixes (before next release)

1. ~~**Fix LanceDB filter injection**~~ — DONE (C2)
2. ~~**Fix MCP embedder**~~ — DONE (M1)
3. ~~**Fix `update_cmd.py`**~~ — DONE (M5)
4. ~~**Install repowise-server in Makefile**~~ — DONE (M7): added as dependency in `packages/cli/pyproject.toml`
5. ~~**Update tool count**~~ — DONE (C3): updated to 8 tools everywhere

### Short-term Improvements

6. ~~**Add `edge_type` to `graph_edges`**~~ — DONE (M4): Alembic migration 0004, CRUD, init_cmd, MCP server
7. **Persist non-file graph nodes** — symbols, packages, external nodes (still open)
8. ~~**Fix `total_pages` tracking**~~ — DONE (M3): now queries DB count
9. ~~**Fix CLI semantic search**~~ — DONE (M2)
10. ~~**Fix HTML export**~~ — DONE (m1)

### Architecture Improvements (longer term)

11. **Lazy graph loading in MCP** — don't load all edges per `get_dependency_path` call
12. **Configurable ownership depth** — support nested module grouping (m7 still open)
13. ~~**MCP startup validation**~~ — DONE (m5)
14. **Database-backed job system** — replace JSON checkpointing with SQL
15. **Rate limiter starvation guard** — add max-wait timeout

### Additional fixes applied 2026-03-25 (second pass)

- **`search_codebase` page_type filter** — over-fetch (3x limit) when filtering by page_type to avoid returning 0 results
- **`search_codebase` confidence_score** — batch-lookup `Page.confidence` from DB instead of always returning None
- **`get_dependency_path` error consistency** — all error cases now return `{"path": [], "distance": -1, "explanation": "..."}`
- **`get_dead_code` docstring** — corrected default from 0.6 to 0.5
- **`get_dead_code` safe_only** — moved filter to SQL WHERE clause for efficiency
- **`get_context` module LIKE prefix** — added trailing `/` to prevent `src/auth` matching `src/auth_helpers`
- **`get_overview` operator precedence** — fixed content duplication bug with parentheses + word-boundary truncation
- **Architecture diagram sanitization** — regex-based `_sanitize_mermaid_id()` replaces all non-alphanumeric chars
- **Architecture diagram prioritization** — edges sorted by source node PageRank before 50-edge cap
- **`completed_pages > total_pages`** — `complete_page()` now clamps total to max(total, completed)
- **6 pre-existing test failures fixed** — dead_code, git_indexer, parser, cost_estimator, registry, gemini_provider tests

---

## Appendix: Test Data Summary

### interview-coach Repo Statistics

| Metric | Value |
|--------|-------|
| Total files indexed | 3,557 |
| Dependency edges | 8,080 (graph) / 9,506 (DB) |
| Symbols parsed | 12,807 (findings) / 12,955 (DB) |
| Git commits analyzed | 500 |
| Files with git metadata | 2,022 |
| Contributors | 2 (RaghavChamadiya, swati510) |
| Languages | TypeScript (1,144), Python (802), Markdown (682), JSON (168), SQL (114), JS (37) |
| Hotspot files | ~15 files with >10 commits in 90 days |
| Circular dependencies | 22 SCCs detected |
| Dead code findings | 1,233 (1,230 unreachable + 3 zombie packages) |
| Decision records | 23 (all proposed) |
| Wiki pages generated | 721 |
| Total tokens used | 4,223,332 |

### Top Hotspot Files

| File | 90d Commits | Owner |
|------|------------|-------|
| `frontend/components/problems/DSAPatternTable.tsx` | 51 | swati510 |
| `frontend/app/dashboard/page.tsx` | 20 | swati510 |
| `frontend/app/page.tsx` | 18 | swati510 |
| `frontend/components/layout/TechnicalCoachingSidebar.tsx` | 17 | swati510 |
| `frontend/app/layout.tsx` | 15 | RaghavChamadiya |
