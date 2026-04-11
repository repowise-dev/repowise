# repowise Architecture

repowise is an open-source, self-hostable codebase documentation engine. It generates
a structured, hierarchical wiki for any codebase, keeps it accurate as code changes,
and exposes everything through an MCP server so AI coding assistants can query it
in real time.

This document covers how the system is built, why each piece exists, and how
they fit together. Read this before contributing.

### Package READMEs

For per-package detail (installation, full API reference, all CLI flags, file maps):

| Package | README | What it covers |
|---------|--------|----------------|
| `packages/core` | [`packages/core/README.md`](../packages/core/README.md) | Ingestion, generation, persistence, providers — all key classes with code examples |
| `packages/cli` | [`packages/cli/README.md`](../packages/cli/README.md) | All 10 CLI commands with every flag documented |
| `packages/server` | [`packages/server/README.md`](../packages/server/README.md) | All REST API endpoints, 11 MCP tools, webhook setup, scheduler jobs |
| `packages/web` | [`packages/web/README.md`](../packages/web/README.md) | Every frontend file with purpose — API client, hooks, components, pages |

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Structure](#2-repository-structure)
3. [The Three Stores](#3-the-three-stores)
4. [Provider Abstraction Layer](#4-provider-abstraction-layer)
5. [Init Path — First-Time Documentation](#5-init-path--first-time-documentation)
6. [Maintenance Path — Keeping Docs in Sync](#6-maintenance-path--keeping-docs-in-sync)
7. [Git Intelligence](#7-git-intelligence)
8. [Dead Code Detection](#8-dead-code-detection)
9. [Decision Intelligence](#9-decision-intelligence)
10. [MCP Server](#10-mcp-server)
11. [REST API and Web UI](#11-rest-api-and-web-ui)
12. [Codebase Chat](#12-codebase-chat)
13. [Data Flow Diagrams](#13-data-flow-diagrams)
14. [Key Design Decisions](#14-key-design-decisions)
15. [Editor File Generation](#15-editor-file-generation)
16. [Adding a New Language](#16-adding-a-new-language)
17. [Adding a New LLM Provider](#17-adding-a-new-llm-provider)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Sources                                      │
│         GitHub / GitLab repo          Local filesystem               │
└────────────────────┬────────────────────────┬───────────────────────┘
                     │ webhook / git diff       │ repowise init/update
                     ▼                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Core Engine                                     │
│                                                                      │
│   ┌─────────────────────┐      ┌──────────────────────────────┐     │
│   │  Ingestion Pipeline  │      │      Generation Engine        │     │
│   │                     │      │                              │     │
│   │  FileTraverser      │─────▶│  ContextAssembler            │     │
│   │  ASTParser          │      │  PageGenerator (hierarchical) │     │
│   │  GraphBuilder       │      │  JobSystem (resumable)        │     │
│   │  ChangeDetector     │      │  PromptTemplates (Jinja2)     │     │
│   │  GitIndexer         │      │                              │     │
│   │  DeadCodeAnalyzer   │      │                              │     │
│   └─────────────────────┘      └──────────────┬───────────────┘     │
│                                               │                     │
│                               ┌───────────────▼──────────────┐      │
│                               │      LLM Provider Layer       │      │
│                               │  Anthropic │ OpenAI │ Ollama  │      │
│                               │         │ LiteLLM │          │      │
│                               └───────────────────────────────┘      │
└──────────────┬─────────────────────────┬────────────────────────────┘
               │                         │
               ▼                         ▼
┌──────────────────────┐   ┌─────────────────────────────────────────┐
│      Three Stores     │   │              Consumers                  │
│                      │   │                                         │
│  SQL (wiki pages,    │   │  Web UI     MCP Server   GitHub Action  │
│  jobs, symbols,      │   │  (Next.js)  (10 tools)   (CI/CD)        │
│  versions)           │   │                                         │
│                      │   │  repowise CLI                           │
│  Vector (LanceDB /   │   │  (init, update, watch,                  │
│  pgvector, semantic  │   │   search, export, serve, mcp)           │
│  search, RAG ctx)    │   │                                         │
│                      │   └─────────────────────────────────────────┘
│  Graph (NetworkX,    │
│  dep graph,          │
│  PageRank, SCC)      │
└──────────────────────┘
```

repowise has two operational modes that share the same core engine but follow
different strategies:

- **Init** — first-time documentation of an existing codebase. May take minutes
  to hours on large repos. Resumable. Supports batch API for cheaper generation.
- **Maintenance** — incremental updates triggered by git commits. Runs in seconds
  to minutes. Uses change propagation through the dependency graph to only regenerate
  what actually changed.

---

## 2. Repository Structure

```
repowise/
├── packages/
│   ├── core/                   # Python: ingestion + generation engine
│   │   ├── src/repowise/core/
│   │   │   ├── ingestion/
│   │   │   │   ├── traverser.py        # file tree walking + gitignore + per-dir .repowiseIgnore + extra exclude patterns
│   │   │   │   ├── parser.py           # ASTParser — one class, all languages
│   │   │   │   ├── parsers/            # per-language parser helpers
│   │   │   │   ├── graph.py            # NetworkX dep graph builder
│   │   │   │   ├── change_detector.py  # git diff + change propagation
│   │   │   │   ├── git_indexer.py      # git history mining → git_metadata table
│   │   │   │   ├── special_handlers.py # OpenAPI, Protobuf, GraphQL, Dockerfile, CI YAML
│   │   │   │   └── models.py           # ParsedFile, FileInfo, Symbol, Import, etc.
│   │   │   ├── analysis/
│   │   │   │   └── dead_code.py        # dead code detection (graph + SQL, no LLM)
│   │   │   ├── generation/
│   │   │   │   ├── page_generator.py   # hierarchical page generator
│   │   │   │   ├── context_assembler.py# context assembly (RAG + graph + git + source)
│   │   │   │   ├── job_system.py       # resumable job state machine
│   │   │   │   ├── models.py           # GeneratedPage, GenerationConfig, PageType, etc.
│   │   │   │   ├── templates/          # Jinja2 prompt templates (one per page type)
│   │   │   │   └── editor_files/       # CLAUDE.md / cursor.md generation (no LLM)
│   │   │   │       ├── base.py         # BaseEditorFileGenerator (marker-merge, atomic write)
│   │   │   │       ├── data.py         # EditorFileData + sub-dataclasses (frozen)
│   │   │   │       ├── fetcher.py      # EditorFileDataFetcher (DB → EditorFileData)
│   │   │   │       ├── tech_stack.py   # filesystem tech stack + build command detection
│   │   │   │       └── claude_md.py    # ClaudeMdGenerator subclass
│   │   │   ├── pipeline/
│   │   │   │   ├── orchestrator.py     # run_pipeline(), PipelineResult
│   │   │   │   ├── persist.py          # persist_pipeline_result() — shared by CLI + server
│   │   │   │   └── progress.py         # ProgressCallback protocol + LoggingProgressCallback
│   │   │   ├── persistence/
│   │   │   │   ├── models.py           # SQLAlchemy ORM models
│   │   │   │   ├── crud.py             # async CRUD layer
│   │   │   │   ├── database.py         # engine factory, session management
│   │   │   │   ├── search.py           # SQLite FTS5 full-text search
│   │   │   │   ├── vector_store.py     # VectorStore abstraction (LanceDB / pgvector)
│   │   │   │   └── embedder.py         # Embedder base class + MockEmbedder
│   │   │   ├── providers/              # LLM provider abstraction
│   │   │   │   ├── base.py             # BaseProvider, GeneratedResponse, ProviderError
│   │   │   │   ├── anthropic.py
│   │   │   │   ├── openai.py
│   │   │   │   ├── ollama.py
│   │   │   │   ├── litellm.py
│   │   │   │   ├── mock.py
│   │   │   │   └── registry.py         # get_provider(), register_provider(), list_providers()
│   │   │   └── rate_limiter.py         # token-bucket RPM + TPM limiter
│   │   └── queries/                    # tree-sitter .scm query files (one per language)
│   │       ├── python.scm
│   │       ├── typescript.scm
│   │       ├── javascript.scm
│   │       ├── go.scm
│   │       ├── rust.scm
│   │       ├── java.scm
│   │       ├── cpp.scm
│   │       ├── c.scm
│   │       ├── ruby.scm
│   │       └── kotlin.scm
│   │
│   ├── server/                 # Python: FastAPI REST API + MCP server
│   │   └── src/repowise/server/
│   │       ├── routers/         # FastAPI routers (repos, pages, jobs, symbols, graph, git, dead-code, decisions, search, claude-md)
│   │       ├── mcp_server/      # MCP server package (10 tools, split into focused modules)
│   │       ├── webhooks/        # GitHub + GitLab handlers
│   │       ├── job_executor.py  # Background pipeline executor — bridges REST endpoints to core pipeline
│   │       └── scheduler.py     # APScheduler background jobs
│   │
│   ├── cli/                    # Python: repowise CLI (click + rich)
│   │   └── src/repowise/cli/
│   │       └── commands/        # init, update, watch, serve, search, export, status, doctor, dead-code, decision, mcp, reindex, generate-claude-md
│   │
│   └── web/                    # Next.js 15 frontend
│       ├── src/app/             # App Router pages (dashboard, wiki, search, graph, symbols, overview, …)
│       ├── src/components/      # UI primitives, layout, wiki, repos, jobs, settings, dashboard
│       └── src/lib/             # API client, SWR hooks, utilities (health-score, format), design tokens
│
├── integrations/
│   ├── github-action/           # action.yml + Dockerfile entrypoint
│   └── github-app/              # GitHub App webhook handler
│
├── docker/
│   ├── Dockerfile               # multi-stage: Python + Node build + final runtime
│   └── docker-compose.yml       # with optional Redis profile
│
├── tests/
│   ├── unit/                    # unit tests (no LLM calls, no filesystem)
│   ├── integration/             # integration tests using sample_repo fixture
│   ├── e2e/                     # full repowise init + update flows
│   └── fixtures/
│       └── sample_repo/         # 30-file multi-language fixture repo
│
└── docs/                        # repowise's own generated wiki (dogfooding)
```

---

## 3. The Three Stores

repowise uses three separate storage systems. They are not redundant — each answers
a fundamentally different kind of question that the other two cannot answer efficiently.

### 3.1 SQL Store (SQLAlchemy + SQLite / PostgreSQL)

**Answers: what exists, what changed, when.**

The source of truth for all structured data. SQLite in development and single-server
deployments; PostgreSQL for multi-worker production deployments. The schema is identical
for both — SQLAlchemy abstracts the difference.

Key tables:

| Table | Purpose |
|-------|---------|
| `repos` | Registered repositories, sync state, provider config |
| `wiki_pages` | All generated wiki pages with content, metadata, confidence score, and a short LLM-extracted `summary` (1–3 sentences) used by `get_context` to keep responses bounded |
| `page_versions` | Full version history of every page (for diff view) |
| `symbols` | Symbol index: every function, class, method across all files |
| `answer_cache` | Memoised `get_answer` responses keyed by `(repository_id, question_hash)` plus the provider/model used. Repeated questions return at zero LLM cost; cache entries are invalidated by repository re-indexing. |
| `generation_jobs` | Job state machine with checkpoint fields for resumability |
| `webhook_events` | Every received webhook event (deduplication, audit, retry) |
| `symbol_rename_history` | Detected renames for auditing and targeted text patching |
| `graph_nodes` / `graph_edges` | SQLite-backed graph for repos exceeding 30K nodes |
| `git_metadata` | Per-file git history: commit counts, ownership, co-change partners, hotspot/stable flags |
| `dead_code_findings` | Dead code findings: unreachable files, unused exports, zombie packages |

If you delete the SQL store, you lose everything and must re-run `repowise init`.

### 3.2 Vector Store (LanceDB embedded / pgvector)

**Answers: what is semantically similar to this query.**

repowise uses a `VectorStore` abstraction with two backends, selected automatically
based on the configured SQL backend:

**LanceDB (default — SQLite mode)**
LanceDB runs embedded as a library — no separate server process. Data is stored in
`.repowise/lancedb/` using the Lance columnar format. This makes self-hosting trivial
and keeps the Docker setup simple. LanceDB is significantly faster than ChromaDB on
both write throughput (batch embedding) and ANN query latency, and it requires no
C++ build tools to install.

**pgvector (PostgreSQL mode)**
When repowise is configured with a PostgreSQL database, the `wiki_pages` table gains
an `embedding vector(N)` column via the `pgvector` PostgreSQL extension. Embeddings
are stored directly in the same SQL database — no second storage system required.
Vector similarity search uses `<=>` (cosine distance) with an HNSW index. This is
the preferred backend for multi-worker production deployments.

Every generated wiki page is embedded and stored immediately after generation.
The vector store is used in two distinct ways:

**During generation (RAG context):** When generating a wiki page for file A, the
`ContextAssembler` queries the vector store with A's exported symbol names to find
pages for files that A imports from. These are included as context in the generation
prompt. Each generated page is aware of what its dependencies actually *do* —
not just their names. See [Section 5.4](#54-context-assembly) for details.

**During search and MCP queries:** The `search_codebase` MCP tool and the web UI
search page use the vector store to find the most semantically relevant pages for a
natural language query. This is better than full-text search for questions like
"how does authentication work?" or "where is rate limiting handled?".

If you delete the vector store (LanceDB directory or pgvector embeddings), search
quality degrades and generation context becomes shallower — rebuild it by running
`repowise reindex` which re-embeds all existing SQL pages into LanceDB using
the configured embedder (Gemini or OpenAI). No LLM calls — only embedding API calls.

### 3.3 Graph Store (NetworkX / SQLite-backed)

**Answers: how are things connected, and how important are they.**

The dependency graph is a directed multigraph where nodes are files and symbols,
and edges are relationships (`imports`, `calls`, `inherits`, `implements`, etc.).

It is built by the `ASTParser` + `GraphBuilder` during ingestion and persisted to
`.repowise/graph.json` (for repos ≤ 30K nodes) or the `graph_nodes`/`graph_edges`
SQL tables (for larger repos, using the `networkit` library as a drop-in).

The graph is used for:

- **Generation ordering** — topological sort determines what to generate first
  (files with no dependents get generated before files that import them, so the
  richer context is available via RAG when the importing file is generated)
- **Change propagation** — when a file changes, walk the graph to find all
  pages that reference its symbols and mark them as stale
- **PageRank** — identifies the most central, important symbols; these get
  "spotlight" wiki pages and richer generation prompts
- **SCC detection** — circular dependency clusters require a special generation
  strategy (see [Section 5.3](#53-circular-dependencies))
- **Co-change edges** — temporal coupling from git history. Files that frequently
  change together (but may have no import relationship) get `co_changes` edges.
  These participate in change propagation and are shown in the graph visualization
  as dashed purple lines. They do NOT affect PageRank.
- **Dead code detection** — files with `in_degree == 0` (no importers) are
  candidates for unreachable file detection
- **MCP `get_dependency_path` tool** — answers "how is module A connected to module B?"
- **D3 graph visualization** in the web UI

If you delete the graph, repowise loses change propagation and generation ordering.
It can be rebuilt from scratch by re-parsing the source files.

---

## 4. Provider Abstraction Layer

Every LLM call in the entire system goes through `LLMProvider`. No provider SDK
is ever imported from business logic packages.

```
                    ┌─────────────────┐
                    │   LLMProvider   │  (abstract base class)
                    │                 │
                    │  generate()     │
                    │  generate_stream│
                    │  embed()        │
                    │  generate_batch │  (optional, default = sequential)
                    │  estimate_cost  │  (optional, returns None if unknown)
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┬──────────────────┐
          ▼                  ▼                  ▼                  ▼
  AnthropicProvider   OpenAIProvider     OllamaProvider    LiteLLMProvider
  (claude-*)          (gpt-*, any        (any local model,  (100+ providers,
  batch API + prompt   OpenAI-compat      fully offline,     optional dep)
  caching support)     endpoint)          no API key)
```

### 4.1 Rate Limiter

Each provider instance wraps a `RateLimiter` using a token-bucket algorithm with
two independent buckets: requests-per-minute (RPM) and tokens-per-minute (TPM).

Before every API call, the limiter acquires from both buckets. On a 429 response,
it calls `on_rate_limit_error()` which applies exponential backoff and temporarily
reduces the refill rate. This is transparent to all callers.

Default limits are configured per provider in `.repowise/config.yaml` and can be
adjusted for users with higher API tiers.

### 4.2 Batch Mode (Init Only)

For `repowise init`, the Anthropic provider supports the Message Batches API:
instead of firing concurrent streaming requests, all file-page generation requests
for a given level are submitted as a single batch, which is ~50% cheaper and
processes asynchronously (typically within 1 hour).

Batch mode is enabled by default for `repowise init` when using the Anthropic
provider. Pass `--no-batch` to use streaming instead (faster wall-clock time,
higher cost).

### 4.3 Prompt Caching

For Anthropic: the `ContextAssembler` marks the system prompt + shared repository
context with cache-control breakpoints. Across the hundreds of file-page generation
calls during a large init, this shared prefix is only billed once. Cost reduction
on large repos is typically 60–90%.

### 4.4 Adding a Provider

Implement `LLMProvider`, add an entry to `LANGUAGE_CONFIGS` in `providers/registry.py`,
and add a section to `.repowise/config.yaml`. See [Section 12](#12-adding-a-new-llm-provider).

---

## 5. Init Path — First-Time Documentation

`repowise init` runs when documenting a codebase for the first time. It is the
expensive, one-time operation that builds the full wiki from scratch.

### 5.1 File Traversal

`FileTraverser` walks the repository tree and produces a `FileInfo` for every
file that should be documented. It respects six layers of exclusion, applied in
priority order:

1. `.gitignore` (parsed with `pathspec`, not simple glob matching)
2. Root `.repowiseIgnore` (same syntax, user-defined at repo root)
3. **Per-directory `.repowiseIgnore`** — loaded from each directory visited during
   the `os.walk`. Patterns are relative to the directory containing the file (like
   git's per-directory `.gitignore`). A spec per directory is loaded once and cached
   for the traversal. Example: `generated/` in `src/.repowiseIgnore` skips
   `src/generated/` without affecting other directories with the same name.
4. **`extra_exclude_patterns`** (constructor param) — additional gitignore-style
   patterns passed at runtime from `--exclude/-x` CLI flags or
   `repo.settings["exclude_patterns"]` (set via Web UI or REST API PATCH). Applied
   to both directory pruning (entire subtree skipped) and individual file filtering.
5. Hardcoded blocklist (`node_modules`, `.git`, `__pycache__`, `dist`, `build`,
   `*.lock`, `*.min.js`, generated protobuf files, etc.)
6. Auto-detection of generated files (files with `// Code generated` headers)
7. Binary files (detected by null bytes in first 8KB)
8. Files over `max_file_size_kb` (default: 500KB)

**Constructor signature:**
```python
FileTraverser(
    repo_root: Path,
    *,
    max_file_size_kb: int = 500,
    extra_ignore_filename: str = ".repowiseIgnore",
    extra_exclude_patterns: list[str] | None = None,
)
```

**Where `extra_exclude_patterns` comes from:**

| Source | How patterns reach traverser |
|--------|------------------------------|
| `repowise init -x vendor/ -x 'src/gen/**'` | Merged with `config.yaml exclude_patterns`, passed directly |
| `repowise update` | Read from `.repowise/config.yaml exclude_patterns` |
| Web UI Excluded Paths section | Saved to `repo.settings["exclude_patterns"]` via REST API |
| Server sync job | Read from `repo.settings.get("exclude_patterns", [])` |

The traverser also detects monorepo structure by looking for multiple
`pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml` files at depth 1–2.
When detected, each package is documented as a semi-independent unit with
cross-package edges tracked in the graph.

Each `FileInfo` is tagged with: `language`, `is_test`, `is_config`, `is_api_contract`,
`is_entry_point`, `git_hash`. These tags influence generation priority and prompt choice.

**Test files are first-class wiki targets.** The page generator includes any file
tagged `is_test=True` that has at least one extracted symbol, even if the file's
PageRank is near zero (which is typical: nothing imports test files back, so
graph-centrality metrics never select them on their own). Test files answer
questions of the form *"what test exercises X"* / *"where is Y verified"*, and
the doc layer is the right place to surface those. Users who want to exclude
tests from the wiki entirely can pass `--skip-tests` to `repowise init`.

### 5.2 AST Parsing

`ASTParser` is a single class that handles all supported languages. There are no
per-language subclasses.

**How it works:**

1. Calls `get_parser(language)` from `tree-sitter-languages` to get the right grammar
2. Parses the source into a tree-sitter parse tree
3. Loads the corresponding `queries/<language>.scm` file (cached after first load)
4. Runs tree-sitter queries to extract symbols, imports, and exports
5. Maps results through `LANGUAGE_CONFIGS[language]` for language-specific rules
   (visibility keywords, entry point patterns, etc.)
6. Returns a `ParsedFile` with a consistent shape regardless of language

**The `.scm` query files** use standard tree-sitter S-expression syntax with
consistent capture name conventions across all languages:
- `@symbol.def` — the full symbol node
- `@symbol.name` — the name identifier
- `@symbol.params` — parameter list
- `@symbol.return_type` — return type annotation
- `@symbol.docstring` — existing docstring (expanded, not repeated, by the LLM)
- `@import.statement` — full import node
- `@import.module` — the module being imported

**Adding a new language** = write one `.scm` file + add one entry to `LANGUAGE_CONFIGS`.
No changes to `ASTParser` itself. See [Section 11](#11-adding-a-new-language).

**Special handlers** live in `core/special_handlers/` and are separate from `ASTParser`.
They handle file types that are not programming languages (OpenAPI specs, Dockerfiles,
GitHub Actions YAML, Makefiles, Protobuf, GraphQL schemas). They produce the same
`ParsedFile` shape but use purpose-built parsers (PyYAML, `graphql-core`, etc.)
instead of tree-sitter.

### 5.3 Dependency Graph Construction

`GraphBuilder` takes all `ParsedFile` outputs and builds a `networkx.DiGraph`.

**Node types:**
- `file` — every source file
- `symbol` — every function, class, interface, etc.
- `package` — every package/module directory
- `external` — third-party packages (lightweight node, not fully documented)

**Edge types:**
- `imports` — file A imports from file B
- `calls` — symbol A calls symbol B
- `inherits` — class A extends class B
- `implements` — class A implements interface B
- `instantiates` — code in A creates an instance of B
- `references` — looser reference (type annotation, generic, etc.)
- `re-exports` — A re-exports symbols from B (barrel files)
- `inter_package` — edge crossing package boundary (monorepos)
- `co_changes` — A and B frequently change in the same commit (from git history,
  added by `GitIndexer` after graph construction). Weight = co-change count.
  Filtered out of PageRank but included in change propagation and visualization.

After graph construction, the builder computes:

- **PageRank** — nodes with high PageRank are central and well-connected.
  Used to decide generation priority and which symbols get spotlight pages.
- **Strongly Connected Components (SCCs)** — groups of files with circular imports.
  Logged as warnings. Require special generation handling (see below).
- **Betweenness centrality** — identifies "bridge" symbols whose removal would
  disconnect the graph. These are the most critical to document well.
- **Community detection (Louvain)** — discovers logical modules even when the
  directory structure doesn't reflect them. These communities become module pages.

**Circular dependency handling:**

Files in an SCC cannot be generated in strict bottom-up order because each one
depends on the others. Strategy:
1. Generate each SCC member with reduced context (own source + signatures of
   cycle partners only, no full docs)
2. Generate a dedicated `SCC` wiki page explaining the cycle and how to navigate it
3. In a second pass, upgrade each member's page with cross-references to the others

**Graph scalability:**

For repos with fewer than 30K nodes, the graph lives in memory as a NetworkX
`DiGraph` and is serialized to `.repowise/graph.json`.

For repos exceeding 30K nodes (configurable via `graph_backend: sqlite`), repowise
switches to `networkit` with SQLite backing via the `graph_nodes` and `graph_edges`
tables. Only the subgraph needed for each operation is loaded into memory. The
API is identical — this is transparent to all callers.

### 5.4 Context Assembly

This is the most important quality driver in the system. The `ContextAssembler`
builds the prompt context for each generation call — it does not just dump the
raw source file.

For a given file page, it assembles context in this priority order (dropping
lower-priority items if the token budget is exceeded):

1. **Source code** — full source (or chunked if file is large)
2. **Symbol signatures** — all symbols extracted from this file with their
   signatures and existing docstrings
3. **Graph context** — PageRank score, cluster membership, which module this
   belongs to, entry point status
4. **Git context** — ownership, significant commit messages explaining *why* code
   evolved, hotspot/stable classification, co-change partners. This transforms
   documentation from "here is what this code does" into "here is what it does
   and why it was written this way." Git context is the last thing dropped when
   over budget — it is more valuable than import summaries.
5. **Import summaries** — for each file this file imports from: the summary of
   that file's already-generated wiki page (if available), or just its public
   API signatures (if not yet generated)
6. **RAG context** — vector store similarity search (LanceDB or pgvector) using this file's top exported
   symbols as the query. Returns the top 3 most relevant already-generated pages.
   This propagates understanding upward: `AuthService`'s page will know what
   `UserRepository` actually does, not just that it imports from it.
7. **Co-change context** — wiki pages for co-change partners (files that change
   together without an import relationship). Reveals hidden coupling.
8. **Dead code findings** — symbols in this file flagged as unused (if any).
   Listed in the generation prompt so the LLM notes them as cleanup candidates.
9. **Reverse import context** — which files import *this* file, and what they
   use from it. Helps the LLM understand how this file is used in practice.

Token budget: the total assembled context targets 12K tokens, leaving room for
the generation output. Items are dropped in reverse priority order when over budget.
The source code is never dropped — it is chunked instead if too large.

**Large file chunking:**

Files over `large_file_threshold_kb` (default: 100KB) are handled differently.
Rather than passing the full source, repowise:
1. Extracts public symbol signatures only for the top-level context
2. Generates one sub-page per major class or function group
3. Synthesizes a file page from the sub-pages
Pages generated from chunks are tagged `chunked: true` in metadata.

### 5.5 Hierarchical Generation Order

Generation must follow a strict dependency-aware order. This is not optional —
generating a module page before its file pages means the module page has no
content to draw from.

```
Level 0: External API contracts (OpenAPI, proto, GraphQL)
         → self-contained, no dependencies on other pages

Level 1: Symbol spotlight pages (top 10% by PageRank)
         → short, fast, parallelizable
         → high-PageRank symbols get richer documentation

Level 2: File pages
         → uses: source + symbol docs + import summaries (from RAG)
         → parallelizable within this level
         → SCC members generate with stubs, upgraded in second pass

Level 3: SCC pages
         → must wait for all member files to complete Level 2

Level 4: Module/package pages
         → uses: all file pages within the module

Level 5: Cross-package relationship pages (monorepos only)
         → uses: all package pages

Level 6: Repository overview + architecture diagram
         → uses: all module pages + graph metrics

Level 7: Config/infra pages (Dockerfile, CI YAML, Makefile, etc.)
         → references code pages, so comes after all code docs

Level 8: Index pages (symbol index, search index)
         → built from all completed pages
```

Within each level, up to `concurrent_jobs` tasks run in parallel using
`asyncio.Semaphore`. The generation engine never exceeds this limit regardless
of how large the repo is.

Each generated page gets a `confidence_score` of `1.0` when first created.

### 5.6 Resumable Jobs

Init on a 50K-file repo can take 30–90 minutes. A crash or network interruption
should not require starting over.

The `JobSystem` persists checkpoint state after every completed page:
- `checkpoint_level` — which generation level is currently active
- `checkpoint_file_index` — position within the current level
- `completed_page_ids` — list of already-generated page IDs
- `failed_page_ids` — pages that failed (retried on resume)

On `repowise init --resume`, the job is loaded from the database, completed pages
are skipped, and generation continues from the last checkpoint.

`repowise init` is fully idempotent. Running it twice produces the same result.
Running it after a partial previous run completes only the remaining pages.

### 5.7 Shared Persistence (`pipeline/persist.py`)

The persistence logic for storing a `PipelineResult` into the database (graph nodes,
edges, symbols, pages, git metadata, dead code findings, decision records) was
extracted from the CLI's `init_cmd.py` into `core/pipeline/persist.py`. Both the
CLI and the server's background job executor call `persist_pipeline_result()` — zero
duplication.

FTS indexing is intentionally excluded from this function. Callers must run it
separately after the session closes to avoid SQLite write-lock conflicts.

### 5.8 Background Job Executor (`server/job_executor.py`)

The server can now run full pipeline jobs in the background, triggered by the
`POST /api/repos/{id}/sync` and `POST /api/repos/{id}/full-resync` endpoints.

`execute_job()` is the single entry point, launched via `asyncio.create_task()`:

1. Marks the job as `running`
2. Resolves the LLM provider from server config
3. Runs `run_pipeline()` with a `JobProgressCallback` that writes progress to the
   `GenerationJob` table (the SSE stream endpoint polls this table)
4. Persists results via `persist_pipeline_result()`
5. Marks the job as `completed` (or `failed` on error)

Progress updates are batched (every 5 items) to avoid per-item DB overhead. Before
writing the final job status, all in-flight progress tasks are drained to prevent a
late `running` update from overwriting `completed`.

Concurrent pipeline runs on the same repository are prevented at the endpoint level
(returns HTTP 409 if a pending/running job already exists).

### 5.9 Async Pipeline Improvements

The pipeline orchestrator now keeps the event loop responsive during CPU-bound work:
- File I/O uses `asyncio.wrap_future()` instead of blocking `as_completed()`
- Graph building runs in a thread via `asyncio.to_thread()`
- The parse loop yields control every 50 files with `asyncio.sleep(0)`
- Thread pool shutdown is non-blocking via `asyncio.to_thread()`

---

## 6. Maintenance Path — Keeping Docs in Sync

`repowise update` runs after a git push (triggered by webhook, GitHub Action, or
polling fallback). It is fast, targeted, and avoids regenerating pages that
haven't actually changed in a meaningful way.

### 6.1 Change Detection

`ChangeDetector` uses GitPython to compute the diff between `last_sync_commit`
and `HEAD`:

```python
changed_files = differ.get_changed_files(repo_path, since_commit="a1b2c3d")
# → ChangedFiles(added=[...], modified=[...], deleted=[...], renamed=[...])
```

Renamed files get special treatment: repowise updates all `source_files` references
in existing wiki pages and regenerates the file page (since the path context has
changed), but does *not* regenerate pages that only reference the file's symbols
(symbol names don't include paths).

### 6.2 Symbol Rename Detection

`ChangeDetector.detect_symbol_renames()` uses a heuristic to identify when a
symbol was renamed rather than deleted-and-recreated:
- Same `kind` (function → function)
- Similar signature (Levenshtein distance under threshold)
- Git blame on the new file confirms line provenance

When a rename is detected, repowise applies a targeted text patch to all pages
that only *mention* the old name (cheaper than full regeneration), and fully
regenerates pages that *document* the old symbol.

All detected renames are stored in `symbol_rename_history` for audit purposes.

### 6.3 Affected Page Computation

For each changed file, `ChangeDetector.get_affected_pages()` walks the dependency
graph to find all pages that need updating:

```
Changed file → find its symbols
            → find all wiki pages that document those symbols (direct)
            → find all wiki pages that reference those symbols (1-hop: inherits/calls)
            → find all wiki pages that reference referencing pages (2-hop: looser reference)
            → apply cascade budget
```

**Cascade budget:**

A change to a central utility module (e.g., `utils.py` imported by 200 files) would
naively require regenerating 200+ pages on every push. This is too expensive.

The `cascade_budget` config option (default: 30 pages per maintenance run) caps the
number of pages fully regenerated per push. Pages beyond the budget:
- Have their `confidence_score` decayed (see below)
- Are added to the background staleness queue
- Are regenerated by the nightly background job

### 6.4 Confidence Score System

Every wiki page has a `confidence_score` (float 0.0–1.0) representing how fresh
it is relative to the source code:

| Score | Status | Badge | Action |
|-------|--------|-------|--------|
| ≥ 0.80 | fresh | green | none |
| 0.60–0.79 | stale | yellow | queued for background regen |
| 0.30–0.59 | outdated | red | auto-queued, warning shown |
| < 0.30 | unusable | red (prominent) | force-regenerated immediately |

Decay rules applied on each maintenance run:
- Source file directly changed: `score *= 0.85`
- Referenced symbol changed (1-hop: calls/inherits): `score *= 0.95`
- Referenced symbol changed (2-hop: looser reference): `score *= 0.98`
- Co-change partner changed (no import relationship): `score *= 0.97`
- Page beyond cascade budget (time-based, 1 week since change): `score *= 0.90`

**Git-informed decay modifiers** (applied multiplicatively on top of base decay):
- Hotspot file (`is_hotspot=True`) → decays faster: direct `*= 0.94`, 1-hop `*= 0.95`
- Stable file (`is_stable=True`) → decays slower: direct `*= 1.03`
- Large change in commit message ("rewrite", "refactor", "migrate") → hard decay:
  direct `*= 0.71`, 1-hop `*= 0.84`
- Cosmetic change in commit message ("typo", "lint", "format") → soft decay:
  direct `*= 1.12`

Pages start at `1.0` and decay over time. Regeneration resets to `1.0`.

### 6.5 Webhook Reliability

Webhooks can be missed when the repowise server is down during a push. repowise
uses a two-layer sync strategy:

**Layer 1 (real-time):** GitHub/GitLab webhook → `POST /api/webhooks/github` →
signature verification → store in `webhook_events` → enqueue `GenerationJob`.
Response is always `200 OK` immediately — processing is async.

**Layer 2 (polling fallback):** APScheduler job runs every 15 minutes (configurable).
Compares `repos.last_sync_commit` against actual `HEAD` via GitHub API or GitPython.
If they differ, triggers an incremental update for the missing commits.

The combination ensures: even if a webhook is missed entirely, docs are at most
`polling_interval` minutes stale for the default branch.

### 6.6 Background Staleness Resolution

APScheduler runs a nightly job (configurable via cron expression) to:
1. Query all pages where `confidence_score < staleness_regen_threshold` (default: 0.60)
2. Sort by `confidence_score ASC` (most stale first)
3. Regenerate up to `background_regen_budget` pages (default: 100)
4. Log token usage and cost

This ensures no page stays stale indefinitely regardless of cascade budget constraints.

### 6.7 PR Documentation Preview

On `pull_request` webhook events, repowise runs `ChangeDetector` in dry-run mode
and posts a GitHub PR comment listing:
- Pages that will be regenerated (with links to current versions)
- Pages that will have confidence decay
- New pages that will be created (new files)
- Pages that will be deleted (deleted files)
- Estimated token cost for this PR's docs update

On merge, the actual incremental update runs.

---

## 7. Git Intelligence

repowise mines git history to make documentation significantly richer and more useful.
The `GitIndexer` runs once during `repowise init` (after graph construction, before
generation) and incrementally during `repowise update`. All git features degrade
gracefully when git metadata is unavailable — they simply skip git-enriched context.

### 7.1 GitIndexer (`packages/core/ingestion/git_indexer.py`)

The `GitIndexer` class mines git history into the `git_metadata` SQL table. For each
tracked file, it computes:

- **Commit volume** — total, last 90 days, last 30 days (churn signals)
- **Timeline** — first and last commit dates (file age)
- **Ownership** — primary owner from `git blame` (who wrote the most lines),
  top 3 contributors by commit count
- **Significant commits** — last 10 meaningful commit messages (filtered: no merges,
  no dependency bumps, no chore/ci, messages > 20 chars). These explain *why*
  the code evolved this way and are included in generation prompts.
- **Co-change partners** — files that changed in the same commit >= 3 times, even
  without an import relationship. Reveals hidden structural coupling.
- **Derived signals** — `is_hotspot` (top 25% churn AND complexity), `is_stable`
  (>10 commits, 0 in 90 days), `churn_percentile` (0.0–1.0)

**Performance targets:**
- 3,000 files, 10K commits → < 3 minutes
- 50,000 files, 100K commits → < 20 minutes (uses shallow history for large repos)
- Uses `asyncio.Semaphore(20)` to parallelize git log calls across files

### 7.2 How Git Intelligence Enhances Each Component

**Generation prompts:** The `file_page.j2` template includes optional git context
blocks (gated on `{% if git_metadata %}`). These add ownership attribution, evolution
context from significant commits, hotspot/stable warnings, and co-change partner
documentation. The LLM uses commit messages to explain *why* code is structured as
it is, not just *what* it does.

**Generation ordering:** Within each level, files are sorted by priority: entry
points first, then hotspots, then high PageRank, then high commit count. This
ensures the most important files generate first, making them available as RAG
context for less important files.

**Generation depth:** Files can be auto-upgraded to "thorough" depth if they are
hotspots, have > 100 commits with > 10 in 90 days, have >= 8 significant commits,
or have co-change partners. Conversely, stable + low PageRank + low commit count
files can be auto-downgraded to "minimal". Controlled by `git.depth_auto_upgrade`.

**Maintenance prompts:** When regenerating during `repowise update`, the specific
commit that triggered the update (SHA, author, message, diff) is included in the
prompt. This produces targeted updates rather than full rewrites.

**Confidence decay:** Git signals modify the base confidence decay multiplicatively.
Hotspot files decay faster (bugs are more likely). Stable files decay slower. Large
changes ("rewrite", "refactor", "migrate" in commit message) trigger aggressive
decay. Cosmetic changes ("typo", "lint", "format") trigger softer decay.

**Co-change edges:** After `GitIndexer` runs, co-change edges (`edge_type="co_changes"`)
are added to the dependency graph. They participate in change propagation (when file
A changes, its co-change partners get mild confidence decay at factor 0.97) and are
visible in the graph visualization as dashed purple lines.

**CLAUDE.md generation:** `repowise generate-claude-md` includes sections for
hotspots, stable core files, ownership map, and hidden coupling pairs.

### 7.3 Module and Repo-Level Git Summaries

Module pages include a team ownership summary: who maintains the most files, who
was most active recently. Repo overview pages include codebase health signals:
hotspot count, stable file count, top churn files, oldest file.

---

## 8. Dead Code Detection

repowise detects unreachable files, unused exports, and zombie packages using
graph traversal and SQL queries. No LLM calls — the analysis completes in < 10
seconds for any repo size.

### 8.1 DeadCodeAnalyzer (`packages/core/analysis/dead_code.py`)

The analyzer runs after `GitIndexer` during init (Step 3.6) and optionally during
`repowise update`. It produces findings with confidence scores:

**Finding types:**
- `unreachable_file` — file with `in_degree == 0`, not an entry point, test, or config
- `unused_export` — public symbol with no incoming edges (but file IS imported)
- `unused_internal` — private symbol with no `calls` edges from same file
- `zombie_package` — monorepo package with no incoming `inter_package` edges

**Confidence scoring (conservative — when in doubt, do NOT flag):**
- Unreachable + no commits in 90d + last commit > 6 months → 1.0
- Unreachable + no commits in 90d → 0.7
- Unreachable but recently touched → 0.4
- `safe_to_delete` only set at confidence >= 0.7, and NOT for files matching
  dynamic patterns (`*Plugin*`, `*Handler*`, `*Adapter*`, `*Middleware*`)

**Never flagged as dead:**
- `__init__.py` public re-exports
- `@pytest.fixture`, `@pytest.mark.*` symbols
- Files matching `*migrations*`, `*schema*`, `*seed*`
- TypeScript `.d.ts` files
- Files where `is_api_contract == True`
- Files in `.repowise/dead_code_whitelist.txt`
- Symbols matching `config.dead_code.dynamic_patterns`

### 8.2 Dead Code in Generation Prompts

The `file_page.j2` template includes an optional block for dead code findings.
When present, the LLM notes unused symbols as cleanup candidates in the
documentation, including confidence percentages and safety assessment.

### 8.3 CLI: `repowise dead-code`

```
repowise dead-code [PATH]
  --min-confidence FLOAT    (default: 0.4)
  --safe-only               only show safe_to_delete=True findings
  --kind TYPE               filter by kind
  --package PKG             filter by monorepo package
  --format table|json|md
  --output FILE

repowise dead-code resolve [FINDING_ID]
  --status acknowledged|resolved|false_positive
  --note "reason"
```

### 8.4 Integration with Other Components

Dead code findings are stored in the `dead_code_findings` SQL table with a status
field (`open` / `acknowledged` / `resolved` / `false_positive`). The web UI shows
a dedicated dead code page with sortable tables, confidence sliders, and resolve
buttons. The graph visualization supports a "show dead code" filter that adds red
dashed borders to dead code nodes.

---

## 9. Decision Intelligence

The Decision Intelligence layer captures **architectural decisions** — the *why* behind how
the system is built, what alternatives were rejected, and what constraints exist. While documentation
describes *what*, decisions capture *why*.

### Capture Sources

Decisions are extracted from four sources, each with a different confidence level:

| Source | Confidence | Status | Trigger |
|--------|-----------|--------|---------|
| **Inline markers** (`# WHY:`, `# DECISION:`, `# TRADEOFF:`, `# ADR:`, `# RATIONALE:`, `# REJECTED:`) | 0.95 | `active` | File scanning during init/update |
| **Git archaeology** (commit messages with migration/refactor signals) | 0.70–0.85 | `proposed` | Init only, reuses git layer data |
| **README/docs mining** (implicit decisions in prose) | 0.60 | `proposed` | Init only, LLM extraction |
| **CLI capture** (`repowise decision add`) | 1.00 | `active` | Manual entry |

### Data Model

Stored in the `decision_records` SQL table. JSON arrays for alternatives, consequences,
affected files, modules, tags, and evidence commits. Deduplication key:
`(repository_id, title, source, evidence_file)`.

### Staleness Tracking

Decisions have a `staleness_score` (0.0 = fresh, 1.0 = very stale) computed during
`repowise init` and recomputed on every `repowise update`. Staleness rises when affected
files receive commits after the decision was recorded. Decisions with `staleness_score > 0.5`
are flagged as stale.

### MCP Tools

- `get_why(query?)` — three modes: natural language search over decisions, path-based lookup for decisions governing a file, or no-arg for decision health dashboard (stale decisions, ungoverned hotspots, proposed decisions needing review)
- `get_context(targets, include?)` — includes decisions governing each target in its response

### CLI Commands

```
repowise decision add        # interactive capture
repowise decision list       # tabular list with filters
repowise decision show <id>  # full detail
repowise decision confirm    # proposed → active
repowise decision dismiss    # delete proposed
repowise decision deprecate  # active → deprecated
repowise decision health     # health summary
```

### Key Files

| File | Purpose |
|------|---------|
| `core/analysis/decision_extractor.py` | All 4 capture sources + staleness computation |
| `core/persistence/models.py` | `DecisionRecord` ORM model |
| `core/persistence/crud.py` | 8 decision CRUD functions |
| `server/mcp_server/tool_why.py` | MCP tool `get_why` (3-mode: search, path, health dashboard) |
| `server/routers/decisions.py` | REST API endpoints |
| `cli/commands/decision_cmd.py` | CLI command group (7 subcommands) |

---

## 10. MCP Server

The MCP server is repowise's most valuable integration feature. It exposes the
entire wiki as a set of queryable tools that any MCP-compatible AI assistant
can call in real time.

Instead of an AI assistant reading 40 source files to understand a codebase,
it calls `get_overview()` and gets a structured, always-current architecture summary.
Instead of calling 5 tools one at a time, it calls `get_context(["src/auth/service.py", "AuthService"])` and gets docs, ownership, decisions, and freshness for all targets in one call.

The server is implemented using the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
and supports two transports:
- **stdio** — for Claude Code, Cursor, Cline (add to their MCP config)
- **SSE** — for web-based MCP clients (served on port 7338)

### Tools (11 total)

| Tool | What it answers | When to call |
|------|----------------|-------------|
| `get_overview` | Architecture summary, module map, entry points. | First call when exploring an unfamiliar codebase. |
| `get_context(targets, include?)` | Docs, ownership, history, decisions, freshness for files/modules/symbols. Pass multiple targets in one call. | When you need to understand specific code before reading or modifying it. |
| `get_risk(targets)` | Hotspot score, dependents, co-change partners, risk summary per target. Also returns top 5 global hotspots. | Before modifying files — assess what could break. |
| `get_why(query?)` | Three modes: NL search over decisions, path-based decisions for a file, no-arg health dashboard. | Before making architectural changes — understand existing intent. |
| `update_decision_records(action, ...)` | Full CRUD on decision records: create, update, update_status, delete, list, get. | After every coding task — record new decisions and keep existing ones current. |
| `search_codebase(query)` | Semantic search over the full wiki. Natural language. | When you don't know where something lives. |
| `get_dependency_path(from, to)` | Connection path between two files/modules in the dependency graph. | When you need to understand how two things are connected. |
| `get_dead_code` | Dead/unused code findings sorted by confidence and cleanup impact. | Before cleanup tasks. |
| `get_architecture_diagram` | Mermaid diagram for repo or specific module. | For documentation or presentation. |
| `get_answer` | One-call RAG: confidence-gated synthesis with cited answers and question cache. | First call on any code question — collapses search → read → reason. |
| `get_symbol` | Resolve a qualified symbol id to source body, signature, and docstring. | When the question names a specific class, function, or method. |
| `annotate_file` | Attach human-authored notes to a wiki page — survives re-indexing. | Adding rationale, known issues, or context the LLM shouldn't overwrite. |

### Auto-generated Config

`repowise init` automatically generates `.repowise/mcp.json` with ready-to-paste
config blocks for Claude Code (`~/.claude/claude.json`), Cursor (`.cursor/mcp.json`),
and Cline. This config is printed at the end of `repowise init`.

---

## 11. REST API and Web UI

### REST API (FastAPI)

Served on port `7337` alongside the web UI. All endpoints are prefixed with `/api/`.

Key routers:
- `/api/repos` — register repos, trigger sync, full-resync (now launches background pipeline jobs with concurrent-run prevention)
- `/api/pages` — read pages, version history, force-regenerate single page
- `/api/search` — semantic (LanceDB or pgvector) and full-text (SQLite FTS5 / PostgreSQL tsvector) search
- `/api/jobs` — job status, SSE stream for live progress updates
- `/api/symbols` — symbol lookup, dependency path queries
- `/api/graph` — graph export in D3-compatible JSON format
- `/api/webhooks/github` — GitHub webhook handler with HMAC verification
- `/api/webhooks/gitlab` — GitLab webhook handler
- `/api/repos/{id}/git-metadata` — per-file git metadata
- `/api/repos/{id}/hotspots` — high-churn + high-complexity files
- `/api/repos/{id}/ownership` — ownership breakdown (file/module/package granularity)
- `/api/repos/{id}/co-changes` — co-change partners for a file
- `/api/repos/{id}/git-summary` — aggregate git health signals for dashboard
- `/api/repos/{id}/dead-code` — dead code findings (GET list, POST trigger analysis)
- `/api/repos/{id}/dead-code/summary` — aggregate dead code stats
- `/api/dead-code/{finding_id}` — PATCH to resolve/acknowledge findings
- `/api/repos/{id}/claude-md` — GET preview of generated CLAUDE.md section (JSON, no disk write)
- `/api/repos/{id}/claude-md/generate` — POST to regenerate and write CLAUDE.md to disk
- `/health` — liveness + readiness (checks DB + provider)
- `/metrics` — Prometheus-compatible metrics (job counts, token totals, stale count)

**Server lifecycle:**
- On startup, any jobs left in `running` state from a previous server instance are
  automatically reset to `failed` (crash recovery).
- Background pipeline tasks are tracked in `app.state.background_tasks` to prevent
  garbage collection of `asyncio.Task` references.

Authentication is optional. Set `REPOWISE_API_KEY` to require bearer token auth on
all non-`/health` endpoints. Default (no key set): fully open, suitable for local use.

### Web UI (Next.js 15)

Served from the same port as the API. All routes under `/`:

| Route | Content |
|-------|---------|
| `/` | Dashboard: all repos, recent jobs, stale page counts, token usage |
| `/repos/[id]` | Repo layout with file tree sidebar |
| `/repos/[id]/overview` | **Overview dashboard** — health score ring, attention panel, language donut, ownership treemap, hotspots mini, decisions timeline, module minimap, quick actions, active job banner |
| `/repos/[id]/wiki/[...slug]` | Individual wiki page with MDX rendering |
| `/repos/[id]/search` | Semantic search results |
| `/repos/[id]/graph` | D3 force-directed dependency graph |
| `/repos/[id]/symbols` | Full symbol index, sortable by PageRank |
| `/repos/[id]/coverage` | Documentation coverage metrics |
| `/repos/[id]/ownership` | Ownership treemap — files colored by primary owner, sized by LOC |
| `/repos/[id]/hotspots` | Hotspot list — top 20 files with churn + complexity bars |
| `/repos/[id]/dead-code` | Dead code report — three tabs: Files, Exports, Internals |
| `/repos/[id]/decisions` | Architectural decision records |
| `/repos/[id]/chat` | Codebase chat with streaming LLM responses |
| `/settings` | Provider config, polling interval, cascade budget |

**Key rendering behavior:**

Every backtick identifier in a wiki page that matches a known symbol becomes a
hover card showing the symbol's signature, file, and confidence score, plus a
link to the symbol's wiki page. This is built by post-processing the MDX content
client-side after initial render.

Mermaid diagrams are rendered lazily (only when scrolled into viewport) using
`mermaid.js` initialized with the repowise design theme.

Code blocks use Shiki for syntax highlighting. Each block has a "View in source"
button that deep-links to the relevant line in GitHub/GitLab.

The `<GenerationProgress>` component connects to `/api/jobs/{id}/stream` via SSE
and shows a live progress display during generation (pages done/total, current
file, tokens used, estimated cost, estimated time remaining).

---

## 12. Codebase Chat

repowise includes an interactive chat interface that lets users ask questions about
their codebase and receive answers grounded in the wiki, dependency graph, git
history, and architectural decisions. The chat agent uses whichever LLM provider
the user has configured and has access to all 11 MCP tools.

See [`docs/CHAT.md`](CHAT.md) for the full technical reference covering the
backend agentic loop, SSE streaming protocol, provider abstraction extensions,
database schema, frontend component architecture, and artifact rendering system.

**Key design points:**

- **Provider-agnostic** — the chat agent goes through the same provider abstraction
  as documentation generation. A `ChatProvider` protocol extends `BaseProvider` with
  `stream_chat()` for streaming + tool use without breaking existing callers.
- **Tool reuse** — the 11 MCP tools are called directly as Python functions (no
  subprocess round-trip). Tool schemas are defined once in `chat_tools.py` and
  fed to both the LLM and the executor.
- **SSE streaming** — `POST /api/repos/{repo_id}/chat/messages` runs the agentic
  loop and streams back Server-Sent Events (`text_delta`, `tool_start`,
  `tool_result`, `done`, `error`).
- **Conversation persistence** — chat history is stored in `conversations` and
  `chat_messages` tables, allowing replay and continuation across page refreshes.
- **Artifact panel** — tool results with rich content (wiki pages, Mermaid diagrams,
  search results, risk reports) open in a slide-in artifact panel that reuses
  existing frontend components.

---

## 13. Data Flow Diagrams

### Init flow

```
repowise init /path/to/repo
       │
       ▼
FileTraverser
  walks repo, produces list[FileInfo]
       │
       ▼
ASTParser  ──── queries/<lang>.scm ────▶ ParsedFile per file
  (parallel,                             (symbols, imports, exports)
   one parser per worker)
       │
       ▼
GraphBuilder
  constructs DiGraph
  computes PageRank, SCC, Betweenness, Communities
  persists to graph.json (or DB for large repos)
       │
       ▼
GitIndexer (if git.enabled)
  mines git history → git_metadata table
  computes co-change partners → adds co_changes edges to graph
  classifies hotspots (top 25% churn + complexity)
  classifies stable files (>10 commits, 0 in 90 days)
       │
       ▼
DeadCodeAnalyzer (if dead_code.enabled)
  graph traversal + SQL: unreachable files, unused exports, zombie packages
  pure analysis — no LLM calls, < 10 seconds
       │
       ▼
JobSystem
  creates GenerationJob in DB
  shows cost estimate + confirmation
       │
       ├── Level 0: special handlers (OpenAPI, proto, GraphQL, Dockerfile...)
       │
       ├── Level 1: symbol spotlight pages (top 10% PageRank)
       │              ┌────────────────────────────────────┐
       │              │ ContextAssembler                   │
       │              │  source + graph metadata           │
       │              │  + RAG (vector store similarity search)│
       │              │  + import summaries                │
       │              └──────────────┬─────────────────────┘
       │                             │
       │                             ▼
       │                       LLMProvider.generate()
       │                             │
       │                             ▼
       │                      WikiPage stored:
       │                        → SQL (content, metadata, confidence=1.0)
       │                        → VectorStore (LanceDB or pgvector — embedding for RAG)
       │                        → Graph (node.page_id linked)
       │
       ├── Level 2: file pages (parallel, RAG pool growing with each completed page)
       ├── Level 3: SCC pages
       ├── Level 4: module pages
       ├── Level 5: cross-package pages (monorepos)
       ├── Level 6: repo overview + architecture diagram
       ├── Level 7: config/infra pages
       └── Level 8: index pages
              │
              ▼
       .repowise/state.json updated
       MCP config printed
       CLAUDE.md generated (EditorFileDataFetcher → ClaudeMdGenerator, no LLM)
       Summary shown (pages, tokens, cost, time)
```

### Maintenance flow (on git push)

```
git push
    │
    ├── GitHub webhook → POST /api/webhooks/github
    │        │ (verified, stored in webhook_events)
    │        ▼
    │   GenerationJob created (type: incremental)
    │
    └── (fallback) APScheduler polls every 15min
             │ (if HEAD != last_sync_commit)
             ▼
        GenerationJob created (type: incremental)

GenerationJob runs:
    │
    ▼
ChangeDetector.get_changed_files(since=last_sync_commit)
    │
    ├── Renamed files → update source_files refs in SQL
    ▼
GitIndexer.index_changed_files(changed_file_paths)
    │ re-indexes only changed files + their co-change partners
    │ updates co-change edges in graph
    ▼
ChangeDetector.detect_symbol_renames()
    │
    ▼
ChangeDetector.get_affected_pages(cascade_budget=30)
    │ includes co-change partners in staleness propagation
    │
    ├── regenerate: list[page_id]  → full regeneration (up to cascade_budget)
    ├── rename_patch: list[page_id] → targeted text find-and-replace
    └── decay_only: list[page_id]  → confidence score decay, add to staleness queue
    │
    ▼
For each page in `regenerate`:
    ContextAssembler → LLMProvider → update SQL + VectorStore + Graph
    │
    ▼
For each page in `rename_patch`:
    scan content_md for old symbol name → replace → update SQL
    │
    ▼
For each page in `decay_only`:
    confidence_score *= decay_factor
    regen_queued = True
    │
    ▼
repos.last_sync_commit = HEAD
state.json updated
```

### Server-triggered pipeline flow

```
Web UI "Sync" / "Full Re-index" button
    │
    ▼
POST /api/repos/{id}/sync (or /full-resync)
    │
    ├── Check: no pending/running job for this repo (else → 409)
    ▼
Create GenerationJob (status=pending, commit)
    │
    ▼
asyncio.create_task(execute_job(job_id, app_state))
    │  (strong ref in app.state.background_tasks)
    ▼
execute_job():
    ├── Mark job "running"
    ├── Resolve LLM provider from server config
    ├── run_pipeline() with JobProgressCallback
    │       └── writes progress to GenerationJob table every 5 items
    ├── persist_pipeline_result() (shared with CLI)
    ├── FTS index new pages
    ├── drain_and_stop() progress tasks
    └── Mark job "completed" (or "failed" on error)
```

### MCP query flow

```
Claude Code: "how does the auth module work?"
    │
    ▼
MCP client calls repowise tool: search_codebase(query="auth module")
    │
    ▼
VectorStore similarity search (LanceDB or pgvector) → top 5 page IDs
    │
    ▼
SQL: fetch page content for those IDs
    │
    ▼
Return: list[{title, content_md snippet, relevance_score, confidence_score}]
    │
    ▼
Claude Code has structured, current documentation
instead of reading 40 files
```

---

## 14. Key Design Decisions

### Three-layer folder exclusion, not one monolithic list

repowise offers three complementary ways to skip paths during traversal, each solving
a different problem:

1. **Root `.repowiseIgnore`** — project-wide exclusions committed to the repo (like
   `.gitignore`). Shared across all users, version-controlled.

2. **Per-directory `.repowiseIgnore`** — exclusions that only apply within a subtree.
   Lets a monorepo package owner exclude its own generated output without polluting
   the root ignore file. Patterns are relative to the directory, matching git semantics.
   Specs are loaded once per directory during `os.walk` and cached by absolute path;
   the root spec is pre-seeded in the cache to avoid reading it twice.

3. **`extra_exclude_patterns` (runtime)** — patterns injected without touching any
   file on disk: from `--exclude/-x` CLI flags (dev workflow), from
   `config.yaml exclude_patterns` (persisted per repo), or from
   `repo.settings["exclude_patterns"]` (Web UI / REST API). This lets teams configure
   exclusions through the UI without git access to the target repo.

All three layers use `pathspec` with `gitwildmatch` semantics — the same library used
for `.gitignore` parsing — so the full gitignore syntax works everywhere.

### `save_config()` round-trips YAML, not overwrites

The original `save_config()` wrote a fixed three-key file (`provider`, `model`,
`embedder`). This meant any other keys — such as `exclude_patterns` set via the Web UI
— would be silently dropped the next time `repowise init` ran. The updated function
loads the existing config, merges in the new values, then writes the result back.
This ensures all config sources (CLI, Web UI, REST API, manual edits) coexist safely.

### One `ASTParser` class, not one per language

Per-language differences live in `.scm` query files and `LANGUAGE_CONFIGS` dict entries.
This means adding a new language requires no changes to Python business logic — just
a new `.scm` file and a config entry. The `ASTParser` class itself never has
`if lang == "python"` branches.

### LanceDB (embedded) + pgvector, not ChromaDB

ChromaDB was the original choice but has notable drawbacks: slow write throughput on
large repos, heavy C++ build dependencies (`chroma-hnswlib`), and a bloated dependency
tree. LanceDB replaces it as the default embedded vector store:

- **No build step** — pure Python wheel, no C++ compiler required on Windows
- **Faster writes** — Lance columnar format is optimised for batch appends; embedding
  50K pages during `repowise init` is measurably faster
- **Faster queries** — IVF-PQ and HNSW index support with sub-millisecond ANN search
- **Simpler data model** — tables are Arrow-native; filtering by `repo_id` or `page_type`
  uses SQL-style predicates alongside the vector search, no separate metadata store needed
- **Zero server process** — same embedded story as ChromaDB: data lives in
  `.repowise/lancedb/`, no container needed

When PostgreSQL is already in use (multi-worker prod), **pgvector** is preferred because
it eliminates the second storage system entirely: embeddings live as a `vector` column
in the existing `wiki_pages` table, queries are plain SQL with `<=>` cosine distance,
and backup/restore is a single `pg_dump`. The HNSW index (`CREATE INDEX ... USING hnsw`)
gives query latency on par with LanceDB at typical repowise dataset sizes.

The `VectorStore` abstraction in `packages/core/src/repowise/core/persistence/vector.py`
selects the backend at startup based on `DATABASE_URL`: SQLite → LanceDB, PostgreSQL → pgvector.

### NetworkX + SQLite fallback, not Neo4j

NetworkX is a Python library that runs in-process. Neo4j is a separate server
(Java process, GBs of RAM, authentication setup). For a graph that is derived
from source code (rebuildable at any time), the operational overhead of Neo4j
is unjustified. NetworkX handles tens of thousands of nodes comfortably.

The SQLite-backed fallback using `networkit` handles repos that genuinely exceed
in-memory limits without adding a server dependency.

### Confidence scores, not binary "fresh/stale"

A binary fresh/stale flag would require either: (a) marking everything stale on
every push (triggering full regen, expensive), or (b) only marking directly changed
pages (missing transitive effects). The confidence score model captures the gradient:
directly changed pages decay sharply, indirectly referenced pages decay gently.
Users can see at a glance how much to trust each page.

### Jinja2 prompts, not hardcoded strings

All LLM prompts are Jinja2 templates in `packages/core/queries/prompts/`. Users can
override any prompt by placing a file with the same name in `.repowise/prompts/`.
This lets power users tune generation quality without forking the project.

### Cascade budget

Without a budget, changing a central utility (imported by 200 files) would
regenerate 200+ pages on every push. With a budget (default: 30), repowise
regenerates the highest-PageRank affected pages immediately and defers the rest
to the nightly background job. No page stays stale indefinitely; the nightly job
catches everything the cascade budget missed.

### Git metadata in generation prompts, not just for display

The highest-value use of git metadata is enriching the LLM's generation context —
not just showing ownership in a sidebar. By including significant commit messages
in the prompt, the LLM can explain *why* code is structured a certain way (e.g.,
"this was refactored in March 2024 to separate auth concerns from the request
pipeline"). This context is unavailable from static analysis alone.

### Co-change edges as a separate graph layer

Co-change relationships are temporal coupling — they cannot be detected by AST
parsing. repowise adds them as `co_changes` edges after `GitIndexer` runs, but
deliberately filters them out of PageRank computation (they would skew it
artificially toward files that are often edited together for process reasons, not
architectural ones). They participate in change propagation and visualization only.

### Conservative dead code detection

repowise's dead code detector errs heavily toward false negatives. `safe_to_delete`
is set to `True` only at confidence >= 0.7 and after excluding dynamically-loaded
patterns. Dead code analysis is pure graph traversal + SQL (no LLM calls), so it
completes in seconds and can be re-run cheaply. repowise surfaces candidates —
humans decide before deleting anything.

### Async-first throughout

All database operations use async SQLAlchemy with `aiosqlite`. The event loop
is never blocked. This matters during generation: the LLM call, the DB write,
and the vector store embed (LanceDB or pgvector) can overlap with the next file's context assembly.

---

## 15. Editor File Generation

See [`docs/EDITOR_FILES.md`](EDITOR_FILES.md) for the complete reference covering
architecture, all data sources, how the marker-merge system works, and how to add
support for a new editor file (cursor.md, copilot-instructions.md, etc.).

**Quick summary:** repowise can generate and maintain AI-editor configuration files
(CLAUDE.md, cursor.md, etc.) from the already-indexed codebase data — no LLM calls.
The system uses HTML comment markers to split the file into a user-owned section and
a Repowise-managed section. The user section is never touched.

The feature runs automatically after `repowise init` and `repowise update`, and can
also be run standalone:

```
repowise generate-claude-md [PATH]
```

Config opt-out:
```yaml
# .repowise/config.yaml
editor_files:
  claude_md: false
```

Key files:

| File | Purpose |
|------|---------|
| `core/generation/editor_files/base.py` | `BaseEditorFileGenerator` — marker-merge logic shared by all editor-file generators |
| `core/generation/editor_files/data.py` | `EditorFileData` frozen dataclass — the data contract between fetcher and template |
| `core/generation/editor_files/fetcher.py` | `EditorFileDataFetcher` — queries DB for architecture summary, modules, hotspots, decisions |
| `core/generation/editor_files/tech_stack.py` | Filesystem scan for languages, frameworks, build commands |
| `core/generation/editor_files/claude_md.py` | `ClaudeMdGenerator` — 30-line subclass that binds filename + template |
| `core/generation/templates/claude_md.j2` | Jinja2 template for the Repowise-managed section |
| `cli/commands/claude_md_cmd.py` | `repowise generate-claude-md` CLI command |
| `server/routers/claude_md.py` | `GET/POST /api/repos/{id}/claude-md` REST endpoints |

---

## 16. Adding a New Language

1. **Write `packages/core/queries/<language>.scm`**

   Use tree-sitter S-expression syntax. Follow the capture name conventions:
   `@symbol.def`, `@symbol.name`, `@symbol.params`, `@symbol.return_type`,
   `@symbol.docstring`, `@import.statement`, `@import.module`, `@import.names`.

   Check the tree-sitter playground for your language's node type names:
   `https://tree-sitter.github.io/tree-sitter/playground`

2. **Add a `LanguageConfig` entry to `LANGUAGE_CONFIGS` in `parser.py`**

   ```python
   "mylang": LanguageConfig(
       symbol_node_types={
           "function_definition": "function",
           "class_definition": "class",
       },
       import_node_types=["import_statement"],
       export_node_types=[],
       visibility_fn=lambda name, mods: "private" if name.startswith("_") else "public",
       entry_point_patterns=["main.ml", "app.ml"],
   ),
   ```

3. **Verify `tree-sitter-languages` includes your language**

   Run `python -c "from tree_sitter_languages import get_parser; get_parser('mylang')"`.
   If it raises, the grammar is not bundled. You can add it manually as a tree-sitter
   grammar and register it with the Language API.

4. **Add test files to `tests/fixtures/sample_repo/`**

   At minimum: one file with a function, one with a class, one with imports.

5. **Run `pytest tests/unit/test_parser.py -k mylang`** to verify extraction.

6. **Open a PR.** That's it — no other changes needed.

---

## 17. Adding a New LLM Provider

1. **Create `packages/core/providers/<name>.py`**

   Subclass `LLMProvider` and implement:
   - `generate(request: GenerationRequest) -> GenerationResponse`
   - `generate_stream(request: GenerationRequest) -> AsyncIterator[str]`
   - `embed(request: EmbedRequest) -> EmbedResponse`
   - `name` property

   Optionally override:
   - `supports_batch` → `True` if the provider has a batch API
   - `generate_batch(requests) -> list[GenerationResponse]`
   - `estimate_cost(input_tokens, output_tokens) -> float`

2. **Register in `providers/registry.py`**

   ```python
   case "myprovider": return MyProvider(config)
   ```

3. **Add config section to `.repowise/config.yaml` docs**

   ```yaml
   myprovider:
     api_key: ${MYPROVIDER_API_KEY}
     base_url: https://api.myprovider.com/v1
   ```

4. **Add default rate limits** to `PROVIDER_DEFAULT_LIMITS` in `rate_limiter.py`

5. **Add a `MockProvider` fixture** for tests if the provider has unique response formats

6. **Update `CONTRIBUTING.md`** with the new provider's environment variables

---

## Appendix: Configuration Reference

Full configuration with defaults (`.repowise/config.yaml`):

```yaml
provider: anthropic          # anthropic | openai | ollama | litellm
model: claude-sonnet-4-5    # passed through to the provider
embedding_provider: anthropic
embedding_model: voyage-3

batch_mode: auto             # auto | always | never
prompt_caching: true

anthropic:
  api_key: ${ANTHROPIC_API_KEY}

openai:
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1

ollama:
  base_url: http://localhost:11434

generation:
  max_tokens: 4096
  temperature: 0.2
  concurrent_jobs: 5
  request_timeout: 120
  max_retries: 3
  large_file_threshold_kb: 100
  max_file_size_kb: 500

graph:
  backend: auto              # auto | networkx | sqlite
  node_threshold: 30000      # switch to sqlite above this

git:
  enabled: true              # mine git history for richer generation
  co_change_commit_limit: 500  # commits to analyze for co-changes (0 = unlimited)
  co_change_min_count: 3     # minimum co-occurrences to register a relationship
  blame_enabled: true        # git blame for ownership (slightly slower)
  prompt_commit_count: 10    # significant commits to include in generation prompts
  depth_auto_upgrade: true   # auto-upgrade depth for hotspots

dead_code:
  enabled: true              # detect unreachable files, unused exports, zombie packages
  detect_unreachable_files: true
  detect_unused_exports: true
  detect_unused_internals: false   # off by default — higher false positive rate
  detect_zombie_packages: true
  min_confidence: 0.4
  safe_to_delete_threshold: 0.7
  dynamic_patterns:          # symbols matching these are never flagged
    - "*Plugin"
    - "*Handler"
    - "*Adapter"
    - "*Middleware"
    - "register_*"
    - "on_*"
  whitelist_file: .repowise/dead_code_whitelist.txt
  analyze_on_update: true    # re-analyze dead code on incremental updates

maintenance:
  cascade_budget: 30
  staleness_decay_direct: 0.85
  staleness_decay_referenced: 0.95
  staleness_regen_threshold: 0.60
  staleness_warn_threshold: 0.75
  background_regen_schedule: "0 2 * * *"
  background_regen_budget: 100
  polling_interval_minutes: 15

rate_limits:
  anthropic:
    rpm: 50
    tpm: 100000
  openai:
    rpm: 500
    tpm: 150000
  ollama:
    rpm: 999999
    tpm: 999999
```