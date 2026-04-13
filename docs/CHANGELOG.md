# Changelog

All notable changes to repowise will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Use `git-cliff` to auto-generate entries from conventional commits -->

---

## [0.3.1] — Unreleased

### Changed
- **PreToolUse hook** — replaced FTS-only file retrieval with multi-signal ranking: symbol name match (highest weight), file path match, then FTS on wiki content. Returns top 3 files instead of 5. Removed git signals (HOTSPOT, bus-factor, owner) from enrichment output — use `get_risk` for that. Removed Bash command interception. Dependencies shown as "Uses" (2 per file) alongside symbols (3) and importers (3).

---

## [0.3.0] — 2026-04-13

### Added

#### Multi-repo workspaces
- **Workspace support** — `repowise init .` from a parent directory scans for git repos (up to 3 levels deep), prompts for selection, and indexes each repo with cross-repo analysis. Config stored in `.repowise-workspace.yaml`.
- **Workspace CLI commands** — `repowise workspace list`, `workspace add <path>`, `workspace remove <alias>`, `workspace scan`, `workspace set-default <alias>` for managing repos in a workspace.
- **Workspace-aware MCP server** — a single MCP server instance serves all workspace repos. Tools accept an optional `repo` parameter to target a specific repo or `"all"` to query across the workspace. Lazy-loading with LRU eviction (max 5 repos loaded simultaneously).
- **Cross-repo co-change detection** — analyzes git history across repos to find files that frequently change in the same time window.
- **API contract extraction** — scans for HTTP route handlers (Express, FastAPI, Spring, Go), gRPC service definitions, and message topic publishers/subscribers. Matches providers with consumers across repos.
- **Package dependency scanning** — reads package manifests (`package.json`, `pyproject.toml`, `go.mod`, `pom.xml`) to detect inter-repo package dependencies.
- **Workspace CLAUDE.md** — auto-generated context file at the workspace root covering all repos, their relationships, cross-repo signals, and contract links.
- **Workspace web UI** — workspace dashboard (`/workspace`) with aggregate stats and repo cards, contracts view (`/workspace/contracts`) with provider/consumer matching, and co-changes view (`/workspace/co-changes`) with cross-repo file pairs ranked by strength.
- **Workspace update** — `repowise update --workspace` updates all stale repos in parallel (up to 4 concurrent) then re-runs cross-repo analysis. `--repo <alias>` targets a single repo.
- **Workspace watch** — `repowise watch --workspace` auto-updates all workspace repos on file change.

#### Auto-sync hooks
- **`repowise hook` CLI** — `repowise hook install` installs a marker-delimited post-commit git hook that runs `repowise update` in the background after every commit. `hook install --workspace` installs for all workspace repos. `hook status` and `hook uninstall` for management.
- **Proactive context enrichment via Claude Code hooks** — `repowise init` registers PreToolUse and PostToolUse hooks in `~/.claude/settings.json`. PreToolUse enriches every `Grep`/`Glob` call with graph context (importers, dependencies, symbols, git signals) at ~24ms latency. PostToolUse detects git commits and notifies the agent when the wiki is stale.
- **Polling scheduler** — when the server is running, a background job polls registered repositories every 15 minutes and triggers updates for new commits missed by webhooks.

#### Graph intelligence
- **Symbol-level dependency graph** — the dependency graph is now two-tier: file nodes for module-level relationships and symbol nodes (functions, classes, methods) for fine-grained call resolution. Call edges carry confidence scores (0.0–1.0).
- **3-tier call resolution** — Tier 1: same-file targets (confidence 0.95). Tier 2: import-scoped targets via named bindings (0.85–0.93). Tier 3: global unique match (0.50). Extracted by tree-sitter for Python, TypeScript, JavaScript, Go, Rust, Java, and C++.
- **Named binding resolution** — tracks import aliases, barrel re-exports (`__init__.py`, `index.ts`), and namespace imports across all 7 full-tier languages.
- **Heritage extraction** — class inheritance and interface implementation for 11 languages (Python, TypeScript, JavaScript, Java, Go, Rust, C++, Kotlin, Ruby, C#, C) with `extends`/`implements` graph edges.
- **Leiden community detection** — two-level community detection (file communities from import edges, symbol communities from call/heritage edges) with cohesion scoring and heuristic labeling. Falls back to NetworkX Louvain when graspologic is unavailable.
- **Execution flow tracing** — 5-signal entry point scoring (fan-out ratio, in-degree, visibility, name pattern, framework hint) with BFS call-path discovery and cross-community classification.
- **Graph query indexes** (migration `0017`) — composite indexes for sub-millisecond graph queries.

#### Web UI
- **Graph Intelligence on Overview** — expandable community list (labels, cohesion, member counts) and execution flows panel with call-path traces on the overview dashboard.
- **Wiki sidebar** — new collapsible section showing PageRank and betweenness percentile bars, community label, and in/out degree for the current file.
- **Symbols drawer** — right panel with graph metrics, callers/callees (with confidence scores), and heritage (extends/implements) for class nodes.
- **Graph page** — community color mode uses real community labels from Leiden detection. Clicking a node opens a community detail panel. Active color mode preserved as a URL parameter.
- **Contributor network, hotspot, and ownership pages** — new dedicated pages for git intelligence.
- **Docs viewer** — enriched with graph intelligence sidebar, version history, and improved markdown rendering.
- **5 new graph REST API endpoints** — communities list, community detail, node metrics, callers/callees, and execution flows.

#### Other
- **Improved init UX** — pre-scan phase shows repo size and language breakdown before confirming. Advanced config options grouped logically with live insights during indexing.
- **Doc generation enriched with graph intelligence** — wiki page generation prompts now include community context, caller/callee information, and heritage relationships.

### Changed
- **`get_overview`** now includes `community_summary` — top communities by size with labels and cohesion scores.
- **`get_context`** now includes `community` block per file target with community ID and label (when `compact=False`). In workspace mode, enriched with cross-repo co-change and contract data.
- **`get_risk`** enriched with cross-repo signals in workspace mode — co-change partners from other repos and contract dependencies.
- **`search_codebase`** in workspace mode searches across all repos and merges results.
- **Job executor** — improved progress tracking, concurrent run detection (HTTP 409), and crash recovery for stale running jobs on server startup.

---

## [0.2.3] — 2026-04-11

### Added
- **`annotate_file` MCP tool** — attach human-authored notes to any wiki page. Notes survive LLM-driven re-generation and appear in `get_context` responses and the web UI. Pass an empty string to clear notes.
- **`repowise export --full`** — full JSON export now includes decision records, dead code findings, git hotspots, and per-page provenance metadata (confidence, freshness, model, provider).
- **Rust import resolution** — `use crate::`, `super::`, and `self::` imports now resolve to local files via crate root detection (`lib.rs`/`main.rs`). External crates mapped to `external:` nodes.
- **Go import resolution** — `go.mod` module path parsing enables accurate local vs external package classification. Local imports resolve by suffix matching against the module path.
- **C/C++ parser improvements** — added captures for `template_declaration`, `type_definition` (typedef struct/enum), `preproc_def` (#define), `preproc_function_def`, and forward declarations.
- **Go parser** — added `const_spec` and `var_spec` captures for package-level constants and variables.
- **Rust parser** — added `macro_definition` capture for `macro_rules!` macros.
- **Dynamic import detection** — dead code analysis now scans for `importlib.import_module()` and `__import__()` calls; files in the same package receive reduced confidence (capped at 0.4).
- **Framework decorator awareness** — Flask, FastAPI, and Django route/endpoint decorators added to `_FRAMEWORK_DECORATORS`. Decorated functions are never flagged as dead code.
- **`human_notes` column on wiki pages** — persists across re-indexing. Alembic migration `0014_page_human_notes`.
- **Decision staleness scoring during ingestion** — `compute_staleness()` now runs during `repowise init`, not just `repowise update`.

### Changed
- **CLAUDE.md template** — replaced imperative "MUST use" / "CRITICAL" language with advisory framing. Added `indexed_commit` display. Made `update_decision_records` optional ("SHOULD for architectural changes").
- **`get_context` freshness** — freshness data now included by default instead of requiring explicit `include=["freshness"]`.
- **`get_answer` docstring** — removed "do NOT verify by Read" instruction. High-confidence note changed to "verify cited file paths exist before acting on them".
- **Token budget caps** — `get_overview` caps knowledge_silos (30), module_pages (20), entry_points (15). `get_why` caps file_commits (10).
- **Dead code patterns** — expanded `_DEFAULT_DYNAMIC_PATTERNS` with `*Mixin`, `*Command`, `*_view`, `*_endpoint`, `*_route`, `*_callback`, `*_signal`, `*_task`.

### Docs
- **README** — tool count updated to 11, `annotate_file` added to MCP tools table, `--full` export flag documented, dynamic import detection noted, comparison table updated.
- **Supported languages** — tiered table with accurate "What works" descriptions per language.
- Updated USER_GUIDE.md, ARCHITECTURE.md, and deep-dives.md to reflect all changes.

---

## [0.2.2] — 2026-04-11

### Added
- **tsconfig/jsconfig path alias resolution** (#40) — new `TsconfigResolver` discovers all `tsconfig.json` / `jsconfig.json` files, resolves `extends` chains (with circular detection), and maps path aliases (e.g. `@/*` -> `src/*`) to real files during graph construction. Non-relative TS/JS imports that match a path alias now create proper internal edges instead of phantom `external:` nodes. Fixes broken dependency graph, PageRank, dead code false positives, and change propagation for any TS/JS project using path aliases (Next.js, Vite, Angular, Nuxt, CRA).
- **Traversal stats** (#57) — `FileTraverser` now tracks skip reasons (`.gitignore`, blocked extension, binary, oversized, generated, `--exclude`, `.repowiseIgnore`, unknown language) via a new `TraversalStats` dataclass. Stats are surfaced after traversal as a filtering summary showing how many files were included vs excluded and why.
- **Submodule handling** (#57) — git submodule directories (parsed from `.gitmodules`) are now excluded by default during traversal. Added `--include-submodules` flag to `repowise init` to opt in.
- **Language breakdown** (#57) — generation plan table now shows language distribution (e.g. "Languages: python 79%, typescript 14%"). Completion panel shows top languages with percentages instead of just a count.
- **Multi-line exclude input** — interactive advanced mode now prompts for exclude patterns one per line instead of comma-separated on a single line.
- 38 new unit tests covering tsconfig resolver, traversal stats, and submodule handling.

### Changed
- Traverse progress bar uses spinner mode instead of showing misleading pre-filter totals (e.g. "2132/83601").
- Traverse phase label changed from "Traversing files..." to "Scanning & filtering files...".

### Fixed
- Server tests now use real temp directories with `.git` folders for path validation (#69 compatibility).

### Docs
- Updated README CLI reference with `--index-only`, `-x`, and `--include-submodules` examples.
- Updated website docs (`cli-reference.md`, `configuration.md`, `getting-started.md`) with submodule handling, `.gitignore` documentation, and new output examples.
- Reorganized `docs/` directory: architecture docs into `docs/architecture/`, internals into `docs/internals/`.
- Removed stale one-time documents (PHASE_5_5_IMPLEMENTATION, GIT_INTELLIGENCE_AUDIT, MCP_AND_STATE_REVIEW, MCP_TOOLS_TEST_REPORT).

---

## [0.2.1] — 2026-04-10

### Added
- **`get_answer` MCP tool** (`tool_answer.py`) — single-call RAG over the wiki layer. Runs retrieval, gates synthesis on top-hit dominance ratio, and returns a 2–5 sentence answer with concrete file/symbol citations plus a `confidence` label. High-confidence responses can be cited directly without verification reads. Backed by an `AnswerCache` table so repeated questions on the same repository cost nothing on the second call.
- **`get_symbol` MCP tool** (`tool_symbol.py`) — resolves a fully-qualified symbol id (`path::Class::method`, also accepts `Class.method`) to its source body, signature, file location, line range, and docstring. Returns the rich source-line signature (with base classes, decorators, and full type annotations preserved) instead of the stripped DB form.
- **`Page.summary` column** — short LLM-extracted summary (1–3 sentences) attached to every wiki page during generation. Used by `get_context` to keep context payloads bounded on dense files. Added by alembic migration `0012_page_summary`.
- **`AnswerCache` table** — memoised `get_answer` responses keyed by `(repository_id, question_hash)` plus the provider/model used. Added by alembic migration `0013_answer_cache`. Cache entries are repository-scoped and invalidated by re-indexing.
- **Test files in the wiki** — `page_generator._is_significant_file()` now treats any file tagged `is_test=True` (with at least one extracted symbol) as significant, regardless of PageRank. Test files have near-zero centrality because nothing imports them back, but they answer "what test exercises X" / "where is Y verified" questions; the doc layer is the right place to surface those. Filtering remains available via `--skip-tests`.
- **Overview dashboard** (`/repos/[id]/overview`) — new landing page for each repository with:
  - Health score ring (composite of doc coverage, freshness, dead code, hotspot density, silo risk)
  - Attention panel highlighting items needing action (stale docs, high-risk hotspots, dead code)
  - Language donut chart, ownership treemap, hotspots mini-list
  - Decisions timeline, module minimap (interactive graph summary)
  - Quick actions panel (sync, full re-index, generate CLAUDE.md, export)
  - Active job banner with live progress polling
- **Background pipeline execution** — `POST /api/repos/{id}/sync` and `POST /api/repos/{id}/full-resync` now launch the full pipeline in the background instead of only creating a pending job. Concurrent runs on the same repo return HTTP 409.
- **Shared persistence layer** (`core/pipeline/persist.py`) — `persist_pipeline_result()` extracted from CLI, reused by both CLI and server job executor
- **Job executor** (`server/job_executor.py`) — background task that runs `run_pipeline()`, writes progress to the `GenerationJob` table, and persists all results
- **Server crash recovery** — stale `running` jobs are reset to `failed` on server startup
- **Async pipeline improvements** — `asyncio.wrap_future` for file I/O, `asyncio.to_thread` for graph building and thread pool shutdown, periodic `asyncio.sleep(0)` yields during parsing
- **Health score utility** (`web/src/lib/utils/health-score.ts`) — composite health score computation, attention item builder, and language aggregation for the overview dashboard

### Changed
- **`get_context` default is now `compact=True`** — drops the `structure` block, the `imported_by` list, and per-symbol docstring/end-line fields to keep the response under ~10K characters. Pass `compact=False` for the full payload (e.g. when you specifically need import-graph dependents on a large file).
- `init_cmd.py` refactored to use shared `persist_pipeline_result()` instead of inline persistence logic
- Pipeline orchestrator uses async-friendly patterns to keep the event loop responsive during ingestion
- Sidebar and mobile nav updated to include "Overview" link

- Monorepo scaffold: uv workspace with `packages/core`, `packages/cli`, `packages/server`, `packages/web`
- Provider abstraction layer: `BaseProvider`, `GeneratedResponse`, `ProviderError`, `RateLimitError`
- `AnthropicProvider` with prompt caching support
- `OpenAIProvider` with OpenAI Chat Completions API
- `OllamaProvider` for local offline inference (OpenAI-compatible endpoint)
- `LiteLLMProvider` for 100+ models via LiteLLM proxy
- `MockProvider` for testing without API keys
- `RateLimiter`: async sliding-window RPM + TPM limits with exponential backoff
- `ProviderRegistry`: dynamic provider loading with custom provider registration
- CI pipeline: GitHub Actions matrix on Python 3.11, 3.12, 3.13
- Pre-commit hooks: ruff lint + format, mypy, standard file checks
- **Folder exclusion** — three-layer system for skipping paths during ingestion:
  - `FileTraverser(extra_exclude_patterns=[...])` — pass gitignore-style patterns at construction time; applied to both directory pruning and file-level filtering
  - Per-directory `.repowiseIgnore` — traverser loads one from each visited directory (like git's per-directory `.gitignore`); patterns are relative to that directory and cached for efficiency
  - `repowise init --exclude/-x PATTERN` — repeatable CLI flag; patterns are merged with `exclude_patterns` from `config.yaml` and persisted back to `.repowise/config.yaml`
  - `repowise update` reads `exclude_patterns` from `config.yaml` automatically
  - Web UI **Excluded Paths** section on `/repos/[id]/settings`: chip editor, Enter-to-add input, six quick-add suggestions (`vendor/`, `dist/`, `build/`, `node_modules/`, `*.generated.*`, `**/fixtures/**`), empty-state message, gitignore-syntax tooltip; saved via `PATCH /api/repos/{id}` as `settings.exclude_patterns`
  - `helpers.save_config()` now round-trips `config.yaml` to preserve all existing keys when updating provider/model/embedder; accepts optional `exclude_patterns` keyword argument
  - `scheduler.py` logs `repo.settings.exclude_patterns` in polling fallback as preparation for future full-sync wiring
- 13 new unit tests in `tests/unit/ingestion/test_traverser.py` covering `extra_exclude_patterns` and per-directory `.repowiseIgnore` behaviour

---

## [0.2.0] — 2026-04-07

A large overhaul: faster indexing, smarter doc generation, transactional storage,
new analysis capabilities, and a completely revamped web UI that surfaces every
new signal — all without changing the eight MCP tool surface.

### Added

#### Pipeline & ingestion
- **Parallel indexing.** AST parsing now runs across all CPU cores via
  `ProcessPoolExecutor`. Graph construction and git history indexing run
  concurrently with `asyncio.gather`. Per-file git history fetched through a
  thread executor with a semaphore.
- **RAG-aware doc generation.** Pages are generated in topological order; each
  generation prompt now includes summaries of the file's direct dependencies,
  pulled from the vector store of already-generated pages.
- **Atomic three-store coordinator.** New `AtomicStorageCoordinator` buffers
  writes across SQL, the in-memory dependency graph, and the vector store, then
  flushes them as a single transaction. Failure in any store rolls back all three.
- **Dynamic import hint extractors.** The dependency graph now captures edges
  that pure AST parsing misses: Django `INSTALLED_APPS` / `ROOT_URLCONF` /
  `MIDDLEWARE`, pytest `conftest.py` fixture wiring, and Node/TS path aliases
  from `tsconfig.json` and `package.json` `exports`.

#### Analysis
- **Temporal hotspot decay.** New `temporal_hotspot_score` column on
  `git_metadata`, computed as `Σ exp(-ln2 · age_days / 180) · min(lines/100, 3)`
  per commit. Hotspot ranking now uses this score; commits from a year ago
  contribute ~25% as much as commits from today.
- **Percentile ranks via SQL window function.** `recompute_git_percentiles()`
  is now a single `PERCENT_RANK() OVER (PARTITION BY repo ORDER BY ...)` UPDATE
  instead of an in-Python sort. Faster and correct on large repos.
- **PR blast radius analyzer.** New `PRBlastRadiusAnalyzer` returns direct
  risks, transitive affected files, co-change warnings, recommended reviewers,
  test gaps, and an overall 0-10 risk score. Surfaced via `get_risk(changed_files=...)`
  and a new web page.
- **Security pattern scanner.** Indexing now runs `SecurityScanner` over each
  file. Findings (eval/exec, weak crypto, raw SQL string construction,
  hardcoded secrets, `pickle.loads`, etc.) are stored in a new
  `security_findings` table.
- **Knowledge map.** Top owners, "bus factor 1" knowledge silos (>80% single
  owner), and high-centrality "onboarding targets" with thin documentation --
  surfaced in `get_overview` and the web overview page.

#### LLM cost tracking
- New `llm_costs` table records every LLM call (model, tokens, USD cost).
- `CostTracker` aggregates session totals; pricing covers Claude 4.6 family,
  GPT-4.1 family, and Gemini.
- New `repowise costs` CLI: `--since`, `--by operation|model|day`.
- Indexing progress bar shows a live `Cost: $X.XXXX` counter.

#### MCP tool enhancements (still 8 tools -- strictly more capable)
- `get_risk(targets, changed_files=None)` -- when `changed_files` is provided,
  returns the full PR blast-radius report (transitive affected, co-change
  warnings, recommended reviewers, test gaps, overall 0-10 score). Per-file
  responses now include `test_gap: bool` and `security_signals: list`.
- `get_overview()` -- now includes a `knowledge_map` block (top owners, silos,
  onboarding targets).
- `get_dead_code(min_confidence?, include_internals?, include_zombie_packages?)` --
  sensitivity controls for false positives in framework-heavy code.

#### REST endpoints (new)
- `GET /api/repos/{id}/costs` and `/costs/summary` -- grouped LLM spend.
- `GET /api/repos/{id}/security` -- security findings, filterable by file/severity.
- `POST /api/repos/{id}/blast-radius` -- PR impact analysis.
- `GET /api/repos/{id}/knowledge-map` -- owners / silos / onboarding targets.
- `GET /api/repos/{id}/health/coordinator` -- three-store drift status.
- `GET /api/repos/{id}/hotspots` now returns `temporal_hotspot_score` and is
  ordered by it.
- `GET /api/repos/{id}/git-metadata` now returns `test_gap`.
- Job SSE stream now emits `actual_cost_usd` (running cost since job start).

#### Web UI (new pages and components)
- **Costs page** -- daily bar chart, grouped tables by operation/model/day.
- **Blast Radius page** -- paste files (or click hotspot suggestion chips) to
  see risk gauge, transitive impact, co-change warnings, reviewers, test gaps.
- **Knowledge Map card** on the overview dashboard.
- **Trend column** on the hotspots table with flame indicator (default sort).
- **Security Panel** in the wiki page right sidebar.
- **"No tests" badge** on wiki pages with no detected test file.
- **System Health card** on the settings page (SQL / Vector / Graph counts +
  drift % + status).
- **Live cost indicator** on the generation progress bar.

#### CLI
- `repowise costs [--since DATE] [--by operation|model|day]` -- new command.
- `repowise dead-code` -- new flags `--min-confidence`, `--include-internals`,
  `--include-zombie-packages`, `--no-unreachable`, `--no-unused-exports`.
- `repowise doctor` -- new Check #10 reports coordinator drift across all
  three stores. `--repair` deletes orphaned vectors and rebuilds missing graph
  nodes from SQL.

### Fixed
- C++ dependency resolution edge cases.
- Decision extraction timeout on very large histories.
- Resume / progress bar visibility for oversized files.
- Coordinator `health_check` falsely reporting 100% drift on LanceDB / Pg
  vector stores (was returning -1 for the count). Now uses `list_page_ids()`.
- Coordinator `health_check` returning `null` graph node count when no
  in-memory `GraphBuilder` is supplied. Now falls back to SQL `COUNT(*)`.

### Internal
- Three new Alembic migrations: `0009_llm_costs`, `0010_temporal_hotspot_score`,
  `0011_security_findings`.

### Compatibility
- Existing repositories must run migrations: `repowise doctor` will detect
  the missing tables and prompt; alternatively re-run `repowise init` to
  rebuild from scratch.
- The eight MCP tool names and signatures are backwards compatible -- new
  parameters are all optional.

---

## [0.1.31] — earlier

See git history for releases prior to 0.2.0.

---

[0.3.1]: https://github.com/repowise-dev/repowise/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/repowise-dev/repowise/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/repowise-dev/repowise/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/repowise-dev/repowise/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/repowise-dev/repowise/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/repowise-dev/repowise/compare/v0.1.31...v0.2.0
