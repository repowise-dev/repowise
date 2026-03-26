# repowise — Complete Build Prompt (v2)
# Open-Source, Self-Hostable, Model-Agnostic Codebase Documentation Engine

---

## What You Are Building

**repowise** is an open-source codebase documentation engine. It generates a structured,
hierarchical wiki for any codebase, keeps it accurate as the code changes, and exposes
everything through an MCP server so AI assistants can query the wiki in real time.

This must work correctly on:
- A 200-file hobby project
- A 50,000-file enterprise monorepo (e.g., the scale of VS Code, Kubernetes, or Django)
- A multi-language repo (Python backend + TypeScript frontend + Rust extensions)
- A repo with circular imports, highly-connected utility modules, and generated code

Two distinct operational modes must be treated as first-class concerns throughout:

**INIT** — first-time documentation generation for an existing codebase
**MAINTENANCE** — keeping documentation accurate as code evolves via commits

Every architectural decision below is made with both modes in mind. Where they require
different strategies, both are specified.

---

## Monorepo Structure

```
repowise/
├── packages/
│   ├── core/              # Python: ingestion, dep graph, generation engine
│   ├── server/            # Python: FastAPI REST + webhook handler + MCP server
│   ├── cli/               # Python: repowise CLI (click)
│   └── web/               # Next.js 15: web UI
├── integrations/
│   ├── github-action/     # GitHub Action YAML + Dockerfile entrypoint
│   └── github-app/        # GitHub App webhook handler (extends server/)
├── providers/             # LLM provider adapters
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── docs/                  # repowise's own documentation (dogfooding)
├── pyproject.toml         # uv workspaces root
├── package.json           # Node workspaces root
├── CLAUDE.md              # This file
└── README.md
```

Python package management: **uv** with workspaces. Never pip directly.
Node package management: **pnpm** workspaces.

---

## Tech Stack

### Backend (Python 3.11+)
- **FastAPI** — REST API + webhook handler + SSE for job progress
- **tree-sitter** + **tree-sitter-languages** — multi-language AST parsing (40+ languages,
  single package, no per-language install step)
- **NetworkX** — dependency graph (directed multigraph). For repos >50K nodes, use a
  **SQLite-backed graph** via the `networkit` library as a drop-in for very large repos.
  Auto-switch based on node count threshold (configurable, default 30K).
- **SQLAlchemy 2.0** async + **Alembic** — ORM and migrations. SQLite dev, PostgreSQL prod.
- **LanceDB** (embedded, default) — vector store for semantic search and RAG-during-generation.
  Zero extra infra: runs as a library, stored in `.repowise/lancedb/`. Backed by the Lance
  columnar format; significantly faster than ChromaDB on both writes and ANN queries.
  When PostgreSQL is the SQL backend, **pgvector** is used instead — storing embeddings
  directly in the `wiki_pages.embedding` column so the entire system stays in one database.
- **APScheduler** — cron-based background jobs (staleness re-generation, polling fallback)
- **Celery + Redis** — job queue for multi-worker mode. Gracefully falls back to an
  asyncio in-process queue when Redis is unavailable (explicitly logged, never crashes).
- **httpx** — async HTTP client for provider calls
- **Pydantic v2** — config and data contracts
- **click** + **rich** — CLI with beautiful progress, tables, spinners
- **GitPython** — git operations: diff, log, blame, symbolic-ref tracking
- **watchdog** — filesystem watcher for `--watch` mode with debounce
- **structlog** — structured JSON logging with context binding
- **Jinja2** — prompt templates (user-overridable)
- **tiktoken** — token counting for all providers (estimate cost before generation)

### Frontend (Next.js 15 App Router, TypeScript strict)
- Tailwind CSS v4, MDX, Mermaid.js, Shiki, Fuse.js, Framer Motion, nuqs
- D3.js — dependency graph visualization
- next-mdx-remote — server-side MDX rendering from DB content

---

## Provider Abstraction Layer (`providers/`)

**Non-negotiable foundation. Build this before anything else.**

Every LLM call in the entire system goes through this abstraction.
Never import any provider SDK from business logic packages.

```python
# providers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: str

@dataclass
class GenerationRequest:
    messages: list[Message]
    system: str = ""
    max_tokens: int = 4096
    temperature: float = 0.2
    stream: bool = False
    # Provider-specific passthrough (e.g. {"top_p": 0.9})
    extra: dict = field(default_factory=dict)

@dataclass
class GenerationResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    cached_tokens: int = 0   # for providers that support prompt caching

@dataclass
class EmbedRequest:
    texts: list[str]
    input_type: Literal["document", "query"] = "document"

@dataclass
class EmbedResponse:
    embeddings: list[list[float]]
    model: str
    provider: str

class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResponse: ...

    @abstractmethod
    async def generate_stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    async def embed(self, request: EmbedRequest) -> EmbedResponse: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def supports_batch(self) -> bool:
        """Whether this provider supports async batch generation (cheaper for init)."""
        return False

    async def generate_batch(
        self, requests: list[GenerationRequest]
    ) -> list[GenerationResponse]:
        """Default: sequential fallback. Override for real batch support."""
        return [await self.generate(r) for r in requests]

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        """Return estimated USD cost, or None if unknown."""
        return None
```

Implement these providers:

**`providers/anthropic_provider.py`**
- Uses `anthropic` SDK. Supports all Claude models.
- Implements `supports_batch = True` via Anthropic's Message Batches API.
  `generate_batch()` submits a batch, polls until complete, returns all responses.
  Show batch mode as default for `repowise init` with `--no-batch` to disable.
- Implements **prompt caching**: For file-page generation, the system prompt +
  repository context is identical across many calls. Use cache-control breakpoints
  so this shared prefix is cached. Reduces cost by 60–90% on large repos.
- Implements `estimate_cost()` using current published token prices.
- `embed()` uses `voyage-3` via the Anthropic partner API. Falls back to a local
  sentence-transformers model if no API key for embeddings.
- Reads: `ANTHROPIC_API_KEY`.

**`providers/openai_provider.py`**
- Uses `openai` SDK with configurable `base_url`.
- Works with: OpenAI, Groq, Together AI, Mistral, DeepSeek, Azure OpenAI, LM Studio.
- For embeddings: uses `text-embedding-3-small` by default.
- Reads: `OPENAI_API_KEY`, `OPENAI_BASE_URL`.

**`providers/ollama_provider.py`**
- Uses Ollama's REST API directly. Zero SDK dependency.
- Supports fully offline/air-gapped/private usage.
- For embeddings: uses `nomic-embed-text` by default (auto-pulled via Ollama).
- Reads: `OLLAMA_BASE_URL` (default `http://localhost:11434`).
- Includes a health check: warn clearly if Ollama is unreachable before starting.

**`providers/litellm_provider.py`**
- Uses `litellm` as a universal adapter (100+ providers).
- Optional dependency: only loaded if `litellm` is installed.
- This is the escape hatch for any provider not explicitly supported.

**Rate Limiting (provider-aware, `providers/rate_limiter.py`)**

Different providers have different limits (requests/min, tokens/min). Implement a
token-bucket rate limiter per provider:

```python
class RateLimiter:
    """
    Token bucket algorithm. Each provider has two buckets:
    - requests_per_minute (RPM)
    - tokens_per_minute (TPM)
    
    Before each API call, acquire from both buckets.
    Buckets refill at their rated speed.
    On 429, back off exponentially and re-fill more conservatively.
    """
    async def acquire(self, estimated_tokens: int) -> None: ...
    async def on_rate_limit_error(self, retry_after: int | None) -> None: ...
```

Default limits per provider (overridable in config):
```yaml
rate_limits:
  anthropic:
    rpm: 50
    tpm: 100000
  openai:
    rpm: 500
    tpm: 150000
  ollama:
    rpm: 999999   # local, no limit
    tpm: 999999
```

**Provider config (`.repowise/config.yaml`):**
```yaml
provider: anthropic
model: claude-sonnet-4-6
embedding_provider: anthropic
embedding_model: voyage-3

# Optional: gitignore-style patterns to exclude during traversal.
# Applied on top of .gitignore and .repowiseIgnore.
# Set via `repowise init -x vendor/ -x 'src/generated/**'` or via the Web UI settings page.
exclude_patterns:
  - vendor/
  - src/generated/**
  - docs/legacy/

batch_mode: auto        # auto | always | never
                        # auto = use batch for init if provider supports it
prompt_caching: true    # use cache breakpoints where supported

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
  concurrent_jobs: 5     # parallel generation workers
  request_timeout: 120   # seconds per request
  max_retries: 3

git:
  enabled: true
  co_change_commit_limit: 500    # commits to analyze for co-changes (0 = unlimited)
  co_change_min_count: 3         # minimum co-occurrences to register a relationship
  blame_enabled: true            # git blame for ownership (slightly slower)
  prompt_commit_count: 10        # significant commits to include in generation prompts
  depth_auto_upgrade: true       # auto-upgrade depth for hotspots

dead_code:
  enabled: true
  detect_unreachable_files: true
  detect_unused_exports: true
  detect_unused_internals: false  # off by default — higher false positive rate
  detect_zombie_packages: true
  min_confidence: 0.4
  safe_to_delete_threshold: 0.7
  dynamic_patterns:
    - "*Plugin"
    - "*Handler"
    - "*Adapter"
    - "*Middleware"
    - "register_*"
    - "on_*"
  whitelist_file: .repowise/dead_code_whitelist.txt
  analyze_on_update: true

rate_limits:
  anthropic:
    rpm: 50
    tpm: 100000
```

---

## Core Engine (`packages/core/`)

---

### INIT PATH — First-Time Documentation

#### Stage 1: Repository Analysis

**File Traversal (`core/ingestion/traverser.py`)**

Traverse the repo tree, respecting:
1. `.gitignore` (parse properly using `pathspec` library — not a simple glob)
2. Root `.repowiseIgnore` (same syntax as .gitignore, user-defined exclusions at repo root)
3. **Per-directory `.repowiseIgnore`** — loaded from each visited directory (like git's
   per-directory `.gitignore`); patterns are relative to that directory.  Enables
   sub-folder exclusions without any UI (e.g. `generated/` in `src/.repowiseIgnore`
   skips only `src/generated/`)
4. **Extra exclude patterns** (`extra_exclude_patterns` constructor param) — gitignore-style
   patterns injected at runtime from `--exclude/-x` CLI flags or `repo.settings["exclude_patterns"]`
   (Web UI / REST API).  Applied to both directory pruning and individual file filtering
5. Hardcoded blocklist: `node_modules`, `.git`, `__pycache__`, `dist`, `build`,
   `.next`, `venv`, `.venv`, `vendor`, `.cache`, `coverage`, `*.min.js`, `*.min.css`,
   `*.lock`, `*.sum`, `go.sum`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`
6. Auto-detect and skip generated files: files with `// Code generated` header,
   protobuf-generated `*_pb2.py` / `*_pb.ts`, OpenAPI-generated clients
7. Binary files (detect by null bytes in first 8KB)
8. Files > 500KB (configurable via `max_file_size_kb`)

For each included file, return:
```python
@dataclass
class FileInfo:
    path: str               # relative to repo root
    abs_path: str
    language: str           # detected language string
    size_bytes: int
    git_hash: str           # SHA of last commit touching this file
    last_modified: datetime
    is_test: bool           # heuristic: test_, _test, spec_, _spec
    is_config: bool         # .yaml, .toml, .json, Dockerfile, Makefile, CI YAML
    is_api_contract: bool   # .proto, openapi.yaml, schema.graphql, swagger.json
    is_entry_point: bool    # main.py, index.ts, app.py, cmd/main.go, etc.
```

Language detection priority: file extension → shebang line → content heuristics.
Supported: Python, TypeScript, JavaScript, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift,
Kotlin, Scala, Dart, Haskell, Elixir, Lua, R, Shell/Bash, YAML (special handler),
Dockerfile (special handler), HCL/Terraform (special handler).

**Large File Strategy**

Files > `large_file_threshold_kb` (default: 100KB) get a different treatment during
generation: instead of passing the full source, extract only the public symbol signatures
and docstrings. Never pass a raw source file > 32K tokens to the LLM. Chunk strategy:
- If a file is large but has clear class/function boundaries (from AST), generate
  one sub-page per major class or function group, then synthesize a file page from those.
- If chunking is needed, generate with overlapping context windows (200-token overlap)
  to avoid losing context at chunk boundaries.
- Track that a page came from chunked generation in its metadata (`chunked: true`).

**Monorepo Detection and Package Isolation**

Detect monorepo structure automatically:
- Look for `packages/`, `apps/`, `libs/`, `services/` directories at depth 1-2
- Look for multiple `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml` at depth 1-2
- If detected: treat each package as a semi-independent documentation unit
- Cross-package dependencies are tracked in the graph as inter-package edges
- Each package gets its own module-level wiki page
- The repo overview page describes the monorepo structure with a package relationship diagram

Store monorepo structure in `RepoStructure`:
```python
@dataclass
class RepoStructure:
    is_monorepo: bool
    packages: list[PackageInfo]   # name, path, language, entry_points
    root_language_distribution: dict[str, float]  # {"python": 0.45, "typescript": 0.32}
    total_files: int
    total_loc: int
    entry_points: list[str]       # main files across all packages
```

---

#### Stage 2: AST Parsing and Symbol Extraction

**AST Parser (`core/ingestion/parser.py`)**

Use tree-sitter with tree-sitter-languages. Extract:

```python
@dataclass
class Symbol:
    id: str               # <file_path>::<symbol_name> (or ::<class>::<method>)
    name: str
    qualified_name: str   # full dotted name, e.g. "myapp.auth.service.AuthService"
    kind: Literal["function", "class", "method", "interface", "enum",
                  "constant", "type_alias", "decorator", "trait", "impl"]
    signature: str        # full signature string
    start_line: int
    end_line: int
    docstring: str | None
    decorators: list[str]
    visibility: Literal["public", "private", "protected", "internal"]
    is_async: bool
    complexity_estimate: int   # cyclomatic: count branches in function body
    language: str

@dataclass
class Import:
    raw_statement: str
    module_path: str          # normalized canonical path
    imported_names: list[str] # specific names imported, or ["*"]
    is_relative: bool
    resolved_file: str | None # absolute path if we can resolve it

@dataclass
class ParsedFile:
    file_info: FileInfo
    symbols: list[Symbol]
    imports: list[Import]
    exports: list[str]        # names exported by this file
    docstring: str | None     # module-level docstring
    parse_errors: list[str]   # tree-sitter parse errors (non-fatal)
```

**Special handlers for non-code file types:**

These are not parsed by tree-sitter but need documentation:

- **OpenAPI/Swagger** (`.yaml`/`.json` with `openapi:` key): Parse with PyYAML/json.
  Extract endpoints, schemas, authentication. Generate an API reference page.
- **GraphQL schema** (`.graphql`, `.gql`): Use `graphql-core` to parse.
  Extract types, queries, mutations, subscriptions.
- **Protobuf** (`.proto`): Parse manually or with `grpcio-tools`. Extract services,
  messages, enums.
- **Dockerfile**: Parse instructions (FROM, RUN, COPY, EXPOSE, ENV, ENTRYPOINT).
  Generate a deployment setup page explaining what the container does.
- **GitHub Actions / CI YAML** (`.github/workflows/*.yml`): Parse jobs, steps, triggers.
  Generate a CI/CD pipeline page explaining the build and release process.
- **Makefile**: Extract targets and their dependencies. Generate a "common commands" page.
- **Terraform / HCL** (`.tf`): Extract resources, modules, variables.
  Generate infrastructure documentation.
- **docker-compose.yml**: Extract services, ports, volumes. Generate a "local development
  setup" page.

---

#### Stage 3: Dependency Graph Construction

**Graph Builder (`core/ingestion/graph.py`)**

Build a directed multigraph. Allow multiple edges between the same two nodes
(a file can both import and call symbols from another file).

```python
Node types:
  - "file"    — every source file is a node
  - "symbol"  — every function/class/interface/etc.
  - "package" — every package/module directory
  - "external" — third-party packages (light node, not fully documented)

Edge types:
  - "imports"      — file A imports file B
  - "calls"        — symbol A calls symbol B
  - "inherits"     — class A extends class B
  - "implements"   — class A implements interface B
  - "instantiates" — code in A creates an instance of B
  - "references"   — A mentions B (looser — type annotation, generic, etc.)
  - "re-exports"   — A re-exports symbols from B (barrel files)
  - "inter_package" — edge crossing package boundary (important for monorepos)
```

After graph construction, compute and store:
1. **PageRank** — high-rank = central, well-connected. Used to prioritize generation
   and decide which symbols get "spotlight" pages.
2. **Strongly Connected Components (SCCs)** — detect circular dependency clusters.
   Log them as warnings. For generation order: treat each SCC as a single unit,
   generate a "circular dependency" note in the wiki pages involved.
3. **Betweenness centrality** — identifies "bridge" symbols — if removed, the graph
   splits. These are the most critical to document well.
4. **Module clustering** — group files into logical clusters by import density
   (Louvain community detection). These clusters become the module-level wiki pages.
   Even if the directory structure doesn't group them, the import graph might reveal
   natural modules.

**Graph Persistence and Scalability**

For repos up to 30K nodes: use NetworkX in-memory, serialize to `.repowise/graph.json`
using `networkx.node_link_data()`.

For repos > 30K nodes: switch to SQLite-backed graph. Store nodes and edges in the
repowise database (`graph_nodes` and `graph_edges` tables). Load only the subgraph
needed for a given operation (e.g., only the 2-hop neighborhood of changed files).
The threshold is configurable: `graph_backend: networkx | sqlite | auto`.

**On incremental runs: never rebuild the full graph.** Load the persisted graph,
re-parse only changed files, remove their old nodes/edges, add new ones.
Store the graph generation commit hash; if it's too far behind, offer a full rebuild.

---

#### Stage 4: Hierarchical Generation (INIT)

**Generation Order for Init — Critical**

Generation must follow this exact dependency-aware order:

```
Level 0: External API contracts (OpenAPI, proto, GraphQL)
         → these are self-contained, generate first
Level 1: Symbol-level docs for high-PageRank symbols (top 10%)
         → parallelizable, short, fast
Level 2: File pages (parallelizable within each level)
         → uses: file source, symbol docs, import/export relationships
Level 3: SCC pages (circular dependency clusters)
         → must wait for all member files to complete Level 2
Level 4: Module/package pages
         → uses: all file pages within the module
Level 5: Cross-package relationship pages (monorepos only)
         → uses: all package pages
Level 6: Repository overview + architecture diagram
         → uses: all module pages + graph metrics
Level 7: Config/infra pages (Dockerfile, CI, etc.)
         → these reference code pages, so come after code docs
Level 8: Index pages (symbol index, file index, search index)
         → built from all completed pages
```

Within each level, fire up to `concurrent_jobs` tasks in parallel using `asyncio.Semaphore`.

**Circular Dependency Handling**

When a file belongs to an SCC (cycle), it cannot be generated bottom-up normally.
Strategy:
1. Generate each file in the SCC with a reduced context (only its own content +
   signatures of cycle partners, no full docs)
2. Generate a separate "Circular Dependency" wiki page for the SCC that explains
   the cycle, why it exists, and how to navigate it
3. After all files in the SCC have stubs, upgrade them with a second pass that adds
   cross-references

**Resumable Jobs (critical for large repos)**

Init on a 50K-file repo can take 30–90 minutes and cost significant API budget.
A crash should not require starting over.

Every job tracks its state in the database:
```python
class GenerationJob(Base):
    id: str
    repo_id: str
    job_type: str          # full_init | incremental | single_page | batch_init
    status: str            # pending | running | completed | failed | paused
    
    # Checkpoint fields
    checkpoint_level: int          # 0-8 (which generation level we're on)
    checkpoint_file_index: int     # within the current level
    completed_page_ids: JSON       # list of page IDs already generated
    failed_page_ids: JSON          # list of page IDs that failed (will retry)
    
    pages_total: int
    pages_done: int
    pages_failed: int
    tokens_input: int
    tokens_output: int
    estimated_cost_usd: float | None
    
    started_at: datetime | None
    completed_at: datetime | None
    last_heartbeat: datetime        # updated every 30s, detect stale jobs
    error_message: str | None
```

On restart after crash: load the job, check `completed_page_ids`, skip those,
resume from `checkpoint_level` and `checkpoint_file_index`.

**`repowise init` must be idempotent.** Running it twice on the same repo produces the
same result. Running it after a partial previous run completes the remaining pages only.
Flag `--force` to override and regenerate everything.

**Pre-generation Cost Estimate**

Before generating a single page:
1. Count total tokens across all files (use tiktoken)
2. Estimate output tokens (heuristic: 800 tokens per file page, 1200 per module page)
3. Compute estimated cost using `provider.estimate_cost()`
4. Show breakdown table:
   ```
   ┌─────────────────────────────────────────────────────┐
   │  Generation Plan                                     │
   ├──────────────────────────┬──────────────────────────┤
   │  File pages              │  847                     │
   │  Module pages            │  23                      │
   │  Symbol spotlight pages  │  94                      │
   │  API contract pages      │  3                       │
   │  Config/infra pages      │  7                       │
   │  Repository overview     │  1                       │
   │  Total pages             │  975                     │
   ├──────────────────────────┼──────────────────────────┤
   │  Estimated input tokens  │  4.2M                    │
   │  Estimated output tokens │  980K                    │
   │  Estimated cost          │  ~$8.40 (claude-sonnet)  │
   │  Estimated time          │  ~18 minutes (5 workers) │
   │  Batch mode              │  ON (50% cheaper → ~$4.20│
   └──────────────────────────┴──────────────────────────┘
   ```
5. Prompt for confirmation if cost > `$2.00` and `--yes` not passed.
   Show `--batch` to enable Anthropic Batch API (async, 50% cheaper, ~1hr turnaround).

---

#### Stage 5: Context Assembly for Generation

**This is the most important quality driver. Never just dump raw source code.**

For each generation call, the `ContextAssembler` builds an optimized context window:

```python
class ContextAssembler:
    def assemble_file_context(
        self,
        file: ParsedFile,
        graph: DiGraph,
        existing_pages: dict[str, WikiPage],  # pages already generated
        token_budget: int = 12000             # leave room for output
    ) -> FileContext:
        """
        Builds context in order of information density:
        1. File source (truncated if large)
        2. Symbol signatures + existing docstrings from THIS file
        3. For each import: summary of the imported module (from existing pages if available,
           else just the public API signatures)
        4. For each file that imports THIS file: what it uses from here
        5. Graph metrics: PageRank, betweenness, which cluster this belongs to
        6. Relevant existing pages via semantic search (RAG): query the vector store
           (LanceDB or pgvector) with the file's module path + top symbols, return top 3
           most relevant page summaries
        
        If total context exceeds token_budget, drop in reverse priority order.
        Always keep: file source (possibly chunked) + direct imports.
        """
```

**RAG During Generation (not just for search)**

Use the vector store (LanceDB or pgvector) to find relevant already-generated pages when generating a new page.
This propagates understanding upward through the hierarchy:

- When generating a file page: query with the file's top 3 exported symbol names.
  Include up to 3 matching page summaries (first 300 words each) as context.
  This means if `AuthService` imports from `UserRepository`, and `UserRepository`'s
  page is already generated, the `AuthService` page will understand what
  `UserRepository` does, producing richer documentation.
- When generating a module page: include the file pages from that module as context.
  If they don't fit in the context window, summarize them (one-paragraph summary per file).
- When generating the repo overview: use module page summaries + graph centrality stats.

---

### MAINTENANCE PATH — Keeping Docs in Sync

#### Change Detection (`core/ingestion/differ.py`)

```python
class ChangeDetector:
    def get_changed_files(
        self, repo_path: str, since_commit: str, until_commit: str = "HEAD"
    ) -> ChangedFiles:
        """
        Uses GitPython. Returns:
        - added: list[str]
        - modified: list[str]
        - deleted: list[str]
        - renamed: list[tuple[str, str]]  # (old_path, new_path) — CRITICAL
        """
    
    def detect_symbol_renames(
        self, old_file: ParsedFile, new_file: ParsedFile
    ) -> list[SymbolRename]:
        """
        Heuristic: if a symbol disappears in old_file and a same-kind symbol
        with a similar signature appears in new_file, treat it as a rename.
        Use git-blame on the new file to confirm line provenance.
        
        Returns list of SymbolRename(old_name, new_name, kind, confidence).
        """
    
    def get_affected_pages(
        self,
        changed_files: ChangedFiles,
        symbol_renames: list[SymbolRename],
        graph: DiGraph,
        cascade_budget: int  # max pages to cascade regenerate per run
    ) -> AffectedPages:
        """
        For each changed file:
          1. Find all wiki pages that document this file (direct pages)
          2. Walk the graph: find all pages that REFERENCE symbols from this file
             (1-hop for strong references like inherits/calls,
              2-hop for weak references like references)
          3. For renamed symbols: find all pages containing the old symbol name
             and mark them for rename-patch (cheaper than full regen)
          4. Apply cascade_budget: if total affected > budget, sort by PageRank
             and regenerate only top-N, mark the rest as stale (confidence decay)
        
        Returns:
          AffectedPages(
            regenerate: list[str],     # page IDs to fully regenerate
            rename_patch: list[str],   # pages needing symbol rename text patch only
            decay_only: list[str],     # pages to mark stale without regenerating
          )
        """
```

**Cascade Budget**

A change to a central utility module (e.g., `utils.py` imported by 200 files) would
naively require regenerating 200 pages on every push. This is too expensive.

The `cascade_budget` config option (default: 30 pages per maintenance run) caps how
many pages are fully regenerated. Pages beyond the budget get confidence decay only
and are queued for the background staleness cron job.

```yaml
maintenance:
  cascade_budget: 30          # max pages to fully regenerate per push
  staleness_decay_direct: 0.85    # confidence multiplier for directly changed pages
  staleness_decay_referenced: 0.95 # confidence multiplier for referencing pages
  staleness_regen_threshold: 0.60  # below this → queue for background regen
  staleness_warn_threshold: 0.75   # below this → show yellow badge in UI
  background_regen_schedule: "0 2 * * *"  # cron: nightly at 2am
  background_regen_budget: 100    # max pages per background run
```

**Symbol Rename Handling**

When a symbol is renamed (detected by `detect_symbol_renames`):
1. For pages that fully document the old symbol: queue full regeneration
2. For pages that only *mention* the old symbol name: apply a targeted text patch —
   scan the page's markdown, replace `old_name` with `new_name` in all code spans
   and backtick references. Update the `symbols_referenced` index.
   This is much cheaper than full regeneration and preserves surrounding context.
3. Record the rename in a `SymbolRename` history table for audit trail.

**File Rename/Move Handling**

When a file is renamed (git tracks this):
1. Update all `source_files` references in wiki pages
2. Update all URL slugs that included the old file path
3. Update the dependency graph (remove old node, add new node with same edges)
4. Regenerate the file page (path context has changed)
5. Do NOT regenerate pages that only reference the file's symbols (paths don't appear
   in generated docs — only symbol names do)

**Webhook Reliability**

Webhooks can be missed (server downtime, network issues). Do not rely solely on webhooks.

**Two-layer sync strategy:**

Layer 1 (real-time): GitHub/GitLab webhook → push event → trigger incremental update job

Layer 2 (polling fallback): APScheduler job runs every N minutes (configurable, default 15).
Compare the repo's `last_sync_commit` to the actual `HEAD` of the main branch.
If they differ, compute the diff and trigger an incremental update.
For GitHub-connected repos, use the GitHub REST API to check HEAD.
For local repos, use `git rev-parse HEAD` via GitPython.

The polling fallback means: even if a webhook is missed, docs will be at most
`polling_interval` minutes stale for the main branch.

Store `last_sync_commit` in the `Repo` table. Update atomically after each successful
sync (never update it if the sync job fails partway through).

**State File (`.repowise/state.json`)**

Persisted alongside the wiki. Used by CLI for offline operation (no DB required for
simple `repowise update` on local repos):

```json
{
  "repo_id": "abc123",
  "last_sync_commit": "a1b2c3d",
  "last_sync_at": "2025-03-18T10:30:00Z",
  "total_pages": 975,
  "stale_pages": 12,
  "schema_version": 2
}
```

**Background Staleness Resolution**

APScheduler runs nightly (configurable) to:
1. Query all pages with `confidence_score < staleness_regen_threshold`
2. Sort by `confidence_score ASC` (most stale first)
3. Regenerate up to `background_regen_budget` pages
4. Log token usage and cost
5. Update the staleness metrics shown in the web UI dashboard

This ensures that even pages beyond the cascade budget eventually get regenerated.
The combination of real-time cascade + background resolution means no page stays
stale indefinitely.

**PR Documentation Preview**

When a pull_request event is received (GitHub webhook):
1. Check out the PR branch (or receive the commit range from the webhook)
2. Run `ChangeDetector.get_affected_pages()` with `--dry-run` (no regeneration)
3. Post a GitHub PR comment listing:
   - Pages that will be regenerated
   - Pages that will have confidence decay
   - New pages that will be created (new files)
   - Pages that will be deleted (deleted files)
   - Estimated token cost for this PR's docs update
4. On PR merge: trigger the actual incremental update

---

### Confidence Score System

Every `WikiPage` has a `confidence_score` float (0.0 to 1.0) and a `freshness_status`:

```python
def compute_freshness_status(score: float) -> str:
    if score >= 0.80: return "fresh"      # green badge
    if score >= 0.60: return "stale"      # yellow badge
    return "outdated"                      # red badge — auto-queued for regen
```

Score decay rules:
- Freshly generated: `1.0`
- Source file directly changed: `score *= 0.85`
- Referenced symbol changed (1-hop): `score *= 0.95`
- Referenced symbol changed (2-hop): `score *= 0.98`
- Beyond cascade budget (time-based decay, 1 week since change): `score *= 0.90`

A page below `0.30` is considered unusable and is shown with a prominent warning
in the web UI. Regeneration is auto-triggered for these regardless of cascade budget.

Store per-page:
- `confidence_score: float`
- `source_commit: str` — commit at which this page was last generated
- `freshness_status: str` — computed from score
- `stale_since: datetime | None` — when score first dropped below 0.75
- `regen_queued: bool` — whether it's in the background regen queue

---

## Database Schema (`packages/core/db/models.py`)

```python
class Repo(Base):
    __tablename__ = "repos"
    id: str (PK, uuid4)
    name: str
    path: str               # absolute path for local repos
    remote_url: str | None  # github.com/user/repo for remote repos
    default_branch: str     # main | master | etc.
    provider: str
    model: str
    is_monorepo: bool
    packages: JSON          # list of PackageInfo if monorepo
    last_sync_commit: str | None
    last_sync_at: datetime | None
    total_files: int
    total_symbols: int
    total_pages: int
    stale_pages: int
    created_at: datetime

class WikiPage(Base):
    __tablename__ = "wiki_pages"
    id: str (PK)
    repo_id: str (FK → repos.id, CASCADE DELETE)
    page_type: str          # repo | module | file | symbol | api_contract | infra
    title: str
    slug: str               # URL-safe slug, unique per repo
    content_md: text
    content_html: text      # cached rendered HTML, nullable (regenerated on read if null)
    source_files: JSON      # list of source file paths
    symbols_referenced: JSON
    confidence_score: float
    freshness_status: str
    stale_since: datetime | None
    regen_queued: bool
    source_commit: str
    generated_at: datetime
    provider: str
    model: str
    tokens_input: int
    tokens_output: int
    chunked: bool           # true if generated from chunked large file
    language: str | None    # primary language of documented code

class PageVersion(Base):
    __tablename__ = "page_versions"
    id: int (PK autoincrement)
    page_id: str (FK → wiki_pages.id, CASCADE DELETE)
    content_md: text
    confidence_score: float
    source_commit: str
    generated_at: datetime
    diff_summary: str | None  # brief description of what changed (generated by LLM)

class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id: str (PK, uuid4)
    repo_id: str (FK)
    job_type: str           # full_init | incremental | single_page | background_regen
    status: str             # pending | running | completed | failed | paused
    trigger: str            # cli | webhook | api | scheduler | pr_preview
    since_commit: str | None
    until_commit: str | None
    checkpoint_level: int
    checkpoint_file_index: int
    completed_page_ids: JSON
    failed_page_ids: JSON
    pages_total: int
    pages_done: int
    pages_failed: int
    tokens_input: int
    tokens_output: int
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    started_at: datetime | None
    completed_at: datetime | None
    last_heartbeat: datetime
    error_message: str | None

class Symbol(Base):
    __tablename__ = "symbols"
    id: str (PK)            # <repo_id>::<file_path>::<qualified_name>
    repo_id: str (FK)
    file_path: str
    name: str
    qualified_name: str
    kind: str
    signature: str
    language: str
    visibility: str
    is_async: bool
    complexity_estimate: int
    pagerank_score: float
    betweenness_score: float
    page_id: str | None (FK → wiki_pages.id)

class SymbolRenameHistory(Base):
    __tablename__ = "symbol_rename_history"
    id: int (PK autoincrement)
    repo_id: str (FK)
    old_name: str
    new_name: str
    file_path: str
    kind: str
    confidence: float
    detected_at_commit: str
    detected_at: datetime

class GraphNode(Base):
    __tablename__ = "graph_nodes"  # only used when graph_backend = sqlite
    id: str (PK)
    repo_id: str (FK)
    node_type: str          # file | symbol | package | external
    attributes: JSON

class GraphEdge(Base):
    __tablename__ = "graph_edges"  # only used when graph_backend = sqlite
    id: int (PK autoincrement)
    repo_id: str (FK)
    source_id: str
    target_id: str
    edge_type: str
    attributes: JSON

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: str (PK, uuid4)
    repo_id: str (FK)
    source: str             # github | gitlab
    event_type: str         # push | pull_request | etc.
    payload: JSON
    received_at: datetime
    processed_at: datetime | None
    job_id: str | None (FK → generation_jobs.id)
    status: str             # received | processing | processed | failed
```

---

## Prompt Templates (`packages/core/generation/prompts/`)

All prompts are Jinja2 templates. Users can override any template by placing a file
with the same name in `.repowise/prompts/`. This is how power users customize generation.

**`file_page.j2`**:
```jinja2
You are a senior software engineer writing accurate, dense technical documentation.
Your audience: senior engineers who are new to this codebase.
Write documentation that is precise, example-rich, and free of filler.

## Repository: {{ repo_name }}
Language: {{ language }} | Module: {{ module_path }}
PageRank cluster: {{ cluster_name }} | File importance: {{ "high" if pagerank > 0.7 else "medium" if pagerank > 0.3 else "standard" }}

## File: `{{ file_path }}`
{% if module_doc_summary %}
## This module's purpose (from existing module docs):
{{ module_doc_summary }}
{% endif %}

## Source code:
```{{ language }}
{{ source_code }}
```

## Symbols exported by this file:
{% for s in exported_symbols %}
- `{{ s.name }}` ({{ s.kind }}, {{ s.visibility }}{% if s.is_async %}, async{% endif %}): `{{ s.signature }}`{% if s.docstring %} — {{ s.docstring }}{% endif %}

{% endfor %}

## Import relationships:
Imports from: {% for i in imports %}{{ i }}{% if not loop.last %}, {% endif %}{% endfor %}
Imported by: {% for i in imported_by %}{{ i }}{% if not loop.last %}, {% endif %}{% endfor %}

{% if related_page_summaries %}
## Related components (from existing wiki):
{% for p in related_page_summaries %}
### {{ p.title }}
{{ p.summary }}
{% endfor %}
{% endif %}

{% if is_in_scc %}
⚠️ This file participates in a circular dependency with: {{ scc_members | join(', ') }}
{% endif %}

Generate a wiki page in Markdown with these sections:

### Overview
2–3 sentences: what this file does, why it exists, what problem it solves.
Do not say "This file contains..." — describe purpose and role.

### Architecture
How the internals are organized. Include a Mermaid diagram if there are non-trivial
relationships between the classes or functions in this file. Omit if the file is simple.

### Public API
Document every public/exported symbol:
- Full signature
- What it does (one dense paragraph)
- Parameters and return values (only if non-obvious from types)
- A runnable usage example for each significant symbol
- Exceptions/errors it can raise

### Usage Patterns
How is this file typically used? Show 1–2 realistic examples of calling code.
If you can infer usage patterns from the "imported by" context, show those.

### Dependencies
What notable dependencies does this file have and why?
Only list dependencies that are non-obvious or important to understand the file's behavior.

### Error Handling and Edge Cases
Non-obvious behaviors, important edge cases, thread-safety notes, performance considerations.
Omit this section if there is nothing non-obvious to say.

Rules:
- Every symbol name, parameter, and type in backticks
- Code examples must be correct and runnable, using this file's actual API
- If existing docstrings are present, expand them — do not just copy them
- Use Mermaid for diagrams (flowchart, sequence, or class — choose the most appropriate)
- Dense prose, no bullet points for narrative text
- Do not include a "Source Code" section — the UI links to source directly
```

**`module_page.j2`** — generate module overview from its file summaries.
**`repo_overview.j2`** — generate repo overview from module summaries + graph metrics.
**`architecture_diagram.j2`** — generate a Mermaid diagram from graph structure.
**`symbol_spotlight.j2`** — detailed page for a single high-PageRank symbol.
**`api_contract.j2`** — generate API reference from OpenAPI/proto/GraphQL parsing.
**`infra_page.j2`** — document Dockerfile, CI configs, Makefiles.
**`scc_page.j2`** — explain a circular dependency cluster.
**`diff_summary.j2`** — generate a one-sentence summary of what changed between
  two versions of a page (used in `PageVersion.diff_summary`).

---

## CLI (`packages/cli/`)

```
repowise init [PATH]
  --provider PROVIDER     anthropic|openai|ollama|litellm
  --model MODEL
  --workers N             parallel workers (default: 5)
  --batch                 use async batch API (cheaper, slower — only for init)
  --no-cache              skip prompt cache
  --dry-run               analyze only, show generation plan, no LLM calls
  --yes / -y              skip confirmation prompts
  --resume                resume a previously interrupted init job
  --force                 regenerate all pages, ignore existing
  --skip-tests            do not document test files
  --skip-infra            do not document Dockerfiles, CI, Makefile, etc.
  --include-private       also document private/internal symbols
  --exclude / -x PATTERN  gitignore-style pattern to exclude (repeatable).
                          Merged with exclude_patterns from config.yaml.
                          Example: -x vendor/ -x 'src/generated/**'
  --config PATH

repowise update [PATH]
  --since COMMIT          override auto-detected last-sync commit
  --force                 regenerate all affected pages, ignore confidence scores
  --cascade-budget N      max pages to regenerate (default from config)
  --dry-run               show what would change, no LLM calls

repowise serve [PATH]
  --port 7337
  --host 0.0.0.0
  --workers N             uvicorn workers (default: 1)

repowise watch [PATH]
  --debounce-ms 2000      wait this long after last save before triggering update

repowise search QUERY [PATH]
  --type semantic|fulltext|symbol
  --limit N

repowise export [PATH]
  --format markdown|html|json|pdf
  --output DIR

repowise status [PATH]
  # Shows: last sync commit, total pages, stale pages breakdown, 
  # pending regen queue, token usage totals, estimated monthly cost at current rate

repowise doctor [PATH]
  # Validates: provider connection, git setup, DB integrity,
  # graph consistency (detects orphaned nodes), missing config, etc.
  # Outputs a health check table. Run this to debug setup issues.

repowise mcp [PATH]
  --port 7338
  --transport stdio|sse   stdio for Claude Code/Cursor, sse for web clients
```

---

## REST API (`packages/server/`)

**Routers:**

`/api/repos` — CRUD + `POST /sync`, `POST /repos/{id}/full-resync`

`/api/pages` — list, get, versions history, force-regenerate single page

`/api/search` — semantic and fulltext search

`/api/jobs` — job status, SSE progress stream (`/api/jobs/{id}/stream`)

`/api/symbols` — symbol lookup, search, dependency path query

`/api/webhooks/github` — verify `X-Hub-Signature-256`, parse push/PR events,
  store in `WebhookEvent` table, enqueue job. Respond 200 immediately — never block.

`/api/webhooks/gitlab` — same, verify `X-Gitlab-Token`

`/api/graph` — export graph as JSON (D3-compatible format), query dependency path

`/api/repos/{id}/git-metadata?file_path=...` — git metadata for a single file
`/api/repos/{id}/hotspots?limit=20` — high-churn + high-complexity files
`/api/repos/{id}/ownership?granularity=file|module|package` — ownership breakdown
`/api/repos/{id}/co-changes?file_path=...&min_count=3` — co-change partners
`/api/repos/{id}/git-summary` — aggregate git health signals for dashboard

`/api/repos/{id}/dead-code?kind=&min_confidence=&safe_only=&status=open` — dead code findings
`POST /api/repos/{id}/dead-code/analyze` — trigger fresh dead code analysis
`PATCH /api/dead-code/{finding_id}` — resolve/acknowledge a finding
`/api/repos/{id}/dead-code/summary` — aggregate dead code stats

`/health` — liveness + readiness (checks DB connection, provider reachability)
`/metrics` — Prometheus-compatible metrics endpoint (token usage, job counts, etc.)

**Auth**: Optional API key auth. Set `REPOWISE_API_KEY` env var to enable.
When set, all non-`/health` endpoints require `Authorization: Bearer <key>`.
Default (no key set): fully open, suitable for local use.

---

## MCP Server (`packages/server/mcp.py`)

Use the **MCP Python SDK** (`pip install mcp`). Expose as both:
1. **stdio transport** — for Claude Code, Cursor, Cline (add to their config)
2. **SSE transport** — for web-based MCP clients and future use

**All 13 tools — detailed specs:**

**Original 8 tools:**

### `get_overview`
Returns the repository overview: architecture summary, module map, key entry points.
Best first call when starting to explore an unfamiliar codebase.
```
Input:  { repo?: string }
Output: { title, content_md, architecture_diagram_mermaid?, key_modules: [{name, path, description}], entry_points: [string] }
```

### `get_module_docs`
Returns the wiki page for a module/package/directory.
```
Input:  { module_path: string, repo?: string }
Output: { title, content_md, files: [{path, description, confidence_score}], public_api_summary: string }
```

### `get_file_docs`
Returns the wiki page for a specific file.
```
Input:  { file_path: string, repo?: string }
Output: { title, content_md, symbols: [{name, kind, signature}], imported_by: [string], confidence_score, freshness_status }
```

### `get_symbol`
Look up any symbol by name (fuzzy match supported — returns top candidates if exact match fails).
```
Input:  { symbol_name: string, kind?: string, repo?: string }
Output: { name, qualified_name, kind, signature, file_path, documentation, used_by: [string], confidence_score }
```

### `search_codebase`
Semantic search over the full wiki. Ask in natural language.
```
Input:  { query: string, limit?: number (default 5), page_type?: string, repo?: string }
Output: { results: [{ page_id, title, page_type, snippet, relevance_score, confidence_score }] }
```

### `get_architecture_diagram`
Returns a Mermaid diagram. Type is automatically chosen based on scope.
```
Input:  { scope: "repo"|"module"|"file", path?: string, diagram_type?: "auto"|"flowchart"|"class"|"sequence", repo?: string }
Output: { diagram_type, mermaid_syntax, description }
```

### `get_dependency_path`
Find how two symbols/modules are connected in the dependency graph.
```
Input:  { from: string, to: string, repo?: string }
Output: { path: [{node, relationship, edge_type}], distance: number, explanation: string }
```

### `get_stale_pages`
List pages whose confidence has dropped. Useful for knowing what docs to distrust.
```
Input:  { threshold?: number (default 0.6), repo?: string }
Output: { stale_pages: [{ id, title, page_type, confidence_score, stale_since, source_files }] }
```

**5 new git intelligence + dead code tools:**

### `get_file_history`
Return the git history for a file — the WHY behind its current structure.
Call this before making changes to understand why code was written as it was.
```
Input:  { file_path: string, repo?: string, limit?: number (default 10) }
Output: { file_path, age_days, primary_owner: {name, email, pct},
          is_hotspot, is_stable, commit_count_total, commit_count_90d,
          significant_commits: [{sha, date, message, author}],
          co_change_partners: [{file_path, co_change_count}] }
```

### `get_hotspots`
Return the riskiest files: high churn AND high complexity.
These are the files most likely to contain bugs.
```
Input:  { repo?: string, limit?: number (default 10), include_stable?: boolean (default false) }
Output: { hotspots: [{file_path, commit_count_90d, complexity_estimate,
                      churn_percentile, primary_owner, wiki_page_id}],
          stable_files: [...] (if include_stable=true) }
```

### `get_codebase_ownership`
Return ownership breakdown. Identifies knowledge silos (modules owned
by one person) and abandoned areas (no recent commits).
```
Input:  { repo?: string, by?: "file"|"module"|"package" (default "module") }
Output: { by_module: [{module_path, primary_owner, owner_pct, contributor_count,
                       is_silo, last_commit_days_ago}] }
```

### `get_co_changes`
Return files that frequently change together with the given file,
even without a static import relationship. Essential before refactoring.
```
Input:  { file_path: string, repo?: string, min_count?: number (default 3) }
Output: { file_path,
          co_change_partners: [{file_path, co_change_count,
                                has_import_relationship,
                                wiki_page_snippet: string | null}] }
```

### `get_dead_code`
Return dead and unused code findings. Use before cleanup tasks.
Results sorted by confidence desc, then lines desc (biggest wins first).
```
Input:  { repo?: string, kind?: string, min_confidence?: number (default 0.6),
          safe_only?: boolean (default false), limit?: number (default 20) }
Output: { summary: {total_findings, deletable_lines, safe_to_delete_count, by_kind},
          findings: [{kind, file_path, symbol_name, confidence, reason,
                      safe_to_delete, lines, last_commit_at,
                      primary_owner, age_days}] }
```

**Update existing `get_blast_radius`** — add co-change partners to output alongside
graph-based dependencies.

**Auto-generated MCP config** (printed at end of `repowise init`, saved to `.repowise/mcp.json`):

```json
{
  "mcpServers": {
    "repowise": {
      "command": "repowise",
      "args": ["mcp", "--repo", "/absolute/path/to/repo", "--transport", "stdio"],
      "description": "repowise: live documentation for this codebase"
    }
  }
}
```

Include setup instructions for Claude Code (`~/.claude/claude.json`), Cursor
(`.cursor/mcp.json`), and Cline (`cline_mcp_settings.json`).

---

## Web UI (`packages/web/`)

### Design Language
Engineering precision meets editorial clarity. Linear's exactness + Stripe's docs quality.

- **Background**: `#0a0a0a`, text `#f0f0f0`, accent `#5B9CF6`
- **Monospace**: `Berkeley Mono` or `Geist Mono` for paths, code, and slugs
- **Prose**: `Geist` for all documentation text  
- **Confidence colors**: `#22c55e` (fresh ≥0.80), `#eab308` (stale 0.60–0.79), `#ef4444` (outdated <0.60)
- Three-column layout: nav sidebar + wiki content + context panel (TOC + symbols)

### Pages

**`/`** — Dashboard: all repos, sync status, stale counts, recent jobs, token/cost summary

**`/repos/[id]`** — Repo overview: generated overview wiki page, file tree sidebar,
last sync info, architecture diagram

**`/repos/[id]/wiki/[...slug]`** — Wiki page:
- MDX rendering with auto-linked symbols (backtick identifiers become hover cards)
- Mermaid diagrams rendered inline (client-side, lazy)
- Right panel: TOC + referenced symbols + dependency mini-map (D3, 1-hop neighbors)
- Top bar: confidence badge + "Regenerate" button + model/commit metadata
- Bottom: version history dropdown (show diff between versions)

**`/repos/[id]/search`** — Semantic + fulltext search, grouped by type, confidence badges

**`/repos/[id]/graph`** — Interactive D3 force-directed graph:
- Nodes: colored by language, sized by PageRank
- Edges: colored by type (imports vs calls vs inherits)
- Click node → navigate to wiki page
- Hover node → show symbol/file summary tooltip
- Filter panel: by language, package, edge type, freshness
- Additional filters: color by owner/churn/freshness, size by churn/complexity/LOC
- Co-change edge toggle: dashed purple lines, thickness = co_change_count
- Dead code filter pill: red dashed border + reduced opacity on dead code nodes

**`/repos/[id]/symbols`** — Symbol index: searchable table sortable by PageRank.
  Columns: name, kind, file, PageRank, complexity, visibility, wiki link

**`/repos/[id]/coverage`** — Documentation coverage page:
  What % of files/symbols have wiki pages? What % are fresh vs stale?
  Useful for teams tracking documentation health as a metric.

**`/repos/[id]/ownership`** — Ownership treemap:
- Each cell = one file, colored by primary owner (unique color per contributor)
- Cell area = LOC. Click a cell → opens wiki page
- Sidebar: all contributors with total % ownership, file count, last commit date
- Amber highlight on cells where `owner_pct > 0.8` (knowledge silos)

**`/repos/[id]/hotspots`** — Hotspot list:
- Top 20 hotspot files with inline bar charts: churn bar + complexity bar
- Each row links to wiki page. Filter by package, owner
- "Why is this a hotspot?" tooltip

**`/repos/[id]/dead-code`** — Dead code report:
- Three tabs: Files | Exports | Internals
- Sortable table: file/symbol, package, confidence dots, lines, last touched, owner,
  safe-to-delete checkbox, resolve button
- Summary header: "7 unreachable files, 23 unused exports, ~2,847 deletable lines"
- Dashboard stat card linking here

**`/settings`** — Provider config editor, polling interval, cascade budget

### Key Components

**`<WikiPage />`** — MDX renderer:
- Auto-links: any `` `SymbolName` `` that matches a known symbol becomes a link + hover card
- Hover card shows: signature, which file, confidence badge, link to wiki page
- Mermaid blocks rendered with `mermaid.js` (lazy, only in viewport)
- Code blocks: Shiki highlighting, "Copy", "View in source" (links to line in GitHub/GitLab)
- Inline confidence badges on any `[[page references]]` or linked pages

**`<ConfidenceBadge score={0.73} status="stale" />**` — shows freshness, clickable for detail

**`<DependencyMiniMap symbols={[...]} graphData={...} />`** — small D3 1-hop graph in sidebar

**`<GenerationProgress jobId="..." />`** — SSE-connected live progress (pages done/total, current page, tokens used, cost)

**`<PageVersionDiff pageId="..." />**` — show diff between two page versions (inline diff view)

**`<ProviderBadge provider="anthropic" model="claude-sonnet-4-5" />`** — model attribution on every page

**`<MonorepoPackageMap packages={[...]} />`** — for monorepos: visual package dependency map

**`<GitHistoryPanel fileMetadata={...} />`** — collapsible right panel section showing
  primary owner, commit stats, recent changes, co-change partners

**`<DeadCodeTable findings={[...]} />`** — sortable table with confidence dots,
  safe-to-delete indicator, resolve button

**`<OwnershipTreemap data={...} />`** — D3 treemap colored by owner, sized by LOC

**`<HotspotList hotspots={[...]} />`** — list with inline churn + complexity bar charts

---

## GitHub Action (`integrations/github-action/`)

**`action.yml`**:
```yaml
name: repowise
description: Auto-generate and maintain codebase documentation
inputs:
  provider:
    required: true
  model:
    required: false
    default: "claude-sonnet-4-5"
  api-key:
    required: false
    description: "API key (use secrets.ANTHROPIC_API_KEY etc.)"
  repowise-url:
    required: false
    description: "Self-hosted repowise server URL (optional, for remote storage)"
  mode:
    default: "auto"
    description: "auto|full|incremental — auto detects based on trigger"
  cascade-budget:
    default: "30"
  commit-wiki:
    default: "true"
    description: "Whether to commit updated .repowise/ back to the repo"
  pr-comment:
    default: "true"
    description: "Whether to comment on PRs with affected pages"
```

**Trigger behavior:**
- `push` to default branch → `incremental` update → commit `.repowise/` back
- `pull_request` → `dry-run` → comment listing affected pages + cost estimate
- `release` / `workflow_dispatch` with `mode: full` → full regeneration
- `schedule` (if configured) → `incremental` or background staleness run

**Commit message**: `docs(wiki): update documentation [skip ci]`

---

## Docker (`docker/`)

Multi-stage Dockerfile:
- Stage 1: `python:3.11-slim` + uv + Python packages
- Stage 2: `node:20-slim` + build Next.js static output
- Stage 3: Final image with Python runtime + pre-built Next.js export

`docker-compose.yml`:
```yaml
services:
  repowise:
    build: .
    ports:
      - "7337:7337"   # Web UI + REST API
      - "7338:7338"   # MCP SSE server
    volumes:
      - ./repos:/repos           # repos to document
      - repowise_data:/data      # SQLite + LanceDB
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DATABASE_URL=sqlite+aiosqlite:////data/repowise.db
      - LANCEDB_PATH=/data/lancedb
      - GRAPH_PATH=/data/graphs
      - REPOWISE_API_KEY=${REPOWISE_API_KEY:-}  # optional auth
    restart: unless-stopped
    
  redis:
    image: redis:7-alpine
    profiles: ["multi-worker"]   # only starts with --profile multi-worker
    
volumes:
  repowise_data:
```

One-command self-host: `docker compose up -d`. Redis is optional (multi-worker profile only).

---

## Implementation Constraints

### Concurrency and Performance
- Use `asyncio.Semaphore(concurrent_jobs)` to cap parallel LLM calls
- All DB operations must use async SQLAlchemy — never block the event loop
- Stream LLM responses for the web UI "regenerate" button (SSE → token-by-token)
- For `repowise init` with `--batch`: use provider batch API where supported.
  For others, fall back to concurrent streaming with the semaphore.
- Cache prompts: if the same `(model, system_prompt, user_prompt)` SHA256 has been
  seen before, return the cached response from `.repowise/cache/`. TTL: never expire
  by default (docs don't change unless code changes). `--no-cache` to bypass.

### Reliability
- Every LLM call: 3 retries with exponential backoff (1s, 2s, 4s), 120s timeout
- Failed page generation: log error, mark page as `generation_failed`, continue with rest
- Failed webhook: store in `WebhookEvent` with `status: failed`, retry via polling
- Rate limit errors (429): back off and retry via `RateLimiter.on_rate_limit_error()`
- Never abort an entire job for a single page failure

### Observability
- Structured JSON logging via `structlog`. Every log line includes: `job_id`, `repo_id`,
  `page_id`, `provider`, `model` as context fields.
- Token tracking: log `input_tokens`, `output_tokens`, `cached_tokens` per call.
  Aggregate in `GenerationJob`. Show in CLI output and web dashboard.
- Cost tracking: compute from token counts + known prices. Show in dashboard.
- `/metrics` endpoint: Prometheus-compatible. Exposes: job counts by status, total tokens,
  stale page count, cache hit rate, average generation time per page.

### Open Source Standards
- License: **Apache 2.0**
- `CONTRIBUTING.md`: full setup guide, how to add a provider (with example), how to add
  a language parser, conventional commits specification, PR checklist
- `CHANGELOG.md`: keep updated; use `git-cliff` to auto-generate from conventional commits
- Dependencies: `>=X.Y,<X+1` ranges in `pyproject.toml`. Lock via `uv.lock`.
- Pre-commit: `ruff` (lint + format), `mypy --strict`, `pytest -x -q` (fast tests only)
- GitHub Actions CI: test matrix on Python 3.11, 3.12, 3.13 + Node 20

### Testing
- `tests/unit/` — parser, graph builder, differ, prompt rendering, confidence score logic
- `tests/integration/` — full ingestion pipeline on `tests/fixtures/sample_repo/`
  (a small multi-language repo included in the project, ~30 files, Python + TS)
- `tests/e2e/` — full `repowise init` on sample repo, verify page count and structure.
  Run repowise on itself: `repowise init .` and assert pages > 50.
- `tests/providers/` — all providers tested against a `MockProvider` that returns fixture
  responses. Never make real API calls in tests.
- `pytest-asyncio`, `pytest-snapshot` (for generated page content regression),
  `respx` (mock httpx), `time-machine` (freeze time for cron tests)

---

## Build Order

Follow this sequence strictly. Each phase must be functional before starting the next.

```
Phase 1: Foundation
  1. Monorepo structure — uv workspace, pyproject.toml files, package scaffolding
  2. Provider abstraction — all 4 providers + rate limiter + registry
     → Test: MockProvider returns fixture responses correctly
  
Phase 2: Ingestion Pipeline (no LLM)
  3. File traverser — gitignore, blocklist, special file detection, monorepo detection
  4. AST parser — tree-sitter for all languages + special handlers (OpenAPI, proto, etc.)
  5. Graph builder — NetworkX + SQLite backend + PageRank + SCC + clustering
  6. Change detector — git diff + symbol rename detection + affected pages computation
     → Test: run ingestion on sample_repo, assert correct symbol counts and graph structure

Phase 3: Generation Engine
  7. Prompt templates — all Jinja2 templates
  8. Context assembler — builds optimized context per page type
  9. Page generator — hierarchical generation in correct order + chunking strategy
  10. Resumable job system — checkpoint + recovery
     → Test: mock LLM, generate all pages for sample_repo, assert structure

Phase 4: Persistence
  11. Database models + Alembic migrations
  12. CRUD layer (async SQLAlchemy)
  13. Vector store integration — LanceDB (SQLite mode) or pgvector (PostgreSQL mode) — indexing + search + RAG query
     → Test: store/retrieve pages, search returns expected results

Phase 5: CLI
  14. repowise init — full end-to-end with cost estimate, progress, confirmation
  15. repowise update — incremental with cascade budget
  16. repowise watch — filesystem watcher with debounce
  17. repowise doctor — health check command
     → Test: init + update on sample_repo produces correct output

Phase 5.5: Git Intelligence + Dead Code Detection (detour before Phase 6)
  15.5. GitMetadata + DeadCodeFinding ORM models + Alembic migration
  15.6. GitIndexer — mine git history into git_metadata table
  15.7. Init/Update pipeline integration (Steps 3.5 + 3.6)
  15.8. Template enrichment (file_page.j2, module_page.j2, repo_overview.j2)
  15.9. Generation ordering (hotspot priority) + depth auto-upgrade
  15.10. Confidence decay git modifiers
  15.11. Co-change edges in graph + staleness propagation
  15.12. DeadCodeAnalyzer — graph + SQL analysis
  15.13. repowise dead-code CLI command
  15.14. Config: git + dead_code sections
  15.15. Tests: git_indexer (7), dead_code (12), confidence_with_git (4), integration (2)
     → Test: GitIndexer indexes sample_repo; DeadCodeAnalyzer detects fixture dead code

Phase 6: Server
  18. FastAPI app — all routers, auth, health/metrics endpoints
      (includes git-metadata, hotspots, ownership, co-changes, dead-code endpoints)
  19. Webhook handlers — GitHub + GitLab, WebhookEvent storage, polling fallback
  20. SSE job progress stream
  21. APScheduler — background regen + polling fallback jobs
     → Test: webhook triggers job, polling catches missed events

Phase 7: MCP Server
  22. MCP server — all 13 tools (8 original + 5 git/dead-code), both stdio and SSE transports
  23. Auto-generated MCP config + setup docs for Claude Code, Cursor, Cline
     → Test: connect mock MCP client, call all tools, verify responses

Phase 8: Web UI
  24. Next.js scaffold — routing, layout, design system setup
  25. All pages: dashboard, repo overview, wiki page, search, graph, symbols, coverage
  26. MDX renderer with symbol hover cards, Mermaid, Shiki
  27. D3 dependency graph visualization
  28. SSE-connected generation progress component
     → Test: render sample_repo wiki, verify all pages load, diagram renders

Phase 9: Integrations
  29. GitHub Action — action.yml + Dockerfile + PR comment + commit-back logic
  30. Docker compose — production config with optional Redis profile

Phase 10: Quality
  31. E2E tests — full init on sample_repo + repowise on itself
  32. README — quickstart, comparison table, MCP setup for all clients, docker one-liner
  33. CONTRIBUTING.md — how to add a provider, how to add a language
  34. Dogfood: run repowise on the repowise repo itself
```

---

## README Requirements

The README must include, in order:

1. **One-sentence description** — what it is
2. **Comparison table** with columns: repowise / Google Code Wiki / Mintlify / Swimlane
   Rows: self-hostable, model-agnostic, MCP server, incremental updates, offline mode,
   free/open source, monorepo support, PR preview
3. **30-second quickstart** — `pip install repowise` + `repowise init` + screenshot
4. **MCP setup** — copy-paste configs for Claude Code, Cursor, Cline
5. **All providers** — config examples for Anthropic, OpenAI, Ollama, LiteLLM
6. **Docker one-liner** — `docker compose up`
7. **GitHub Action** — minimal workflow YAML
8. **Architecture overview** — generated by repowise itself (dogfooding screenshot)
9. **"Why repowise?"** — hierarchical understanding, incremental updates, confidence scores,
   MCP-native, works offline with Ollama, committed docs = versioned docs

---

## Start Here

Begin with Phase 1, Step 1: monorepo structure.
Create the full directory tree, `pyproject.toml` files with correct dependencies,
`package.json` for the web package, and pre-commit config.

Then Phase 1, Step 2: provider abstraction. Build and test all providers and the rate
limiter before touching the ingestion code. This foundation is used everywhere.

Make decisions and document them in comments. Ask only if a requirement is genuinely
ambiguous. When in doubt, implement the simpler version and note it as a TODO.