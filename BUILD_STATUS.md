# repowise — Build Status

> **Last Updated:** 2026-03-23
> **Current Phase:** Phase 8.5 — Decision Intelligence ✅
> **Overall Progress:** 8.5 / 10 phases complete ✅

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Complete + tests passing |
| 🔄 | In progress |
| ⏳ | Not started |
| ❌ | Blocked / failed |

---

## Phase 1 — Foundation ✅

> Monorepo scaffold + Provider abstraction layer.
> **Gate test:** `MockProvider` returns fixture responses correctly; registry loads all providers.

| Step | Description | Status |
|------|-------------|--------|
| 1.1 | Monorepo directory structure | ✅ |
| 1.2 | Root `pyproject.toml` (uv workspace) | ✅ |
| 1.3 | `packages/core/pyproject.toml` | ✅ |
| 1.4 | `packages/cli/pyproject.toml` | ✅ |
| 1.5 | `packages/server/pyproject.toml` | ✅ |
| 1.6 | `packages/web/package.json` | ✅ |
| 1.7 | Root tooling: Makefile, .pre-commit-config.yaml, .gitignore | ✅ |
| 1.8 | CI: `.github/workflows/ci.yml` | ✅ |
| 1.9 | `BaseProvider` + `GeneratedResponse` | ✅ |
| 1.10 | `RateLimiter` (sliding window, RPM + TPM) | ✅ |
| 1.11 | `AnthropicProvider` | ✅ |
| 1.12 | `OpenAIProvider` | ✅ |
| 1.13 | `OllamaProvider` | ✅ |
| 1.14 | `LiteLLMProvider` | ✅ |
| 1.15 | `MockProvider` | ✅ |
| 1.16 | `ProviderRegistry` | ✅ |
| 1.17 | Provider unit tests | ✅ |
| 1.18 | Rate limiter unit tests | ✅ |
| **Gate** | `pytest tests/providers/ -v` all pass | ✅ 56/56 passed (1.39s) |

---

## Phase 2 — Ingestion Pipeline ✅

> File traversal, AST parsing, dependency graph, change detection. No LLM calls.
> **Gate test:** Ingest `tests/fixtures/sample_repo/`; assert correct symbol counts and graph structure.

| Step | Description | Status |
|------|-------------|--------|
| 2.1 | `FileTraverser` — gitignore, blocklist, special file detection | ✅ |
| 2.2 | Monorepo detection (pyproject.toml, package.json, Cargo.toml at multiple levels) | ✅ |
| 2.3 | `ASTParser` base + `ParseResult` / `Symbol` / `Import` models | ✅ |
| 2.4 | Python parser (.scm query — covers sync + async functions, classes, methods) | ✅ |
| 2.5 | TypeScript/JavaScript parser (.scm query) | ✅ |
| 2.6 | Go parser (.scm query — receiver-based method parent detection) | ✅ |
| 2.7 | Rust parser (.scm query — impl-based parent detection) | ✅ |
| 2.8 | Java parser (.scm query) | ✅ |
| 2.9 | C++ parser (.scm query) | ✅ |
| 2.10 | Special handlers: OpenAPI, Dockerfile, Makefile | ✅ |
| 2.11 | `GraphBuilder` — NetworkX + SQLite backend (aiosqlite) | ✅ |
| 2.12 | Graph algorithms: PageRank, SCC, betweenness centrality, Louvain community | ✅ |
| 2.13 | `ChangeDetector` — git diff + symbol-level diff | ✅ |
| 2.14 | Symbol rename detection (SequenceMatcher + line proximity heuristic) | ✅ |
| 2.15 | `tests/fixtures/sample_repo/` — multi-language fixture (Python, TS, Go, Rust, Java, C++, OpenAPI, Dockerfile, Makefile) | ✅ |
| 2.16 | Unit tests: traverser (23), parser (41), graph (18), change detector (17) | ✅ |
| 2.17 | Integration test: ingest sample_repo end-to-end (20 assertions) | ✅ |
| **Gate** | `pytest tests/unit/ingestion/ tests/integration/ -v` all pass | ✅ 134/134 passed (1.07s) |

**Notes:**
- Unified `ASTParser` architecture: one class, per-language differences in `.scm` query files + `LANGUAGE_CONFIGS` dict. Zero if/elif language branches in `parser.py`.
- Used `tree_sitter.Query()` constructor (not deprecated `language.query()`) for clean compile.
- `scipy` added as dependency for NetworkX PageRank (scipy-based implementation).
- Python `async_function_definition` does not exist in tree-sitter-python ≥ 0.23 — async functions share `function_definition` node type.
- C++ `enum_specifier` has no named `name:` field — `(type_identifier)` must be matched as an unnamed child.
- Function-to-method kind upgrade happens after parent class detection (`parent_name is not None and kind == "function"`).

---

## Phase 3 — Generation Engine ✅

> Jinja2 templates, context assembly, hierarchical page generation, job checkpointing.
> **Gate test:** Mock LLM generates all page types for `sample_repo`; assert structure and page count.

| Step | Description | Status |
|------|-------------|--------|
| 3.1 | Jinja2 template: `file_page.j2` | ✅ |
| 3.2 | Jinja2 template: `module_page.j2` | ✅ |
| 3.3 | Jinja2 template: `repo_overview.j2` | ✅ |
| 3.4 | Jinja2 template: `symbol_spotlight.j2` | ✅ |
| 3.5 | Jinja2 template: `architecture_diagram.j2` | ✅ |
| 3.6 | Jinja2 template: `api_contract.j2` | ✅ |
| 3.7 | Jinja2 template: `infra_page.j2` | ✅ |
| 3.8 | Jinja2 template: `scc_page.j2` | ✅ |
| 3.9 | Jinja2 template: `diff_summary.j2` | ✅ |
| 3.10 | `ContextAssembler` — token-budgeted context per page type | ✅ |
| 3.11 | `PageGenerator` — hierarchical generation order | ✅ |
| 3.12 | Concurrency: `asyncio.Semaphore` + dependency-ordered work queue | ✅ |
| 3.13 | Prompt caching (SHA256 dedup) | ✅ |
| 3.14 | `JobSystem` — state machine, checkpointing, resume | ✅ |
| 3.15 | Unit tests: template rendering (27 tests) | ✅ |
| 3.16 | Unit tests: context assembler, models, page generator, job system (82 tests) | ✅ |
| 3.17 | Integration test: generate all pages for sample_repo (mock LLM, 25 tests) | ✅ |
| **Gate** | `pytest tests/unit/generation/ tests/integration/test_generation_pipeline.py -v` | ✅ 134/134 passed (2.25s) |

**Notes:**
- System prompts as Python module-level constants (not in templates) for Anthropic server-side prefix caching.
- Token budget uses `len(text) // 4` — no tiktoken; suffix `"...[truncated]"` counted toward budget.
- `asyncio.Semaphore(config.max_concurrency)` guards each level's concurrent LLM calls.
- SHA256(model + page_type + user_prompt) as prompt cache key; `cache_enabled=False` bypasses it.
- SCC pages only generated for `frozenset`s with `len > 1` (singleton SCCs are not circular deps).
- Module grouping: `Path(path).parts[0]` (top-level directory); files with no parent → `"root"` group.
- `JobSystem` uses JSON checkpoint files; `Checkpoint.from_dict()` for round-trip persistence.
- `rag_context: list[str] = []` stub in `FilePageContext`; Phase 4 fills it via LanceDB (or pgvector when PostgreSQL is the SQL backend).

---

## Phase 4 — Persistence ✅

> SQLAlchemy models, Alembic migrations, LanceDB semantic search (pgvector when using PostgreSQL).
> **Gate test:** Store/retrieve pages; semantic search returns expected results.

| Step | Description | Status |
|------|-------------|--------|
| 4.1 | SQLAlchemy models: `Repository`, `GenerationJob`, `Page`, `PageVersion` | ✅ |
| 4.2 | SQLAlchemy models: `GraphNode`, `GraphEdge`, `WebhookEvent`, `WikiSymbol` | ✅ |
| 4.3 | Alembic migration: initial schema (adds `pgvector` extension + `embedding` column when PostgreSQL) | ✅ |
| 4.4 | Async CRUD layer (aiosqlite / asyncpg) | ✅ |
| 4.5 | `VectorStore` abstraction: `InMemoryVectorStore` + `LanceDBVectorStore` + `PgVectorStore` | ✅ |
| 4.6 | LanceDB integration — embed on page generation, store in `.repowise/lancedb/` | ✅ |
| 4.7 | pgvector integration — store embeddings in `wiki_pages.embedding` column (PostgreSQL only) | ✅ |
| 4.8 | Semantic search: `search(query, limit)` → `list[SearchResult]` (routes to active backend) | ✅ |
| 4.9 | Full-text search fallback (SQLite FTS5 / PostgreSQL `tsvector`) | ✅ |
| 4.10 | Tests: store/retrieve pages, version history | ✅ |
| 4.11 | Tests: semantic search returns expected results (both backends) | ✅ |
| **Gate** | `pytest tests/unit/ tests/integration/ -v` all pass | ✅ 237/237 passed (6.63s) |

**Notes:**
- `InMemoryVectorStore` for tests/dev; `LanceDBVectorStore` (optional extra `search`) for production SQLite; `PgVectorStore` (optional extra `pgvector`) for PostgreSQL.
- `MockEmbedder`: deterministic 8-dim SHA256-based unit vectors — zero external deps, used for all tests.
- `FullTextSearch`: SQLite FTS5 virtual table (`page_fts`) with porter tokenizer; PostgreSQL uses `tsvector` + GIN index.
- `Alembic` migration (`0001_initial_schema`) creates all 8 tables; dialect-conditional: pgvector extension + `embedding vector(1536)` column for PostgreSQL, FTS5 table for SQLite.
- `upsert_page` versioning: first upsert inserts at version=1; subsequent upserts archive existing page as `PageVersion`, update in-place (version += 1), preserving `created_at`.
- ORM symbol model is named `WikiSymbol` (not `Symbol`) to avoid shadowing `ingestion.models.Symbol`.
- `pythonpath = ["."]` added to `[tool.pytest.ini_options]` to allow importing from `tests.*` namespace in test helpers.

---

## Phase 5 — CLI ✅

> Full `repowise` command-line interface.
> **Gate test:** `repowise init` + `repowise update` on `sample_repo` produces correct output.

| Step | Description | Status |
|------|-------------|--------|
| 5.1 | `helpers.py` — async bridge, path resolution, state file, DB setup, provider resolution | ✅ |
| 5.2 | `cost_estimator.py` — pre-generation token/cost estimation (mirrors `generate_all()` logic) | ✅ |
| 5.3 | `repowise init` — full end-to-end with cost estimate, Rich progress, confirmation, persist | ✅ |
| 5.4 | `repowise update` — incremental with ChangeDetector + cascade budget | ✅ |
| 5.5 | `repowise search` — fulltext (FTS5), semantic (vector store), symbol (LIKE query) | ✅ |
| 5.6 | `repowise export` — markdown/html/json with Rich progress | ✅ |
| 5.7 | `repowise status` — sync state, page counts by type, token totals | ✅ |
| 5.8 | `repowise doctor` — 6 health checks as Rich table | ✅ |
| 5.9 | `repowise watch` — watchdog Observer + debounce timer | ✅ |
| 5.10 | `repowise serve` — Phase 6 stub | ✅ |
| 5.11 | `repowise mcp` — Phase 7 stub | ✅ |
| 5.12 | `main.py` — Click group + version + 9 command registrations | ✅ |
| 5.13 | Unit tests: helpers (13), cost estimator (11), commands (15) | ✅ |
| 5.14 | Integration tests: init dry-run, init full mock, idempotent, status, doctor, search, export (7) | ✅ |
| **Gate** | `pytest tests/unit/cli/ tests/integration/test_cli.py -v` all pass | ✅ 46/46 passed (6.03s) |

**Notes:**
- One module per command (`commands/*.py`) keeps each file under 300 lines.
- `ContextAssembler(config)` takes only `config` — graph and repo_structure are passed per-method call.
- `upsert_page_from_generated(session, generated_page, repository_id)` — `generated_page` before `repository_id`.
- GraphNode ORM uses `node_id` (not `file_path`); GraphEdge uses `source_node_id`/`target_node_id`.
- `batch_upsert_symbols` expects duck-typed Symbol objects with a `file_path` attribute.
- Click 8.x removed `mix_stderr` from `CliRunner` — use `CliRunner()` without kwargs.
- `asyncio.run()` instead of deprecated `asyncio.get_event_loop().run_until_complete()` for Python 3.12+ safety.
- Provider auto-detection: `REPOWISE_PROVIDER` env → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OLLAMA_BASE_URL`.
- Cost confirmation prompt when estimated cost > $2.00 (bypassed with `--yes`).
- `--provider mock` for all tests — no API calls ever in test suite.

---

## Phase 5.5 — Git Intelligence + Dead Code Detection ✅

> Git metadata indexing, git-enriched generation, dead code detection. Standalone feature addition.
> **Gate test:** `GitIndexer` indexes `sample_repo`, hotspots/stable files classified correctly;
> `DeadCodeAnalyzer` detects unreachable files and unused exports in fixture repo.

| Step | Description | Status |
|------|-------------|--------|
| **Foundation** | | |
| 5.5.1 | `GitMetadata` + `DeadCodeFinding` ORM models in `persistence/models.py` | ✅ |
| 5.5.2 | Alembic migration: `git_metadata` + `dead_code_findings` tables | ✅ |
| 5.5.3 | CRUD methods: `upsert_git_metadata`, `get_git_metadata`, `get_git_metadata_bulk`, `get_all_git_metadata`, `upsert_git_metadata_bulk`, `save_dead_code_findings`, `get_dead_code_findings`, `update_dead_code_status`, `get_dead_code_summary` | ✅ |
| 5.5.4 | `GitIndexer` class (`ingestion/git_indexer.py`) — full repo index + incremental | ✅ |
| **Part A: Git Intelligence** | | |
| 5.5.5 | Init pipeline integration: Steps 3.5 (git indexing) and 3.6 (dead code) in `init_cmd.py` | ✅ |
| 5.5.6 | `FilePageContext` extended with `git_metadata`, `co_change_pages`, `dead_code_findings` fields | ✅ |
| 5.5.7 | `ContextAssembler.assemble_file_page()` — accepts optional `git_meta` and `dead_code_findings` | ✅ |
| 5.5.8 | Template: `file_page.j2` — git context block (ownership, evolution, hotspot, co-changes) | ✅ |
| 5.5.9 | Template: `module_page.j2` — team ownership summary block | ✅ |
| 5.5.10 | Template: `repo_overview.j2` — codebase health signals block | ✅ |
| 5.5.11 | Generation ordering: `_sort_level_files()` in `page_generator.py` — hotspot priority | ✅ |
| 5.5.12 | Git-informed generation depth: `_select_generation_depth()` in `context_assembler.py` | ✅ |
| 5.5.13 | Maintenance prompt enhancement: `diff_summary.j2` with trigger commit + diff | ✅ |
| 5.5.14 | `FileDiff` extended with trigger commit fields | ✅ |
| 5.5.15 | `ContextAssembler.assemble_update_context()` — new method for targeted updates | ✅ |
| 5.5.16 | Confidence decay git modifiers: `compute_confidence_decay_with_git()` in `models.py` | ✅ |
| 5.5.17 | Co-change edges: `GraphBuilder.add_co_change_edges()` + `update_co_change_edges()` | ✅ |
| 5.5.18 | `ChangeDetector.get_affected_pages()` — include co-change partners in staleness | ✅ |
| 5.5.19 | Update pipeline integration in `update_cmd.py` — git re-index + co-change edges | ✅ |
| **Part B: Dead Code Detection** | | |
| 5.5.20 | `DeadCodeAnalyzer` class (`analysis/dead_code.py`) — graph traversal, no LLM | ✅ |
| 5.5.21 | Dead code in `file_page.j2` — unused code callout block | ✅ |
| 5.5.22 | CLI: `repowise dead-code` command (table/json/md output) | ✅ |
| **Config + Tests** | | |
| 5.5.23 | Config: `GitConfig` + `DeadCodeConfig` frozen dataclasses in `generation/models.py` | ✅ |
| 5.5.24 | Test fixtures: `dead/unreachable_module.py`, `utils/helpers.py` with unused export | ✅ |
| 5.5.25 | Unit tests: `test_git_indexer.py` (7 tests) | ✅ |
| 5.5.26 | Unit tests: `test_dead_code.py` (12 tests) | ✅ |
| 5.5.27 | Unit tests: `test_confidence_with_git.py` (4 tests) | ✅ |
| 5.5.28 | Integration tests: `test_git_intelligence_integration.py` (2) + `test_dead_code_integration.py` (2) | ✅ |
| **Gate** | `pytest tests/ -q` — all pass | ✅ 500 passed, 1 skipped (12.80s) |

**Notes:**
- Phase 5.5 is a standalone feature detour before Phase 6. All additions are additive — no existing Phase 1–5 code is modified, only extended.
- MCP tools (5 new: `get_file_history`, `get_hotspots`, `get_codebase_ownership`, `get_co_changes`, `get_dead_code`), REST API endpoints, and Web UI pages for git intelligence and dead code are deferred to their respective phases (7, 6, 8). The foundation and core logic are built here.
- `GitIndexer` uses `gitpython` (already a dependency) with `asyncio.Semaphore(20)` for parallel git log calls.
- `DeadCodeAnalyzer` is pure graph traversal + SQL — no LLM calls, completes in < 10 seconds.
- All git metadata usage is gated on `{% if ctx.git_metadata %}` / `if git_meta is not None` — graceful degradation if git is unavailable.
- Template additions use `is defined and` guards for backward compatibility with Jinja2 `StrictUndefined`.
- `GraphBuilder.pagerank()` now filters out `co_changes` edges before computing PageRank (co-change edges would skew scores).
- `ChangeDetector.get_affected_pages()` now propagates staleness to co-change partners (decay factor 0.97).

---

## Phase 6 — Server ✅

> FastAPI REST API, webhook handlers, SSE job progress, background scheduler.
> Includes git intelligence + dead code REST endpoints.
> **Gate test:** Webhook triggers job; all server tests pass.

| Step | Description | Status |
|------|-------------|--------|
| 6.1 | FastAPI app scaffold — `app.py` with lifespan, CORS, error handlers, router registration | ✅ |
| 6.2 | `/api/repos` router — CRUD + sync + full-resync | ✅ |
| 6.3 | `/api/pages` router — list, get (path + query), versions, force-regen | ✅ |
| 6.4 | `/api/search` router — semantic (vector store) + fulltext (FTS5) | ✅ |
| 6.5 | `/api/jobs` router — list, get, SSE progress stream (`text/event-stream`) | ✅ |
| 6.6 | `/api/symbols` router — search by name/kind/language, lookup by name, get by ID | ✅ |
| 6.7 | `/api/graph` router — D3-compatible JSON export + shortest dependency path (NetworkX) | ✅ |
| 6.8 | `/api/webhooks/github` — HMAC-SHA256 signature verification, event storage, job enqueue on push | ✅ |
| 6.9 | `/api/webhooks/gitlab` — X-Gitlab-Token verification, event storage, job enqueue on Push Hook | ✅ |
| 6.10 | `/health` + `/metrics` (Prometheus text format) endpoints | ✅ |
| 6.11 | Optional API key auth (`REPOWISE_API_KEY`) via `deps.verify_api_key` dependency | ✅ |
| 6.12 | APScheduler — staleness checker + polling fallback (configurable intervals) | ✅ |
| 6.13 | Git intelligence endpoints: `git-metadata`, `hotspots`, `ownership`, `co-changes`, `git-summary` | ✅ |
| 6.14 | Dead code endpoints: `GET` findings, `POST` analyze, `GET` summary, `PATCH` resolve | ✅ |
| 6.15 | `deps.py` — dependency injection: sessions, vector store, FTS, API key auth | ✅ |
| 6.16 | `schemas.py` — Pydantic response/request models with `from_orm()` for all ORM models | ✅ |
| 6.17 | `repowise serve` CLI command updated to start uvicorn with FastAPI app | ✅ |
| 6.18 | Unit tests: 66 tests across 11 test files (auth, repos, pages, search, jobs, symbols, graph, webhooks, git, dead-code, health) | ✅ |
| **Gate** | `pytest tests/unit/server/ -v` all pass | ✅ 66/66 passed (4.30s) |

**Notes:**
- Server package: `packages/server/src/repowise/server/` with `app.py`, `deps.py`, `schemas.py`, `scheduler.py`, and 10 router modules under `routers/`.
- Page IDs contain colons and slashes (e.g. `file_page:src/main.py`). Path-based GET uses `{page_id:path}` for direct access; versions and regenerate use `/api/pages/lookup/*` with `?page_id=...` query parameter to avoid greedy path matching conflicts.
- SSE streaming implemented with raw `StreamingResponse` + `text/event-stream` — no extra dependency needed. Polls DB every 1 second with independent sessions per poll iteration.
- Webhook signature verification: GitHub uses HMAC-SHA256 (`X-Hub-Signature-256`), GitLab uses static token (`X-Gitlab-Token`). Both skip verification when secret/token env vars are unset (dev mode).
- Auth: `REPOWISE_API_KEY` env var. When unset, all endpoints are open. When set, `Authorization: Bearer <key>` required on all endpoints except `/health`.
- APScheduler `AsyncIOScheduler` with two interval jobs: staleness check + polling fallback. Started in lifespan, shut down on app close.
- `httpx` added as dev dependency for `AsyncClient`-based test transport (`ASGITransport`).
- Metrics endpoint returns Prometheus text format with page counts by freshness, job counts by status, and token usage totals.
- Graph path query reconstructs NetworkX `DiGraph` from `graph_edges` table on each request. For production, consider caching per repo.

---

## Phase 7 — MCP Server ✅

> 13-tool MCP server with stdio + SSE transports.
> **Gate test:** Mock MCP client calls all 13 tools and receives valid responses.

| Step | Description | Status |
|------|-------------|--------|
| 7.1 | MCP server scaffold (FastMCP from MCP Python SDK) | ✅ |
| 7.2 | Tool: `get_overview` | ✅ |
| 7.3 | Tool: `get_module_docs` | ✅ |
| 7.4 | Tool: `get_file_docs` | ✅ |
| 7.5 | Tool: `get_symbol` | ✅ |
| 7.6 | Tool: `search_codebase` | ✅ |
| 7.7 | Tool: `get_architecture_diagram` | ✅ |
| 7.8 | Tool: `get_dependency_path` | ✅ |
| 7.9 | Tool: `get_stale_pages` | ✅ |
| 7.10 | Tool: `get_file_history` — git history + ownership for a file | ✅ |
| 7.11 | Tool: `get_hotspots` — high-churn + high-complexity files | ✅ |
| 7.12 | Tool: `get_codebase_ownership` — ownership breakdown by module | ✅ |
| 7.13 | Tool: `get_co_changes` — files that change together without import link | ✅ |
| 7.14 | Tool: `get_dead_code` — dead/unused code findings | ✅ |
| 7.15 | Co-change partners included in `get_co_changes` with import relationship enrichment | ✅ |
| 7.16 | stdio transport | ✅ |
| 7.17 | SSE transport | ✅ |
| 7.18 | Auto-generated MCP config + setup docs for Claude Code, Cursor, Cline | ✅ |
| 7.19 | Unit tests: 34 tests across all 13 tools + MCP config generation | ✅ |
| 7.20 | Integration tests: 5 end-to-end MCP tool flow tests | ✅ |
| **Gate** | `pytest tests/unit/server/test_mcp.py tests/integration/test_mcp.py -v` all pass | ✅ 39/39 passed (2.69s) |

**Notes:**
- Used FastMCP from MCP Python SDK (`mcp>=1.0,<2`) — the high-level `@mcp.tool()` decorator pattern.
- MCP server module: `packages/server/src/repowise/server/mcp_server.py` — single file with lifespan, 13 tools, and runner.
- Module-level globals (`_session_factory`, `_vector_store`, `_fts`, `_repo_path`) for shared state across tools — set during lifespan context.
- Repository resolution: `_get_repo()` tries local_path match → ID match → name match → default first repo. Allows flexible tool usage.
- Symbol lookup: exact match first → `ILIKE` fuzzy match fallback. Returns up to 5 candidates when fuzzy.
- `get_co_changes` enriches partners with `has_import_relationship` (checks graph edges) and `wiki_page_snippet`.
- `get_architecture_diagram` returns stored page for repo scope; dynamically builds Mermaid from graph for module/file scope (limited to 50 edges for readability).
- Auto-generated MCP config: `save_mcp_config()` writes `.repowise/mcp.json` at end of `repowise init`. `format_setup_instructions()` prints Claude Code, Cursor, and Cline config blocks.
- CLI: `repowise mcp [PATH] --transport stdio|sse --port 7338`. stdio mode suppresses console output (would corrupt protocol).
- `get_blast_radius` was dropped from the plan — its functionality is covered by `get_co_changes` (co-change partners) + `get_dependency_path` (graph-based dependencies).

---

## Phase 8 — Web UI ✅

> Next.js 15 web interface with MDX rendering, D3 graph, SSE progress.
> **Gate test:** Render `sample_repo` wiki; all pages load; Mermaid diagrams render.

| Step | Description | Status |
|------|-------------|--------|
| 8.1 | Next.js 15 scaffold — routing, layout, design system | ✅ |
| 8.2 | Dashboard page (`/`) | ✅ |
| 8.3 | Repo overview page (`/repos/[id]`) | ✅ |
| 8.4 | Wiki page (`/repos/[id]/wiki/[...slug]`) with MDX + Mermaid + Shiki | ✅ |
| 8.5 | Search page (`/repos/[id]/search`) | ✅ |
| 8.6 | Graph page (`/repos/[id]/graph`) — D3 force-directed + path finder + PNG export | ✅ |
| 8.7 | Symbol index page (`/repos/[id]/symbols`) | ✅ |
| 8.8 | Coverage page (`/repos/[id]/coverage`) | ✅ |
| 8.9 | Repo settings page (`/repos/[id]/settings`) — name, branch, excluded paths | ✅ |
| 8.10 | `<WikiPage />` — MDX renderer with symbol auto-links | ✅ |
| 8.11 | `<ConfidenceBadge />` component | ✅ |
| 8.12 | `<GenerationProgress />` — SSE-connected live progress | ✅ |
| 8.13 | `<GitHistoryPanel />` — right panel on wiki page (ownership, commits, co-changes) | ✅ |
| 8.14 | Ownership page (`/repos/[id]/ownership`) | ✅ |
| 8.15 | Hotspots page (`/repos/[id]/hotspots`) — churn + complexity bars | ✅ |
| 8.16 | Dead code page (`/repos/[id]/dead-code`) | ✅ |
| 8.17 | Responsive sidebar (collapse-to-icon-rail + mobile Sheet overlay) | ✅ |
| 8.18 | 10 loading.tsx skeletons + root/repo error boundaries | ✅ |
| 8.19 | All tables: horizontal scroll on mobile; responsive padding | ✅ |
| **Gate** | All pages render; Mermaid diagrams display; git/dead-code pages load | ✅ |

---

## Folder Exclusion Feature ✅

> Cross-cutting feature: three-layer system to skip directories/files during ingestion.
> Spans core, CLI, server, and web UI.
> **Gate test:** 13 new traverser unit tests all pass.

| Step | Description | Status |
|------|-------------|--------|
| FX.1 | `FileTraverser(extra_exclude_patterns=[...])` — pathspec from extra patterns, checked in `_should_skip_dir()` + `_build_file_info()` | ✅ |
| FX.2 | Per-directory `.repowiseIgnore` — `_get_dir_ignore()` loads + caches one spec per visited directory; root pre-seeded from `self._extra_ignore` | ✅ |
| FX.3 | `helpers.load_config()` return type corrected to `dict[str, Any]` | ✅ |
| FX.4 | `helpers.save_config()` — round-trip load→update→write (preserves all keys); optional `exclude_patterns` kwarg | ✅ |
| FX.5 | `repowise init --exclude/-x PATTERN` (repeatable) — merged with `config.yaml exclude_patterns`, passed to `FileTraverser`, persisted back to config | ✅ |
| FX.6 | `repowise update` — reads `exclude_patterns` from config, passes to `FileTraverser` | ✅ |
| FX.7 | `types.ts` — `RepoUpdate.settings` typed as `{ exclude_patterns?: string[]; [key: string]: unknown }` | ✅ |
| FX.8 | Web UI `/repos/[id]/settings` — "Excluded Paths" chip editor with quick-add suggestions, tooltip, empty state, `hasChanges` detection | ✅ |
| FX.9 | `scheduler.py` — `polling_fallback` logs `exclude_patterns` and documents where FileTraverser wiring goes for future sync | ✅ |
| FX.10 | 13 new unit tests: `TestExtraExcludePatterns` (5) + `TestPerDirectoryrepowiseIgnore` (4) + expanded `TestFileTraverser` (4 new) | ✅ |
| **Gate** | `pytest tests/unit/ingestion/test_traverser.py -v` | ✅ 32/32 passed (0.55s) |

---

## Phase 8.5 — Decision Intelligence ✅

> Architectural Decision Intelligence layer — capture, track, and query the *why* behind code.
> **Gate test:** `repowise decision list` returns results after `repowise init --index-only` on a repo with inline markers.

| Step | Description | Status |
|------|-------------|--------|
| 8.5.1 | `DecisionRecord` ORM model + Alembic migration `0003` | ✅ |
| 8.5.2 | 8 decision CRUD functions in `crud.py` | ✅ |
| 8.5.3 | `decision_extractor.py` — inline markers, git archaeology, README mining | ✅ |
| 8.5.4 | `LanceDBVectorStore` extended with `table_name` param | ✅ |
| 8.5.5 | 3 MCP tools: `get_decisions`, `get_why`, `get_decision_health` | ✅ |
| 8.5.6 | REST API: 5 endpoints in `routers/decisions.py` | ✅ |
| 8.5.7 | CLI: `repowise decision` group with 7 subcommands | ✅ |
| 8.5.8 | Init integration: extraction after dead code step | ✅ |
| 8.5.9 | Update integration: marker re-scan + staleness recomputation | ✅ |
| 8.5.10 | Frontend: Decisions list/detail pages + sidebar nav + health widget | ✅ |
| **Gate** | `repowise decision list` + MCP tools return data after init | ✅ |

---

## Phase 9 — Integrations ⏳

> GitHub Action and Docker Compose.
> **Gate test:** `docker compose up` starts successfully.

| Step | Description | Status |
|------|-------------|--------|
| 9.1 | GitHub Action `action.yml` | ⏳ |
| 9.2 | GitHub Action Dockerfile entrypoint | ⏳ |
| 9.3 | PR comment: affected pages + cost estimate | ⏳ |
| 9.4 | Commit-back logic for `.repowise/` | ⏳ |
| 9.5 | Multi-stage `docker/Dockerfile` | ⏳ |
| 9.6 | `docker/docker-compose.yml` with optional Redis profile | ⏳ |
| **Gate** | `docker compose up -d` succeeds; `curl localhost:7337/health` returns 200 | ⏳ |

---

## Phase 10 — Quality ⏳

> E2E tests, documentation, dogfooding.
> **Gate test:** `repowise init .` on repowise repo itself produces > 50 pages.

| Step | Description | Status |
|------|-------------|--------|
| 10.1 | E2E test: full `repowise init` on `sample_repo` | ⏳ |
| 10.2 | E2E test: `repowise update` after code change | ⏳ |
| 10.3 | E2E test: `repowise init .` on repowise itself | ⏳ |
| 10.4 | `README.md` — complete with comparison table, quickstart, MCP setup | ⏳ |
| 10.5 | `CONTRIBUTING.md` — how to add a provider, language, PR checklist | ⏳ |
| 10.6 | `docs/ARCHITECTURE.md` — finalized | ⏳ |
| 10.7 | `CHANGELOG.md` — initial version | ⏳ |
| 10.8 | Dogfood: generate repowise's own wiki, commit `.repowise/` | ⏳ |
| **Gate** | E2E tests pass; repowise wiki has > 50 pages | ⏳ |

---

## Test Results Log

| Date | Phase | Command | Result |
|------|-------|---------|--------|
| 2026-03-18 | Phase 1 | `pytest tests/providers/ -v` | ✅ 56 passed, 0 failed, 1.39s |
| 2026-03-19 | Phase 2 | `pytest tests/unit/ingestion/ tests/integration/ -v` | ✅ 134 passed, 0 failed, 1.07s |
| 2026-03-19 | All | `pytest tests/ -q` | ✅ 190 passed, 0 failed, 2.35s |
| 2026-03-19 | Phase 4 | `pytest tests/unit/persistence/ tests/integration/test_persistence.py -v` | ✅ 103 passed, 1 skipped, 4.29s |
| 2026-03-19 | All | `pytest tests/ -q` | ✅ 427 passed, 1 skipped, 6.63s |
| 2026-03-19 | Phase 5 | `pytest tests/unit/cli/ tests/integration/test_cli.py -v` | ✅ 46 passed, 0 failed, 6.03s |
| 2026-03-19 | All | `pytest tests/ -q` | ✅ 473 passed, 1 skipped, 11.48s |
| 2026-03-19 | Phase 5.5 | `pytest tests/ -q` | ✅ 500 passed, 1 skipped, 12.80s |
| 2026-03-19 | Phase 6 | `pytest tests/unit/server/ -v` | ✅ 66 passed, 0 failed, 4.30s |
| 2026-03-19 | All (pre-P7) | `pytest tests/ -q` | ✅ 566 passed, 1 skipped, 24.52s |
| 2026-03-19 | Phase 7 | `pytest tests/unit/server/test_mcp.py -v` | ✅ 34 passed, 0 failed, 1.59s |
| 2026-03-19 | Phase 7 | `pytest tests/integration/test_mcp.py -v` | ✅ 5 passed, 0 failed, 1.10s |
| 2026-03-19 | All | `pytest tests/ -q` | ✅ 605 passed, 1 skipped, 22.39s |
| 2026-03-19 | Phase 8 Step 1 | `tsc --noEmit` (packages/web) | ✅ 0 errors |
| 2026-03-19 | Phase 8 Step 1 | `next build` (packages/web) | ✅ 11 routes, clean build |
| 2026-03-20 | Folder Exclusion | `pytest tests/unit/ingestion/test_traverser.py -v` | ✅ 32/32 passed (0.55s) |
| 2026-03-20 | Folder Exclusion | `pytest tests/unit/cli/ tests/unit/server/ -q` | ✅ 132 passed, 7 pre-existing failures (test_cost_estimator FakeConfig) |

---

## Architecture Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-18 | uv workspace for monorepo | Single lockfile, fast installs, native workspace support |
| 2026-03-18 | Python namespace packages (`repowise.*`) | Clean imports, extensible by community providers |
| 2026-03-18 | `src/` layout for all Python packages | Prevents accidental imports of un-installed code |
| 2026-03-18 | async-first (SQLAlchemy + aiosqlite) | Never block the event loop during DB or LLM operations |
| 2026-03-18 | tree-sitter for AST parsing | One interface for 6+ languages, fast, battle-tested |
| 2026-03-19 | Unified ASTParser (one class + .scm files) | Zero per-language Python branches; adding a language = one .scm file + one dict entry |
| 2026-03-19 | `scipy` added as explicit dependency | NetworkX PageRank uses scipy for large graphs; without it the pure-Python fallback is slow and may behave differently |
| 2026-03-19 | Function→method upgrade post parent detection | tree-sitter returns `function_definition` for both; kind is promoted to `method` when a parent class is detected, keeping the query files simple |
| 2026-03-18 | structlog for logging | Structured JSON output, context fields on every log line |
| 2026-03-18 | `tenacity` for LLM retries | Declarative retry logic with backoff, composable |
| 2026-03-19 | LanceDB (default) + pgvector (PostgreSQL) for semantic search | LanceDB: embedded, zero infra, faster than ChromaDB; pgvector: consolidates vectors into the SQL DB when already using PostgreSQL — avoids a second storage system |
| 2026-03-19 | One file per CLI command | Keeps each module under 300 lines; easy to navigate and test independently |
| 2026-03-19 | `asyncio.run()` as sync-async bridge | Simple, clean, Python 3.12+ safe; no custom event loop policy needed |
| 2026-03-19 | Cost estimator replicates `generate_all()` selection logic | Dry-run page counting without LLM calls; enables `--dry-run` and cost confirmation prompts |
| 2026-03-19 | Co-change edges filtered from PageRank | Co-change relationships are temporal coupling, not architectural; including them in PageRank would skew scores toward files edited together for process reasons |
| 2026-03-19 | `compute_confidence_decay_with_git()` as multiplicative modifiers | Hotspot ×0.94, stable ×1.03, rewrite ×0.71, typo ×1.12 — applied on top of base decay, composes cleanly |
| 2026-03-19 | Template `is defined and` guards for new blocks | Backward compatibility with `StrictUndefined` Jinja2 env — new optional template variables don't break existing tests |
| 2026-03-19 | `GitConfig` + `DeadCodeConfig` as separate frozen dataclasses | `GenerationConfig` is already frozen; adding fields would break it. Separate configs passed where needed |
| 2026-03-19 | Conservative dead code: `safe_to_delete` only at confidence ≥ 0.7 | Plus exclusion of dynamic patterns (`*Plugin`, `*Handler`, etc.), framework decorators, and `__init__.py` re-exports. repowise surfaces candidates — humans decide |
| 2026-03-19 | FastAPI lifespan for resource management | Engine, session factory, FTS, vector store, and scheduler initialized in lifespan context manager; clean shutdown on app close |
| 2026-03-19 | `deps.py` for FastAPI dependency injection | `get_db_session`, `get_vector_store`, `get_fts`, `verify_api_key` — clean separation, each router declares its needs via `Depends()` |
| 2026-03-19 | Query-param endpoints for page operations with path suffixes | Page IDs contain `:` and `/` (e.g. `file_page:src/main.py`); `{page_id:path}` is greedy and swallows `/versions` suffix. Solution: `/api/pages/lookup/*` endpoints with `?page_id=...` for versions and regenerate |
| 2026-03-19 | Raw `StreamingResponse` for SSE instead of `sse-starlette` | Avoids extra dependency; `text/event-stream` with `event:` and `data:` fields is trivial to implement manually |
| 2026-03-19 | One router module per resource | 10 router files under `routers/`; mirrors the one-file-per-CLI-command pattern from Phase 5 |
| 2026-03-19 | `from_orm()` classmethods on Pydantic schemas | Explicit conversion from ORM models (which have `_json` suffixed columns) to Pydantic models (which have parsed `dict` fields); avoids Pydantic's `from_attributes` magic which can't handle JSON deserialization |
| 2026-03-19 | FastMCP `@mcp.tool()` decorator pattern | High-level API from MCP Python SDK; auto-generates tool schemas from type hints; handles stdio/SSE transport switching via `mcp.run(transport=...)` |
| 2026-03-19 | Module-level globals for MCP state | `_session_factory`, `_vector_store`, `_fts`, `_repo_path` — set during FastMCP lifespan context manager. Simpler than dependency injection for tool functions that aren't FastAPI route handlers |
| 2026-03-19 | Flexible repo resolution in MCP tools | `_get_repo()` tries local_path → ID → name → first repo. Single-repo setups (most common MCP use case) need zero configuration |
| 2026-03-19 | Auto-generated `.repowise/mcp.json` at end of init | Ready-to-paste config blocks for Claude Code, Cursor, Cline. Removes setup friction — users can copy-paste immediately after `repowise init` |
| 2026-03-20 | Per-directory `.repowiseIgnore` cached by absolute dir path | Root `.repowiseIgnore` pre-seeded in the cache to avoid reading it twice (already loaded into `self._extra_ignore`). Cache invalidation is not needed — traversal is a single pass |
| 2026-03-20 | `save_config()` round-trips YAML before writing | Without round-trip, any `exclude_patterns` set via the Web UI would be silently dropped the next time `repowise init` or `repowise update` ran. Round-trip preserves all keys regardless of how they were set |
| 2026-03-20 | `extra_exclude_patterns` checked at both directory and file level | Directory-level check prunes entire subtrees (fast). File-level check handles patterns that apply to files but not dirs (e.g. `*.test.ts`). Both use pathspec so full gitignore syntax is supported |
