# Repowise Architecture — Complete Guide

This document covers the entire Repowise architecture beyond the graph layer: ingestion, generation, persistence, providers, server, and MCP tools. For graph algorithms specifically, see `graph-algorithms-guide.md`.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Ingestion Pipeline](#2-ingestion-pipeline)
3. [Generation Pipeline](#3-generation-pipeline)
4. [Persistence Layer (Three Stores)](#4-persistence-layer-three-stores)
5. [LLM Provider Abstraction](#5-llm-provider-abstraction)
6. [Rate Limiter](#6-rate-limiter)
7. [Server Layer](#7-server-layer)
8. [MCP Tools](#8-mcp-tools)
9. [Frontend (Web UI)](#9-frontend-web-ui)

---

## 1. The Big Picture

Repowise takes a codebase and produces a living wiki — AI-generated documentation that stays current as the code changes.

```
Your Codebase
     │
     ▼
┌─────────────────────────────────────────────┐
│              INGESTION                       │
│  FileTraverser → ASTParser → GraphBuilder   │
│  GitIndexer → ChangeDetector                │
│  DeadCodeAnalyzer                           │
└──────────────────┬──────────────────────────┘
                   │ ParsedFiles, Graph, GitMetadata
                   ▼
┌─────────────────────────────────────────────┐
│              GENERATION                      │
│  ContextAssembler → Jinja2 Templates        │
│  PageGenerator → LLM Provider               │
│  JobSystem (checkpoints, resumability)       │
└──────────────────┬──────────────────────────┘
                   │ GeneratedPages
                   ▼
┌─────────────────────────────────────────────┐
│              PERSISTENCE                     │
│  SQL Store ─── Full-Text Search (FTS5)      │
│  Vector Store (LanceDB / pgvector)          │
│  Graph Store (NetworkX → SQL tables)        │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
     CLI        Server      MCP Server
  (repowise)  (FastAPI)   (for AI editors)
                  │
                  ▼
              Web UI
           (Next.js 15)
```

**Why this architecture?** Each layer has a single responsibility and a clear boundary:

- **Ingestion** knows about code but nothing about LLMs or databases
- **Generation** knows about LLMs but not about HTTP or the web UI
- **Persistence** knows about storage but not about how data was created
- **Server** knows about HTTP but delegates all logic to core

This means you can swap the LLM provider, change the database, or replace the frontend without touching the core logic.

---

## 2. Ingestion Pipeline

The ingestion pipeline turns raw source code into structured data. It has five components that run in sequence.

### 2.1 FileTraverser — "What files exist?"

**The problem:** A repository contains thousands of files, but most are irrelevant — `node_modules/`, compiled binaries, lock files, generated code. You need to find the files worth documenting.

**How it works:**

FileTraverser walks the directory tree and applies three filter layers:

```
All files in repo
     │
     ▼
  Layer 1: Blocked directories
  (.git, node_modules, __pycache__, .venv, dist, build, ...)
     │
     ▼
  Layer 2: .gitignore + .repowiseIgnore
  (using pathspec library for glob matching)
     │
     ▼
  Layer 3: File-level filters
  - Blocked extensions (.pyc, .so, .exe, .wasm)
  - Blocked patterns (*.min.js, package-lock.json)
  - Generated file detection (checks first 512 bytes for markers like "DO NOT EDIT")
  - Size limit (default 500 KB)
     │
     ▼
  Surviving files → FileInfo objects
```

**Language detection** uses a multi-step fallback:

1. Check special filenames first (`Dockerfile` → dockerfile, `Makefile` → makefile)
2. Check extension map (`.py` → python, `.tsx` → typescript, etc.)
3. If extension unknown: check for null bytes (binary → skip)
4. If text: read first 200 chars for shebang (`#!/usr/bin/env python` → python)

**File classification** adds boolean flags to each FileInfo:

| Flag | How it's detected | Example |
|------|------------------|---------|
| `is_test` | `test_` prefix, `_test` suffix, or inside `/tests/` directory | `test_auth.py`, `auth.spec.ts` |
| `is_entry_point` | Filename in known set: `main.py`, `index.ts`, `app.py`, `server.go` | `src/main.py` |
| `is_config` | Language is yaml, toml, json, dockerfile, or makefile | `docker-compose.yml` |
| `is_api_contract` | Language is proto/graphql, or name contains "openapi"/"swagger" | `api.proto`, `openapi.yaml` |

**Why this matters:** These flags drive downstream decisions. Entry points are never flagged as dead code. Test files don't get full wiki pages. Config files skip AST parsing.

### 2.2 ASTParser — "What's inside each file?"

**The problem:** You need to extract functions, classes, imports, and exports from source code in 9+ languages without writing a parser per language.

**The solution: tree-sitter queries.**

Tree-sitter is a parser generator that produces concrete syntax trees for any language. Repowise uses it with a **unified query architecture**:

```
Source code (Python)        tree-sitter grammar        S-expression query (.scm)
──────────────────    +    ─────────────────────   +   ──────────────────────────
def calculate(x):          python grammar              (function_definition
    return x * 2                                          name: (identifier) @symbol.name
                                                          parameters: (parameters) @symbol.params
                                                       ) @symbol.def
```

Every language uses the same capture names:
- `@symbol.def` — the full definition node (provides line numbers)
- `@symbol.name` — the name identifier
- `@symbol.params` — parameter list
- `@symbol.modifiers` — decorators, visibility keywords
- `@import.statement` — full import node
- `@import.module` — the module path being imported

**Adding a new language** requires only two things:
1. Write one `.scm` query file in `packages/core/queries/`
2. Add one entry to the `LANGUAGE_CONFIGS` dict

No new Python classes, no language-specific if/elif chains.

**What the parser extracts:**

For each file, the parser produces a `ParsedFile` containing:

```python
ParsedFile
├── symbols: [Symbol]        # functions, classes, methods, interfaces, enums
│   ├── name: "calculate"
│   ├── kind: "function"
│   ├── signature: "def calculate(x: int) -> int"
│   ├── visibility: "public"    # language-specific rules
│   ├── start_line, end_line    # for source extraction
│   ├── docstring: "..."
│   ├── decorators: ["@cache"]
│   ├── is_async: True/False
│   └── parent_name: "Calculator"  # if it's a method
├── imports: [Import]
│   ├── module_path: "utils.helpers"
│   ├── imported_names: ["calculate", "Config"]
│   └── is_relative: True/False
├── exports: ["calculate", "Config"]
└── content_hash: SHA-256 of raw bytes
```

**Visibility detection** is language-specific because every language has different conventions:

| Language | Rule | Example |
|----------|------|---------|
| Python | Underscore prefix → private, dunder → public | `_helper` is private, `__init__` is public |
| Go | Uppercase first letter → public | `Calculate` is public, `calculate` is private |
| TypeScript | `private`/`protected` keyword in modifiers | `private _cache` is private |
| Rust | `pub` keyword in modifiers | `pub fn new()` is public |
| Java | `private`/`protected`/`public` keyword | `public void run()` is public |
| C/C++ | All public by default | Everything is public |

**Parent detection** (for methods) also varies:

| Strategy | Languages | How it works |
|----------|-----------|-------------|
| Nesting | Python, TypeScript, Java | Walk up the AST to find enclosing class node |
| Receiver | Go | Extract type from `func (r *Router) Handle()` receiver |
| Impl block | Rust | Find enclosing `impl Router { }` ancestor |

**Special handlers** exist for languages without tree-sitter support:

- **OpenAPI** (YAML/JSON) — extracts HTTP operations and schema definitions
- **Dockerfile** — extracts FROM, ENTRYPOINT, CMD, EXPOSE as symbols
- **Makefile** — extracts targets as functions, `include` as imports

### 2.3 GraphBuilder — "How do files depend on each other?"

See `graph-algorithms-guide.md` for full coverage. The key point: GraphBuilder takes ParsedFiles and resolves their imports into actual file paths, creating a directed dependency graph. The graph is **two-tier**: file nodes for module-level relationships and symbol nodes for fine-grained call relationships, all in the same `networkx.DiGraph`.

**Named binding resolution** (`NamedBinding` in `ingestion/models.py`) happens during `GraphBuilder.build()`. For each import in a `ParsedFile`, the parser's `_extract_import_bindings()` produces `NamedBinding` objects tracking `local_name`, `exported_name`, `source_file`, and `is_module_alias`. These are used to populate `Import.resolved_file` and to build the alias lookup tables that `CallResolver` relies on. Barrel files (`__init__.py`, `index.ts`) are followed one hop during resolution to handle re-exports.

**Call resolution** runs after import graph construction. `CallResolver` (`ingestion/call_resolver.py`) extracts call sites from the tree-sitter parse tree using `_extract_calls()` (per-language `.scm` captures for all 7 languages), then resolves each `CallSite` in three tiers:

| Tier | Confidence | How it resolves |
|------|-----------|-----------------|
| Same-file | 0.95 | Target defined in the same `ParsedFile` |
| Import-scoped | 0.85–0.93 | Target matched via `NamedBinding` from the file's imports; higher confidence for direct named imports vs namespace aliases |
| Global unique | 0.50 | Target name appears exactly once across the whole repo |

Resolved call sites become `CALLS` edges in the graph. Unresolvable call sites are discarded. Each `CALLS` edge stores the confidence score on the edge data. `DEFINES` and `HAS_METHOD` edges are added during `add_file()` when symbol nodes are created.

`file_subgraph()` returns a filtered view of the `DiGraph` containing only `file` and `package` nodes. All file-level metrics (PageRank, betweenness, SCCs, Louvain, in-degree dead code) run on this subgraph to prevent symbol-node cardinality from distorting centrality scores.

After building the static import graph, GraphBuilder also adds **framework-aware synthetic edges** via `add_framework_edges()`. This detects common framework patterns and creates edges for dependencies that static import resolution misses:

- **pytest**: `conftest.py` → test files in the same/child directories
- **Django**: `admin.py` → `models.py`, `urls.py` → `views.py`, `forms.py` → `models.py`, `serializers.py` → `models.py` (same directory)
- **FastAPI**: files with `include_router()` → the router modules they include
- **Flask**: files with `register_blueprint()` → the blueprint modules they register

Framework detection uses the existing `detect_tech_stack()` function (which scans `pyproject.toml`, `package.json`, etc.) to know which frameworks are present.

**Import resolution** is the tricky part — each language has different rules:

```
Python relative:   "from .sibling import x"  → resolve dots + walk up directories
Python absolute:   "from pkg.calc import x"  → try pkg/calc.py, pkg/calc/__init__.py
TypeScript:        "./utils"                  → try utils.ts, utils.tsx, utils/index.ts
Go:                "github.com/foo/bar"       → match last segment "bar" by stem
Generic fallback:  stem matching              → "calculator" matches calculator.py
```

Alias forms (`import X as Y`, `from X import Y as Z`, Go aliased imports, Rust `use_as_clause`, TS namespace imports) are handled by `_extract_import_bindings()` and stored in separate `module_aliases` tracking so `CallResolver` can look up both the alias and the original name.

### 2.4 GitIndexer — "What's the history of each file?"

**The problem:** Code structure (imports, symbols) is only half the story. You also need to know: who owns this file? How often does it change? Is it a hotspot? What files change together?

**What it extracts per file:**

```
Timeline
├── commit_count_total, commit_count_90d, commit_count_30d
├── first_commit_at, last_commit_at, age_days
└── merge_commit_count_90d

Ownership
├── primary_owner (by git blame — who wrote the most lines)
├── recent_owner (most commits in last 90 days)
├── contributor_count
├── bus_factor (minimum contributors for 80% of commits)
└── top_authors_json (top 5 by commit count)

Churn
├── lines_added_90d, lines_deleted_90d
├── avg_commit_size
├── churn_percentile (rank among all files, 0.0-1.0)
├── is_hotspot (churn_percentile >= 0.75 AND active in 90d)
└── is_stable (> 10 total commits BUT 0 in 90d)

Significant Commits
└── Top 10 commits that matter (filtered)
    - Skip: merge commits, bot authors, < 12 chars
    - Skip: "Bump version", "chore:", "ci:", "style:" (unless decision signal)
    - Decision signals: "migrate", "refactor", "deprecate", "rewrite", etc.
```

**Bus factor** deserves explanation. It answers: "if people leave, when does knowledge become dangerously concentrated?"

```
Example: file with 100 commits
  Alice:  60 commits (60%)
  Bob:    25 commits (25%)
  Carol:  10 commits (10%)
  Dave:    5 commits  (5%)

Walk from top: Alice alone = 60%. Not 80%. Add Bob: 85%. >= 80%.
Bus factor = 2.

If both Alice AND Bob leave, knowledge drops below 20%.
Bus factor of 1 = single point of failure.
```

**Co-change detection** finds files that change together:

```
Commit 1: [auth.py, config.py, tests/test_auth.py]
Commit 2: [auth.py, config.py]
Commit 3: [auth.py, config.py, middleware.py]

auth.py ↔ config.py: co-changed 3 times → strong signal
auth.py ↔ middleware.py: co-changed 1 time → weak signal
```

Recent co-changes count more than old ones. The score uses exponential decay:

```
score = Σ exp(-age_days / 180)
```

A co-change yesterday contributes ~1.0. A co-change 6 months ago contributes ~0.37. A co-change a year ago contributes ~0.13. This ensures the signal reflects current coupling patterns, not ancient history.

**Why a single git log call?** A naive approach would run `git log` per-file (10,000 files → 10,000 processes). Instead, GitIndexer runs one `git log --name-only` for the whole repo and parses all commits in a single pass. O(1) git processes instead of O(N).

### 2.5 ChangeDetector — "What changed since last run?"

**The problem:** After the initial indexing, you don't want to re-generate documentation for every file. You only want to update what changed.

**How it works:**

```
Step 1: git diff HEAD~1..HEAD → list of changed files

Step 2: For each changed file, parse old and new versions

Step 3: Detect symbol renames
  Old file has: calculate(), Config
  New file has: compute(), Config
  "calculate" removed, "compute" added, same kind (function),
  similar line position → likely rename (confidence scored via Levenshtein)

Step 4: Cascade through the graph
  auth.py changed
  → Who imports auth.py? → [main.py, middleware.py]  (1-hop, full regen)
  → Who imports those? → [app.py, cli.py]            (2-hop, decay only)
  → Co-change partners? → [config.py]                 (co-change, decay)

Step 5: Budget-sort regeneration list
  Sort by PageRank (most important first)
  Cap at cascade_budget (adaptive: 10 for 1 file, 30 for 2-5, up to 50 max)
  Remaining pages → decay their confidence score without regenerating
```

**Why cascade?** If `utils.py` changes, the documentation for `service.py` (which imports `utils.py`) is now potentially wrong — it might reference old function names or behavior. The cascade ensures dependent documentation stays accurate.

**Why budget?** Without a budget, changing a widely-imported file like `config.py` would trigger regeneration of hundreds of pages. The budget ensures a bounded cost per update, with PageRank-based prioritization so the most important pages get refreshed first.

---

## 3. Generation Pipeline

The generation pipeline takes ingested data and produces wiki pages using LLMs.

### 3.1 Page Types and Levels

Repowise generates **10 types of pages** in **8 ordered levels**:

```
Level 0: api_contract      ← API definitions (OpenAPI, Proto, GraphQL)
Level 1: symbol_spotlight   ← Individual important symbols
Level 2: file_page          ← Individual code files
Level 3: scc_page           ← Circular dependency documentation
Level 4: module_page        ← Directory-level summaries
Level 5: cross_package      ← Inter-package boundaries (monorepo only)
Level 6: repo_overview      ← Repo-wide summary
         architecture_diagram ← Mermaid dependency diagram
Level 7: infra_page         ← Dockerfiles, Makefiles, Terraform
         diff_summary       ← Change summaries
```

**Why levels?** Later levels depend on earlier levels. A module_page (level 4) references file_pages (level 2). The repo_overview (level 6) references module_pages (level 4). By generating in order, later pages can include summaries of earlier pages as context for the LLM.

**Within each level**, pages generate concurrently (controlled by a semaphore, default concurrency = 5).

### 3.2 Page Budget — "How many pages should we generate?"

Not every file deserves its own wiki page. The budget controls cost:

```python
budget = max(50, int(total_files * 0.10))   # at most 10% of files, minimum 50
```

The budget is allocated across page types:

```
Fixed overhead (always generated):
  + all API contract files
  + all SCC cycles (size > 1)
  + all modules (top-level directories)
  + repo_overview + architecture_diagram

Remaining budget split between:
  file_pages:       top 10% files by PageRank (capped by remaining)
  symbol_spotlights: top 10% public symbols by PageRank (capped by remaining)
```

**Example for a 500-file repo:**

```
Budget = max(50, 500 × 0.10) = 50 pages

Fixed overhead:
  3 API contracts + 2 SCC cycles + 8 modules + 2 overview = 15

Remaining = 50 - 15 = 35
  file_pages: top 10% of 500 = 50, capped at 35 → 25 file pages
  symbol_spotlights: top 10% of symbols, capped at 10
```

### 3.3 Significant File Selection — "Which files get their own page?"

A file gets a Level 2 page if it passes `_is_significant_file()`:

```
Is it a package __init__.py with symbols?        → YES (module interface)
Is it an entry point?                            → YES
Is its PageRank >= threshold (top percentile)?   → YES
Is its betweenness centrality > 0?               → YES (bridge file)
Does it have >= 1 symbol?                        → Required (unless entry point or high PageRank)

Everything else → included only in module-level summaries
```

**Why betweenness overrides PageRank here:** A bridge file might not be widely imported (low PageRank), but it's the only connection between two subsystems. Documenting it helps developers understand the coupling point.

### 3.4 ContextAssembler — "What context does the LLM need?"

Each page type has a corresponding context assembler method that builds a dataclass of everything the LLM needs to know. The context is then rendered into a Jinja2 template to form the user prompt.

**File page context example:**

```
FilePageContext
├── file_path, language
├── symbols (public first, then private)
│   └── Each with: name, kind, signature, docstring, visibility
├── imports, exports
├── dependencies (what I import) + dependents (who imports me)
├── file_source_snippet (raw code, trimmed to token budget)
├── pagerank_score, betweenness_score, community_id
├── git_metadata (ownership, churn, significant commits)
├── co_change_pages (files that change with this one)
├── dead_code_findings (unused symbols in this file)
├── depth: "minimal" | "standard" | "thorough"
├── dependency_summaries (summaries of already-generated pages)
└── rag_context (related pages from vector search)
```

**Token budgeting** ensures context fits the LLM window:

```
Token estimate = len(text) // 4   (rough approximation, no tiktoken dependency)

Budget = 48,000 tokens per page

Allocation priority:
1. File path + language + symbol signatures     (always included)
2. Symbol docstrings                            (if budget allows)
3. Import list (capped at 30)                   (if budget allows)
4. Source code snippet                          (remainder of budget)
   → If source > 40% of budget → use structural summary instead
```

### 3.5 Generation Depth — "How detailed should docs be?"

Each file gets a depth level that tells the LLM how much detail to produce:

```
"thorough" if:
  - File is a hotspot (high churn + high commits)
  - OR has > 100 total commits AND > 10 in last 90 days
  - OR has >= 8 significant commits
  - OR has co-change partners

"minimal" if:
  - File is stable (many total commits but none recently)
  - AND PageRank < 0.3
  - AND commit count < 5

"standard" otherwise
```

**Why?** Hotspot files change often and are touched by many developers — they need comprehensive documentation explaining edge cases, rationale, and usage patterns. Stable low-importance files just need a brief API summary. This saves LLM tokens and keeps the wiki focused.

### 3.6 PageGenerator — "How are pages actually generated?"

The `generate_all()` method orchestrates everything:

```
async def generate_all():

    1. Compute graph metrics (PageRank, betweenness, communities, SCCs)
    2. Compute page budget and thresholds
    3. For each level 0-7:
        a. Build list of (page_id, coroutine) pairs
        b. Skip pages already completed (for resumability)
        c. Run all coroutines concurrently (semaphore-limited)
        d. For each completed page:
           - Embed in vector store (for RAG context in later levels)
           - Extract summary (for dependency context in later levels)
           - Record in checkpoint (for resumability)
    4. Return all generated pages
```

**Each page generation follows the same pattern:**

```
1. Assemble context    → ContextAssembler.assemble_*()  → dataclass
2. Render template     → Jinja2 template                → user_prompt string
3. Check cache         → SHA256(model + type + prompt)   → hit = skip LLM
4. Call LLM            → provider.generate(system, user) → markdown response
5. Wrap result         → GeneratedPage dataclass
6. Validate output     → Cross-check backtick refs against AST symbols
```

**Step 6 (LLM output validation)** extracts all backtick-quoted names from the generated markdown (e.g., `` `calculate` ``, `` `Config` ``) and compares them against actual symbol names, exports, and imports from the ParsedFile. References that don't match any known name are logged as hallucination warnings and stored in `page.metadata["hallucination_warnings"]`. Common keywords and builtins are excluded from checking.

**After `generate_all()` completes**, the `update` command prints a **Generation Report** — a rich table showing pages by type, token counts (input/output/cached), estimated cost, elapsed time, stale page count, and hallucination warning count.

**System prompts are constant per page type.** This is intentional — Anthropic's prompt caching gives ~10% cost reduction when the system prompt is identical across requests. Since all file_pages share the same system prompt, the cache hit rate is high.

### 3.7 Job System — "What if generation crashes halfway?"

Generating documentation for a large repo might take hours and involve hundreds of LLM calls. If the process crashes at page 150 of 300, you don't want to start over.

**How it works:**

```
Checkpoint file: .repowise/jobs/{job_id}.json

{
  "status": "running",
  "total_pages": 300,
  "completed_pages": 150,
  "completed_page_ids": ["file_page:src/auth.py", "file_page:src/api.py", ...],
  "failed_page_ids": ["file_page:src/broken.py"],
  "current_level": 2
}
```

State machine:

```
pending → running → completed
            ↓
          failed
            ↓
          paused → running (resume)
```

On resume, `generate_all()` reads `completed_page_ids` and skips those pages. Generation continues from where it left off.

**Why checkpoint per page (not per level)?** A level might have 200 pages. Crashing at page 199 and restarting from page 1 of that level wastes 198 LLM calls. Per-page checkpointing means at most 1 wasted call on crash.

### 3.8 Freshness and Confidence Decay

Pages go stale as code changes. Repowise tracks this:

```
confidence = 1.0 at generation time
           → decays linearly to 0.0 over expiry_threshold_days (default 30)

freshness_status:
  "fresh"   → source hash matches AND age < staleness_threshold (7 days)
  "stale"   → source hash changed OR age >= 7 days (but < 30)
  "expired" → age >= 30 days (always regenerate)
```

**Source hash = SHA256(user_prompt).** If the code changes, the assembled context changes, the rendered template changes, and the hash changes. This detects staleness even without checking git — if the inputs to the LLM would be different, the page is stale.

### 3.9 RAG Integration — "Can pages reference each other?"

When generating a file_page, the system queries the vector store for related pages:

```
Query terms = file's exports or top 3 public symbol names
Results = top 3 semantically similar pages (excluding self)
Injected into ctx.rag_context as snippets
```

**Why?** If `service.py` imports `repository.py`, and we already generated a page for `repository.py`, the LLM documenting `service.py` can reference the repository documentation for accurate cross-references.

### 3.10 Editor File Generation (CLAUDE.md)

Repowise generates `CLAUDE.md` files for AI coding assistants. These are NOT LLM-generated — they're assembled from structured data:

```
CLAUDE.md contains:
  - Architecture summary (from repo_overview page)
  - Key modules table (sorted by PageRank)
  - Entry points list
  - Tech stack (categorized by type)
  - Hotspot files table (high churn)
  - Repowise MCP tools (recommended workflow)
  - Build commands (from project config)
```

**Marker-based merge** preserves user customizations:

```markdown
# CLAUDE.md
<!-- Your custom instructions here — Repowise never touches this -->

Your hand-written notes, conventions, team rules...

<!-- REPOWISE:START — Do not edit below this line. Auto-generated by Repowise. -->
[Auto-generated content updated on each run]
<!-- REPOWISE:END -->
```

On update: only content between markers is replaced. Everything above/outside is preserved.

---

## 4. Persistence Layer (Three Stores)

Repowise uses three independent storage systems because each answers a fundamentally different type of question.

### 4.1 SQL Store — "What exists?"

**Tech:** SQLAlchemy 2.0 + SQLite (default) or PostgreSQL

**What it stores:**

| Table | Contents | Key |
|-------|----------|-----|
| `repositories` | Registered repos (name, path, branch, sync state) | `local_path` |
| `wiki_pages` | Generated documentation | `page_id = "{type}:{path}"` |
| `wiki_page_versions` | Historical snapshots of pages | auto-incremented |
| `graph_nodes` | File nodes with computed metrics | `(repo_id, node_id)` |
| `graph_edges` | Import dependencies between nodes | `(repo_id, source, target)` |
| `wiki_symbols` | Functions, classes, methods | `(repo_id, symbol_id)` |
| `git_metadata` | Per-file ownership, churn, co-changes | `(repo_id, file_path)` |
| `generation_jobs` | Job status and progress | UUID |
| `dead_code_findings` | Unreachable files, unused exports | UUID |
| `decision_records` | Architectural Decision Records (ADRs) | composite natural key |

**Page versioning:**

```
First upsert  → insert page, version=1, no PageVersion created
Second upsert → archive old content as PageVersion, increment to version=2
Third upsert  → archive again, increment to version=3
```

This means: version 1 is the current content (in `wiki_pages`), older versions are in `wiki_page_versions`. You get history without doubling storage on the first write.

**Why natural keys?** Page IDs are `"{page_type}:{target_path}"` (e.g., `"file_page:src/auth.py"`). This means you can upsert a page knowing only its type and path — no need to query the database for an auto-generated ID first. Same for repositories (keyed by `local_path`) and decisions (keyed by title + source + evidence_file).

### 4.2 Full-Text Search — "Which pages mention X?"

**Two backends** (auto-detected from database dialect):

| Backend | Technology | How it works |
|---------|-----------|-------------|
| SQLite | FTS5 virtual table | `page_fts(page_id, title, content)` with BM25-like ranking |
| PostgreSQL | GIN index + tsvector | `to_tsvector('english', title || content)` with ts_rank |

**Query processing:**
1. Strip English stop words ("the", "is", "at", etc.)
2. Join remaining terms with OR (broad recall)
3. Support prefix matching: `"pay*"` matches "payment", "payload"

**Why a separate search index?** SQL LIKE queries don't rank results by relevance and are slow on large text columns. FTS5/tsvector indexes are optimized for text search and return results ranked by relevance.

### 4.3 Vector Store — "What's semantically similar to X?"

**Three implementations** (chosen at startup based on config):

| Implementation | Storage | Use case |
|---------------|---------|----------|
| InMemoryVectorStore | Python dict | Tests, tiny repos |
| LanceDBVectorStore | Local files (`.repowise/lancedb/`) | Default for production |
| PgVectorStore | PostgreSQL pgvector extension | When using PostgreSQL |

**How it works:**

```
1. Text → Embedder → float vector (e.g., 1536 dimensions)
2. Vector stored alongside page_id and metadata
3. Search: query text → embed → find nearest vectors by cosine similarity
4. Return top-k most similar pages
```

**All vectors are L2-normalized** (unit length). This means cosine similarity = dot product, which is cheaper to compute.

**Embedder options:**
- MockEmbedder: SHA-256 → 8-dim vector (deterministic, no API calls)
- OpenAI: `text-embedding-3-small` (1536 dims)
- Gemini: configurable dimensions

**Why a vector store alongside FTS?** FTS matches keywords — it finds "authentication" when you search "authentication." Vector search matches meaning — it finds "login flow" and "credential validation" when you search "authentication" because those concepts are semantically close in embedding space. Both are useful; Repowise tries vector search first and falls back to FTS.

### How the three stores work together

```
Page Generation:
  LLM produces markdown
  → SQL: upsert_page(page_id, content, metadata)
  → FTS: index(page_id, title, content)
  → Vector: embed_and_upsert(page_id, content, metadata)

Search query "how does auth work?":
  → Vector store: top 5 semantically similar pages
  → FTS fallback: keyword match if vector search fails
  → SQL: fetch full page content for results

Graph query:
  → SQL: read GraphNode/GraphEdge rows
  → Reconstruct NetworkX graph for algorithms
  → Return computed paths, neighborhoods, metrics
```

---

## 5. LLM Provider Abstraction

Repowise supports 6 LLM providers behind a single interface.

### The interface

```python
class BaseProvider:
    async def generate(system_prompt, user_prompt, max_tokens, temperature) → GeneratedResponse
    provider_name → str   # "anthropic", "openai", etc.
    model_name → str      # "claude-sonnet-4-6", "gpt-4o", etc.

GeneratedResponse:
    content: str          # markdown output
    input_tokens: int
    output_tokens: int
    cached_tokens: int    # Anthropic prompt cache hits
```

**Why an abstraction?** You might want to use Claude for production documentation but Ollama for local development. Or switch from OpenAI to Gemini when pricing changes. The abstraction means the generation pipeline doesn't care which LLM it's talking to.

### Provider implementations

| Provider | Key Feature | Default Model |
|----------|------------|---------------|
| **Anthropic** | Prompt caching (~10% cost savings), retry with backoff | claude-sonnet-4-6 |
| **OpenAI** | OpenAI-compatible endpoint support (works with proxies) | gpt-5.4-nano |
| **Gemini** | Runs sync SDK in thread pool (async wrapper) | gemini-3.1-flash-lite |
| **Ollama** | Local inference, no API key, air-gap compatible | configurable |
| **LiteLLM** | 100+ LLMs via unified API (Groq, Together, Azure, etc.) | configurable |
| **Mock** | Deterministic responses for testing | — |

### Provider Registry

```python
provider = get_provider("anthropic", api_key="sk-...", model="claude-sonnet-4-6")
# Automatically attaches a RateLimiter with Anthropic defaults (50 RPM, 100k TPM)
```

The registry uses **lazy imports** — `pip install repowise-core` works without installing the Anthropic or OpenAI SDK. The SDK is only imported when you actually request that provider.

### Prompt caching (Anthropic)

Anthropic caches the system prompt server-side. Since all file_pages share the same system prompt, subsequent requests within a session use the cached version at reduced token cost. Repowise exploits this by keeping system prompts constant per page type.

---

## 6. Rate Limiter

LLM APIs have rate limits (e.g., Anthropic allows 50 requests/minute). The rate limiter prevents 429 errors.

### How it works

A **sliding-window** approach tracking both requests per minute (RPM) and tokens per minute (TPM):

```
┌─── 60-second window ──────────────────────────────┐
│ req  req  req  req  req  ......  req   [new req?] │
│  ↑ recorded timestamps                            │
└───────────────────────────────────────────────────┘

Can I make a new request?
  1. Prune timestamps older than 60 seconds
  2. Count remaining: if < RPM limit → RPM OK
  3. Sum tokens in window: if + estimated < TPM limit → TPM OK
  4. Both OK → proceed. Otherwise → sleep until a slot opens.
```

**Why sleep outside the lock?**

```python
async with self._lock:
    # Check limits
    if not ok:
        sleep_time = calculate_wait()
# Release lock THEN sleep
await asyncio.sleep(sleep_time)
```

If we held the lock during sleep, all other coroutines would block waiting for the lock. By releasing before sleeping, other coroutines can check their own limits independently. This prevents thundering herd — multiple coroutines don't all wake up at the same instant.

### Default limits per provider

| Provider | RPM | TPM |
|----------|-----|-----|
| Anthropic | 50 | 100,000 |
| OpenAI | 60 | 150,000 |
| Gemini | 60 | 1,000,000 |
| Ollama | 1,000 | 10,000,000 |

Ollama limits are essentially unlimited (local inference). Gemini's TPM is high because token counting is more generous.

### Retry on 429

If the API returns 429 despite rate limiting (e.g., shared quota), exponential backoff kicks in:

```
Attempt 1: wait 2^1 + jitter = ~2.5 seconds
Attempt 2: wait 2^2 + jitter = ~4.7 seconds
Attempt 3: wait 2^3 + jitter = ~8.3 seconds
Max: 64 seconds
```

---

## 7. Server Layer

### 7.1 FastAPI App

The server is a FastAPI application created via `create_app()` factory pattern.

**Startup (lifespan manager):**
1. Initialize SQLAlchemy async engine + session factory
2. Create FTS index (idempotent)
3. Build embedder from environment (REPOWISE_EMBEDDER → mock/gemini/openai)
4. Create vector store (InMemory or LanceDB)
5. Start APScheduler background jobs
6. Bridge state to MCP server (shared DB + stores)

**12 router groups:**

| Router | Path | Purpose |
|--------|------|---------|
| health | `/health` | Liveness check |
| repos | `/api/repos` | CRUD repositories |
| pages | `/api/pages` | Wiki pages + versioning + regeneration |
| search | `/api/search` | Full-text + semantic search |
| jobs | `/api/jobs` | Job status + SSE progress stream |
| symbols | `/api/symbols` | Symbol index lookup |
| graph | `/api/graph` | Dependency graph export + pathfinding |
| git | `/api/repos/{id}/git-*` | Hotspots, ownership, git summary |
| dead_code | `/api/dead-code` | Dead code findings |
| decisions | `/api/repos/{id}/decisions` | Architectural decision records |
| webhooks | `/api/webhooks` | GitHub + GitLab push handlers |
| chat | `/api/repos/{id}/chat` | Agentic chat with tool use |

### 7.2 Webhooks — Incremental Updates

When code is pushed to GitHub/GitLab, a webhook triggers incremental documentation updates:

```
GitHub push event
     │
     ▼
  Verify HMAC-SHA256 signature
     │
     ▼
  Store WebhookEvent in DB
     │
     ▼
  Find matching Repository by URL
     │
     ▼
  Create GenerationJob:
    mode: "incremental"
    config: {before: "abc123", after: "def456"}
     │
     ▼
  Background worker picks up job:
    1. git diff before..after → changed files
    2. Re-parse changed files
    3. ChangeDetector cascades through graph
    4. Regenerate affected pages (budget-limited)
    5. Update SQL + FTS + Vector stores
```

### 7.3 Scheduler

Two recurring background jobs (APScheduler):

| Job | Interval | Purpose |
|-----|----------|---------|
| Staleness checker | 15 min | Find stale/expired pages across all repos |
| Polling fallback | 15 min | Compare stored HEAD commit vs actual git HEAD (catches missed webhooks) |

### 7.4 Doctor and Three-Store Repair

`repowise doctor` runs health checks: git repo, `.repowise/` dir, database, state.json, providers, stale pages, and **three-store consistency** (SQL vs vector store vs FTS index).

With `--repair`, it fixes detected mismatches:
- Re-embeds pages missing from the vector store
- Re-indexes pages missing from the FTS index
- Deletes orphaned entries from vector store and FTS that no longer exist in SQL

This is powered by `list_page_ids()` on vector stores and `list_indexed_ids()` on FullTextSearch — methods that return the set of page IDs each store knows about, enabling set-difference consistency checks.

### 7.5 Chat — Agentic Loop

The chat endpoint runs an agentic loop where the LLM can call Repowise tools:

```
User: "How does auth work in this codebase?"
     │
     ▼
  LLM receives: system prompt (with repo context) + 10 tool schemas
     │
     ▼
  Iteration 1: LLM calls search_codebase("authentication")
  → Returns 5 relevant pages
     │
     ▼
  Iteration 2: LLM calls get_context(["src/auth/login.py"])
  → Returns docs + ownership + decisions
     │
     ▼
  Iteration 3: LLM synthesizes answer
  → Streams text response to user
     │
     ▼
  Save conversation to DB (for continuity)
```

Max 10 iterations per request. Streamed via SSE (Server-Sent Events).

---

## 8. MCP Tools

MCP (Model Context Protocol) lets AI coding assistants (Claude Code, Cursor, Windsurf, Cline) call Repowise tools directly. There are 10 tools, each answering a specific question.

### Tool 1: `get_overview` — "What is this codebase?"

**When to use:** First time exploring an unfamiliar repo.

**Returns:** Architecture summary, module map, entry points, and git health metrics (hotspot count, average bus factor, churn trend).

**Git health computation:**

```
churn_trend:
  recent_rate = total_commits_30d / 30 days
  baseline_rate = (total_commits_90d - total_commits_30d) / 60 days

  recent_rate > baseline × 1.3 → "increasing" (accelerating development)
  recent_rate < baseline × 0.7 → "decreasing" (cooling down)
  otherwise                    → "stable"
```

### Tool 2: `get_context` — "What does this code do?"

**When to use:** Before modifying a file, to understand its documentation, ownership, and governing decisions.

**Target resolution** (4-phase fallback):

```
Input: "auth.py"
  1. Try exact: file_page:auth.py         → found? return
  2. Try module: target_path = auth.py     → found? return
  3. Try symbol: ILIKE '%auth.py%'         → found? return
  4. Try file: target_path = auth.py       → found? return
  5. Fallback: check GitMetadata exists    → suggest fuzzy matches
```

**Returns:** Documentation, symbol list, importers, ownership (primary + recent owner), last change, governing decisions, freshness score.

### Tool 3: `get_risk` — "Will changing this break anything?"

**When to use:** Before a refactor, to assess blast radius.

**Risk type classification:**

```
bug-prone:      >= 40% of commits match (fix|bug|patch|hotfix|revert|crash|error)
churn-heavy:    churn_percentile >= 0.7
bus-factor-risk: bus_factor == 1 AND total_commits > 20
high-coupling:  dependents >= 5
stable:         none of the above
```

**Impact surface** — BFS up 2 hops through reverse dependencies, ranked by PageRank:

```
auth.py changed
  → middleware.py imports auth.py (PageRank 0.05)
  → main.py imports middleware.py (PageRank 0.08, entry point)
  → app.py imports main.py (PageRank 0.12)

Impact surface: [app.py, main.py, middleware.py]  (top 3 by criticality)
```

### Tool 4: `get_why` — "Why was this built this way?"

**When to use:** When you find surprising code and need to understand the rationale.

**Four modes:**

| Mode | Trigger | Returns |
|------|---------|---------|
| Health dashboard | No query, no targets | Decision counts, stale decisions, ungoverned hotspots |
| Path analysis | Target provided | Decisions governing that file + origin story + alignment score |
| Semantic search | Query provided | Decisions matching the query semantically |
| Target-aware search | Both | Decisions matching query that also govern targets |

**Origin story** — reconstructs the history of a file:

```
Who created it? → first commit author
Who maintains it now? → primary owner by blame
Key decisions? → commits with "migrate", "refactor", "deprecate" matched to ADRs
Alignment? → "high" if active decisions exist and siblings are similarly governed
```

### Tool 5: `search_codebase` — "Where is X implemented?"

**When to use:** Looking for specific functionality across the codebase.

**Search strategy:**

```
1. Wait for vector store ready (background async loading, up to 30s)
2. Semantic search (embedding similarity)
   → Fetch 3× limit (for filtering headroom)
   → 8-second timeout
3. Fallback to FTS if semantic fails
4. Boost recently-modified files:
   - Modified in last 30 days: +1.0× to relevance
   - Modified in last 90 days: +0.5×
5. Normalize confidence relative to top result
```

### Tool 6: `get_dependency_path` — "How does A connect to B?"

**When to use:** Understanding how two seemingly unrelated files are connected.

**When a path exists:** Returns the chain of imports (BFS shortest path).

**When no path exists:** Returns rich diagnostic context:

```
visual_context:
  reverse_path:       Does B → A exist? (dependency flows the other way)
  common_ancestors:   Nodes reachable from both A and B (via undirected BFS)
  shared_neighbors:   Files that both A and B directly connect to
  community_analysis: Are A and B in the same community?
  bridge_suggestions: High-PageRank files connecting both communities

co_change_signal:     If no import link but frequent co-changes → logical coupling
```

### Tool 7: `get_dead_code` — "What can we safely delete?"

**When to use:** Cleanup sprints, reducing maintenance burden.

**Findings organized in three tiers:**

| Tier | Confidence | Meaning |
|------|-----------|---------|
| High | >= 0.8 | Almost certainly dead. No importers, no recent commits, old. |
| Medium | 0.5 - 0.8 | Probably dead. No importers but has some recent activity. |
| Low | < 0.5 | Suspicious. Might be dynamically loaded or framework-used. |

**Supports rollup by directory or owner** — so you can say "show me all dead code owned by Alice in the payments/ directory."

### Tool 8: `get_architecture_diagram` — "Show me the structure"

**When to use:** Getting a visual understanding of the codebase architecture.

**Three scopes:**

| Scope | What you get |
|-------|-------------|
| `"repo"` | Full architecture diagram (from pre-generated page) |
| `"module"` | Subgraph of a specific module and its dependencies |
| `"file"` | Single file with its imports and importers |

Returns Mermaid syntax that renders as a flowchart in any Mermaid-compatible viewer.

---

## 9. Frontend (Web UI)

### Tech Stack

```
Next.js 15 + React 19 + TypeScript
Tailwind CSS v4 + Radix UI primitives
SWR (data fetching) + SSE (live job progress)
React Flow (graph visualization) + ELK.js (hierarchical layout)
Shiki (syntax highlighting) + next-mdx-remote (wiki rendering)
Recharts (charts) + Mermaid (diagrams)
Framer Motion (animations)
```

### Pages

| Route | Purpose |
|-------|---------|
| `/` | Dashboard — repo list, recent jobs, aggregate stats |
| `/repos/[id]` | Repository overview |
| `/repos/[id]/wiki/[...slug]` | Wiki page viewer (MDX with Mermaid + syntax highlighting) |
| `/repos/[id]/graph` | Interactive dependency graph (5 view modes) |
| `/repos/[id]/search` | Full-text and semantic search |
| `/repos/[id]/symbols` | Symbol index (sortable, filterable table) |
| `/repos/[id]/coverage` | Documentation freshness dashboard |
| `/repos/[id]/ownership` | Code ownership breakdown |
| `/repos/[id]/hotspots` | High-churn files |
| `/repos/[id]/dead-code` | Dead code findings with bulk actions |
| `/repos/[id]/decisions` | Architectural decision records |
| `/settings` | Global and per-repo settings |

### Graph Visualization

**5 view modes:**

| Mode | What it shows | Layout |
|------|-------------|--------|
| Module | Directory-level nodes (click to drill down) | ELK hierarchical |
| Full | File-level dependency graph | ELK hierarchical |
| Architecture | Entry points + 3-hop reachability | ELK hierarchical |
| Dead code | Unreachable nodes highlighted | ELK hierarchical |
| Hot files | High-churn files + their dependencies | ELK hierarchical |

**ELK.js** (Eclipse Layout Kernel) is used instead of force-directed layout because dependency graphs are DAGs — they have a natural top-to-bottom flow. ELK's layered algorithm respects this directionality, producing clean hierarchical layouts instead of the tangled hairballs that force-directed layouts create for DAGs.

**Interactive features:**
- **Drill-down**: In module view, click a module to expand into sub-modules
- **Path finder**: Select two nodes, find the shortest dependency path
- **Context menu**: Right-click for view docs, explore, set as path endpoint
- **Color modes**: By language, PageRank, or community
- **Ego sidebar**: Click a node to see its stats, git metadata, and connections
- **MiniMap**: Color-coded by doc coverage or importance

---

## Putting It All Together

Here's the complete flow from `repowise init` to serving documentation:

```
repowise init ./my-project
     │
     ▼
  FileTraverser: walk directory, apply filters
  → 500 files discovered
     │
     ▼
  ASTParser: tree-sitter queries per file
  → 500 ParsedFiles with 3,000 symbols and 2,000 imports
     │
     ▼
  GraphBuilder: resolve imports, build directed graph
  → 500 nodes, 2,000 edges
  → Compute: PageRank, betweenness, SCCs, communities
     │
     ▼
  GitIndexer: mine commit history
  → Ownership, churn, bus factor, co-changes per file
     │
     ▼
  DeadCodeAnalyzer: graph reachability from entry points
  → 15 unreachable files, 30 unused exports
     │
     ▼
  PageGenerator.generate_all():
  → Level 0: 3 API contracts
  → Level 1: 10 symbol spotlights
  → Level 2: 25 file pages
  → Level 3: 2 SCC cycle pages
  → Level 4: 8 module pages
  → Level 5: 3 cross-package pages
  → Level 6: repo overview + architecture diagram
  → Level 7: 2 infra pages
  Total: 55 pages generated via LLM
     │
     ▼
  Persistence:
  → SQL: 55 pages + 500 graph nodes + 2,000 edges + 500 git metadata rows
  → FTS: 55 pages indexed for keyword search
  → Vector: 55 pages embedded for semantic search
  → Graph: metrics stored in graph_nodes table
     │
     ▼
  CLAUDE.md: generated with architecture summary, module map, hotspots
     │
     ▼
  Ready to serve:
  → repowise serve    → REST API + Web UI at http://localhost:8877
  → repowise mcp      → MCP server for Claude Code / Cursor / Cline
  → repowise search   → CLI search across documentation
  → repowise update   → Incremental sync after code changes
```
