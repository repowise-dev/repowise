# LLM Doc Generation — Deep Dive

This document is a precise, code-level description of how repowise builds prompts and calls
LLMs to produce wiki pages. It covers every piece of context that goes into each call,
the exact generation ordering, how scalability is handled, and a frank gap analysis of
where the current implementation diverges from the intended design.

**Validated against:** actual generated output from `interview-coach/.repowise/export/` —
a test run on a 2022-file monorepo (Python + TypeScript) using Gemini, producing 33 pages.

---

## Table of Contents

1. [High-Level Pipeline](#1-high-level-pipeline)
2. [Page Types and Generation Levels](#2-page-types-and-generation-levels)
3. [What Context Goes to the LLM — Per Page Type](#3-what-context-goes-to-the-llm--per-page-type)
4. [Token Budget Mechanics](#4-token-budget-mechanics)
5. [Generation Ordering and Significance Filtering](#5-generation-ordering-and-significance-filtering)
6. [Prompt Caching and Provider Layer](#6-prompt-caching-and-provider-layer)
7. [Confidence and Freshness Tracking](#7-confidence-and-freshness-tracking)
8. [Scalability: Small vs. Large Repos](#8-scalability-small-vs-large-repos)
9. [Output Quality: What the LLM Actually Produces](#9-output-quality-what-the-llm-actually-produces)
10. [Gap Analysis — What Is Not Yet Wired Up](#10-gap-analysis--what-is-not-yet-wired-up)

---

## 1. High-Level Pipeline

Every wiki page follows the same three-step cycle:

```
ParsedFile + graph metrics + git metadata
        │
        ▼
ContextAssembler.assemble_*()
        │  builds a typed context dataclass
        ▼
Jinja2 template rendered → user_prompt string
        │
        ▼
LLMProvider.generate(system_prompt, user_prompt)
        │
        ▼
GeneratedPage (markdown content + token counts + confidence=1.0)
```

The key files:

| File | Role |
|------|------|
| `generation/context_assembler.py` | Assembles typed context dataclasses from ingestion output |
| `generation/page_generator.py` | Renders prompts, calls provider, wraps in `GeneratedPage` |
| `generation/models.py` | `GeneratedPage`, `GenerationConfig`, decay math |
| `generation/templates/*.j2` | One Jinja2 template per page type |

---

## 2. Page Types and Generation Levels

repowise generates 10 distinct page types in a strict dependency-ordered sequence:

| Level | Page Type | What It Documents | Template |
|-------|-----------|-------------------|----------|
| 0 | `api_contract` | OpenAPI / Protobuf / GraphQL schema files | `api_contract.j2` |
| 1 | `symbol_spotlight` | Top-10% public symbols by PageRank | `symbol_spotlight.j2` |
| 2 | `file_page` | Significant source files | `file_page.j2` |
| 3 | `scc_page` | Circular dependency clusters (len > 1) | `scc_page.j2` |
| 4 | `module_page` | Top-level directories as modules | `module_page.j2` |
| 5 | `cross_package` | Monorepo inter-package boundaries | *(stub — not yet implemented)* |
| 6 | `repo_overview` | Repository-level architecture summary | `repo_overview.j2` |
| 6 | `architecture_diagram` | Mermaid dependency graph | `architecture_diagram.j2` |
| 7 | `infra_page` | Dockerfile, Makefile, Terraform, shell | `infra_page.j2` |
| 7 | `diff_summary` | Git diff summaries (maintenance path) | `diff_summary.j2` |

Within each level, pages run concurrently up to `config.max_concurrency` (default: 5).
A level never starts until all coroutines in the previous level complete or fail.

**Page ID format:** `"{page_type}:{target_path}"` — deterministic, used for resume and
deduplication.

---

## 3. What Context Goes to the LLM — Per Page Type

Every LLM call is two parts:
- **System prompt** — a constant string per page type (enables Anthropic prefix caching)
- **User prompt** — rendered Jinja2 template with the assembled context

### 3.1 `file_page` — the most important page type

**System prompt (constant):**
```
You are repowise, an expert technical documentation generator.
Your task is to produce comprehensive, accurate wiki pages from source code.
Output markdown only. Do not include preamble or apologies.
Required sections: ## Overview, ## Public API, ## Dependencies, ## Usage Notes.
```

**User prompt context** (assembled by `ContextAssembler.assemble_file_page()`):

| Context piece | Source | Always included? |
|---------------|--------|-----------------|
| File path | `ParsedFile.file_info.path` | Yes |
| Language | `ParsedFile.file_info.language` | Yes |
| Module docstring | `ParsedFile.docstring` | If present |
| PageRank score | `graph_builder.pagerank()[path]` | Yes |
| Betweenness centrality | `graph_builder.betweenness_centrality()[path]` | Yes |
| Community ID (Louvain cluster) | `graph_builder.community_detection()[path]` | Yes |
| Flags: `is_api_contract`, `is_entry_point`, `is_test` | `ParsedFile.file_info` | Yes |
| Public symbols first (name, kind, signature, docstring, visibility, complexity, decorators, start/end lines) | `ParsedFile.symbols` | Within token budget |
| Private documented symbols | `ParsedFile.symbols` | Within token budget |
| Private undocumented symbols | `ParsedFile.symbols` | Within token budget |
| Import statements (up to 30) | `ParsedFile.imports[].raw_statement` | Within token budget |
| Export names | `ParsedFile.exports` | Yes |
| Dependencies (files this imports, from graph edges) | `graph.successors(path)` | Yes |
| Dependents (files that import this, from graph edges) | `graph.predecessors(path)` | Yes |
| Parse errors | `ParsedFile.parse_errors` | If present |
| Source code snippet | `source_bytes` decoded, trimmed to budget | Within token budget |
| RAG context (related page summaries) | `ctx.rag_context` list | If populated |
| Git: primary owner + % lines | `git_metadata.primary_owner_name` | If git enabled |
| Git: top 3 contributors | `git_metadata.top_authors` | If git enabled |
| Git: significant commit messages (date, author, message) | `git_metadata.significant_commit_messages` | If git enabled |
| Git: total commit count + age in days | `git_metadata.commit_count_total` | If git enabled |
| Git: `is_hotspot` flag | `git_metadata.is_hotspot` | If git enabled |
| Git: `is_stable` flag | `git_metadata.is_stable` | If git enabled |
| Git: co-change partners (files changed together without import link) | `git_metadata.co_change_partners[:3]` | If git enabled |
| Dead code findings (symbol name, reason, confidence, safe_to_delete) | From `DeadCodeAnalyzer` | If dead code enabled |

**Symbol priority order** in the prompt: public → private-with-docstring → private-undocumented.
Each symbol's token cost is measured before adding — the loop stops when budget is exhausted.

**Source code placement:** The source snippet always comes last in the budget calculation.
All other fields are reserved first; the source gets whatever tokens remain.

### 3.2 `symbol_spotlight`

**System prompt:**
```
You are repowise, an expert technical documentation generator.
Write a detailed spotlight page for a single code symbol.
Output markdown only.
Required sections: ## Purpose, ## Signature, ## Parameters, ## Returns, ## Example Usage.
```

**User prompt context:**

| Context piece | Source |
|---------------|--------|
| Symbol name, qualified name, kind | `Symbol` |
| File path | `ParsedFile.file_info.path` |
| `is_async` flag | `Symbol.is_async` |
| Complexity estimate | `Symbol.complexity_estimate` |
| Full signature string | `Symbol.signature` |
| Docstring (if any) | `Symbol.docstring` |
| Decorators | `Symbol.decorators` |
| Callers (files that import the containing module, from graph in-edges) | `graph.predecessors(path)` |

**Note:** Symbol spotlight only gets the signature + docstring — not the actual function body
(source code). There is no per-symbol source extraction.

### 3.3 `module_page`

**System prompt:**
```
You are repowise, an expert technical documentation generator.
Write a module-level overview page summarising all files in the module.
Output markdown only.
Required sections: ## Overview, ## Public API Summary, ## Architecture Notes.
```

**User prompt context:**

| Context piece | Source |
|---------------|--------|
| Module path (top-level directory name) | `Path(file_path).parts[0]` |
| Language | From constituent files |
| Total symbol count | Summed from all `FilePageContext` in the module |
| Public symbol count | Filtered from symbol dicts |
| Entry point files | Files where `is_entry_point == True` |
| Cross-module dependencies | Aggregated `dependencies` minus intra-module edges |
| Cross-module dependents | Aggregated `dependents` minus intra-module edges |
| Mean PageRank of module files | Mean of `pagerank_score` across all file contexts |
| File list | All file paths in the module |
| Team ownership summary | `module_git_summary` (variable injection, see gap §9.3) |

**Important:** The module page gets `FilePageContext` objects (which contain symbol metadata),
but the Jinja2 template only renders the file path list — it does not include the file pages'
actual generated markdown content. The LLM sees which files are in the module but not what
those files do.

### 3.4 `scc_page`

**System prompt:**
```
You are repowise, an expert technical documentation generator.
Document this circular dependency cycle and provide actionable refactoring advice.
Output markdown only.
Required sections: ## Cycle Description, ## Files Involved, ## Why This Exists,
## Refactoring Suggestions.
```

**User prompt context:**

| Context piece | Source |
|---------------|--------|
| SCC ID | `f"scc-{index}"` |
| Cycle description string | `"Circular dependency cycle: A → B → C"` (ordered paths joined) |
| Files in cycle | `sorted(scc)` — path list |
| Total symbol count across all cycle members | Summed from `FilePageContext` list |

**Note:** The SCC page context is very thin — the LLM gets file paths and a total symbol
count but no signatures, no source, and no information about which symbols create the cycle.

### 3.5 `repo_overview`

**System prompt:**
```
You are repowise, an expert technical documentation generator.
Write a high-level repository overview suitable for onboarding new developers.
Output markdown only.
Required sections: ## Project Summary, ## Technology Stack, ## Entry Points, ## Architecture.
```

**User prompt context:**

| Context piece | Source |
|---------------|--------|
| Repo name | `repo_structure.name` |
| Is monorepo | `repo_structure.is_monorepo` |
| Total files | `repo_structure.total_files` |
| Total lines of code | `repo_structure.total_loc` |
| Circular dependency count | SCCs with `len > 1` |
| Package list (name, path, language) | `repo_structure.packages` |
| Language distribution (percent per language) | `repo_structure.root_language_distribution` |
| Entry points | `repo_structure.entry_points` |
| Top 20 files by PageRank (path + score) | Top of `pagerank` dict sorted descending |
| Codebase health signals | `repo_git_summary` (variable injection, see gap §9.3) |

### 3.6 `architecture_diagram`

**System prompt:**
```
You are repowise, an expert technical documentation generator.
Generate an architecture overview with a Mermaid diagram.
You MUST include a fenced mermaid block with graph TD showing key dependencies.
Output markdown only.
```

**User prompt context:**

| Context piece | Source |
|---------------|--------|
| Repo name | Passed directly |
| ALL graph nodes (every non-external file) | `graph.nodes()` filtered |
| ALL graph edges (every dependency) | `graph.edges()` filtered |
| Community → member file mapping | All entries in `community` dict |
| SCC groups (only non-singleton) | `sccs` filtered |

**Warning:** For large repos this dumps every node and edge — a 1,000-file repo produces
a prompt with 1,000 node lines and potentially thousands of edge lines. See gap §9.5.

### 3.7 `api_contract`

**System prompt:**
```
... Document this API contract file for developers integrating with the service.
Required sections: ## Overview, ## Endpoints, ## Schemas, ## Authentication, ## Examples.
```

**User prompt context:** Full raw file content (trimmed to token budget) + function signatures
as endpoints + class names as schemas.

### 3.8 `infra_page`

**System prompt:**
```
... Document this infrastructure file for DevOps and platform engineers.
Required sections: ## Purpose, ## Key Targets/Stages, ## Configuration, ## Operational Notes.
```

**User prompt context:** Full raw file content (trimmed to token budget) + symbol names
(e.g., Makefile targets).

### 3.9 `diff_summary`

**System prompt:**
```
... Summarise the changes between two git refs and their documentation impact.
Required sections: ## Summary, ## Changed Files, ## Symbol Changes, ## Affected Documentation.
```

**User prompt context:** Added/deleted/modified file lists, symbol diffs, affected page IDs,
trigger commit SHA + message + author + diff text (trimmed to 1,000 tokens).

---

## 4. Token Budget Mechanics

### Budget constants (from `GenerationConfig` defaults)

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `token_budget` | 8,000 tokens | Maximum context fed to the LLM per call |
| `max_tokens` | 4,096 tokens | Maximum LLM output |
| `temperature` | 0.3 | Low temperature for consistent, factual output |

### Token estimation

repowise uses a rough heuristic: `len(text) // 4` characters-to-tokens. No tiktoken
dependency. This is accurate for English prose (~4 chars/token) but underestimates for
dense code (operators, brackets, identifiers) and overestimates for unicode-heavy code.

### Budget allocation for `file_page` (in order)

```
budget = 8,000 tokens
─────────────────────────────────────────────────────────
Step 1: path + language overhead             ~5 tokens  (always)
Step 2: public symbol signatures             accumulated, stop when budget hit
Step 3: private documented symbol sigs      accumulated, stop when budget hit
Step 4: private undocumented symbol sigs    accumulated, stop when budget hit
Step 5: import statements (max 30)          skipped entirely if over budget
Step 6: source code snippet                 trimmed to REMAINING tokens
─────────────────────────────────────────────────────────
Git context, dead code, RAG context: added to the dataclass but NOT
included in the budget calculation — they expand the prompt beyond the budget.
```

**Key insight:** The source code is the lowest-priority item in the budget allocation.
For a file with many symbols, the source may be heavily truncated or replaced with
`...[truncated]`.

### Page budget cap

Total page count is capped at `max(50, N_files * max_pages_pct)` where
`max_pages_pct = 0.10`. For a 100-file project: cap = 50 pages. For a 50,000-file
project: cap = 5,000 pages. Fixed-overhead pages (api, scc, module, overview, diagram)
are subtracted from this budget before allocating file pages and symbol spotlights.
File pages get priority over symbol spotlights within the remaining budget.

---

## 5. Generation Ordering and Significance Filtering

### Why ordering matters

The generation order is topologically motivated: dependencies are generated before
dependents. This means when a file page is generated, its imported modules' pages
already exist in the vector store — they can theoretically be retrieved as RAG context
for the importing file's generation.

### File page significance filter

Not every code file gets its own `file_page`. A file is significant if ALL of:
- Has at least `file_page_min_symbols` (default: 1) symbols, AND
- Is an entry point, OR in the top PageRank percentile, OR has non-zero betweenness centrality

Files that pass this filter get a generated page. Files that don't still have their
`FilePageContext` assembled (needed for module pages) but no LLM call is made.

**For a 100-file project:** With `file_page_top_percentile = 0.10`, the top ~10 files
by PageRank get pages, plus all entry points and bridge files. In practice, betweenness
centrality > 0 catches most architecturally important files even at low PageRank.

**For a 50,000-file project (tokio-rs scale):** The same logic applies — only the most
central files get individual pages. Low-PageRank leaf files (test helpers, simple data
structures) are documented only at the module level.

### Module grouping

Modules are defined as `Path(file_path).parts[0]` — the top-level directory. A file
at `src/auth/jwt.py` belongs to module `src`. A file at `tests/test_auth.py` belongs
to module `tests`. This is a simple heuristic that works well for standard Python/Go
layouts but less well for deeply nested monorepos.

### Generation depth auto-upgrade

When git metadata is available, `_select_generation_depth()` can upgrade to `"thorough"`:
- `is_hotspot == True`
- > 100 total commits AND > 10 in last 90 days
- >= 8 significant commit messages
- Any co-change partners

Or downgrade to `"minimal"`:
- `is_stable == True` AND PageRank < 0.3 AND < 5 total commits

Note: The `depth` selection is computed but currently not plumbed through to the
template or the `generate_*` calls — the value is returned but unused in `generate_all`.

---

## 6. Prompt Caching and Provider Layer

### In-process SHA256 cache

`PageGenerator` maintains an in-memory dict keyed by:
```python
SHA256(f"{model_name}:{page_type}:{user_prompt}")
```

If the same file is enqueued twice (e.g., after a resume), the cached response is
returned without an LLM call. This cache is per-process — it does not persist across
`repowise init` runs.

### Anthropic prefix caching

System prompts are module-level Python constants (not Jinja2 templates). This is
intentional: Anthropic's server-side prefix caching only works when the prefix is
byte-for-byte identical across calls. Using a constant guarantees this.

For a 1,000-file repo with Anthropic, all `file_page` calls share the same system
prompt. The system prompt is billed once; subsequent calls reuse the cached prefix.
Cost reduction on large repos is typically 60–90% on input tokens.

### Batch mode (Anthropic only)

`AnthropicProvider.generate_batch()` submits all requests for a level as a single
Message Batches API request. Cheaper (~50%) than streaming but asynchronous — the
job polls for completion. Enabled by default for `repowise init`. Pass `--no-batch`
for streaming (faster, costlier).

---

## 7. Confidence and Freshness Tracking

Every `GeneratedPage` starts with `confidence = 1.0`. Confidence decays over time
and on code changes.

### Time-based decay (`decay_confidence`)

Linear decay from 1.0 to 0.0 over `expiry_threshold_days` (default: 30):
```python
new_confidence = max(0.0, 1.0 - days_since_update / expiry_threshold_days)
```

### Change-driven decay (`compute_confidence_decay_with_git`)

Applied multiplicatively. Base factors:

| Relationship to changed file | Base decay |
|-----------------------------|------------|
| Direct (source file changed) | × 0.85 |
| 1-hop (symbol called/inherited) | × 0.95 |
| 2-hop (looser reference) | × 0.98 |
| Co-change partner (no import link) | × 0.97 |

Git modifiers applied on top:

| Condition | Effect |
|-----------|--------|
| `is_hotspot` + direct | × 0.94 additional |
| `is_hotspot` + 1-hop | × 0.95 additional |
| `is_stable` + direct | × 1.03 additional (slower decay) |
| Commit message contains "rewrite/refactor/migrate" + direct | × 0.71 additional (hard decay) |
| Commit message contains "typo/lint/format" + direct | × 1.12 additional (soft decay) |

### Freshness status thresholds

| `confidence` | Status | UI badge |
|-------------|--------|----------|
| ≥ 0.80 | fresh | green |
| 0.60–0.79 | stale | yellow |
| 0.30–0.59 | outdated | red |
| < 0.30 | unusable | red prominent |

---

## 8. Scalability: Small vs. Large Repos

### Small project (100 files)

- Page cap: `max(50, 100 * 0.10) = 50` pages
- All entry points + bridge files get `file_page`
- Top 10% symbols get `symbol_spotlight`
- Likely 1–3 modules (top-level dirs), so 1–3 `module_page` entries
- Full graph fits in memory as NetworkX `DiGraph` in `.repowise/graph.json`
- Full source snippets fit in token budget for most files
- `repowise init` runs in minutes

### React / tokio-rs scale (thousands of files)

- Page cap: `max(50, 5000 * 0.10) = 500` pages for a 5,000-file repo
- Significance filter is critical — only architecturally central files get pages
- Module grouping by top-level dir creates logical package pages
- Graph switches to SQLite-backed `networkit` above 30K nodes
- `concurrent_jobs = 5` prevents runaway API parallelism
- `repowise init` supports `--resume` — checkpoint after every completed page
- Batch mode (Anthropic) makes large-scale init economically practical
- Source snippets for large files will be heavily truncated

---

## 9. Output Quality: What the LLM Actually Produces

This section is grounded in the `interview-coach` test run exports. The test run used
`gemini-3.1-flash-lite-preview` via LiteLLM on a 2022-file monorepo, producing 33 pages
in ~17 minutes. `token_budget=8000`, `max_tokens=4096`, `max_concurrency=3`.

### 9.1 File pages — consistently high quality

`supabase_service.py.md`, `node_creators.py.md`, `supabase_client.py.md` all show:
- Correct Overview section that accurately describes the module's purpose
- Accurate Public API section listing all methods/functions with their signatures
- Correct Dependencies (listed as module paths, not wiki summaries — see gap 10.2)
- Non-trivial Usage Notes: the LLM inferred singleton pattern, RLS security implications,
  TTS-aware response length constraints, state key requirements — none of which are in
  the signature. This comes entirely from reading the source snippet.

**Verdict:** File page quality is excellent. The raw source code in the prompt is doing
most of the work.

### 9.2 Symbol spotlight — good quality from signature alone

`get_user_analyses.md` produces all required sections (Purpose, Signature, Parameters,
Returns, Example Usage) with only the signature and docstring as input. The generated
example is syntactically correct and semantically plausible.

**Verdict:** For well-named symbols with full type signatures, the output is useful
even without the source body (gap 10.8). For complex symbols with no docstring,
quality will degrade.

### 9.3 Module pages — surprisingly good despite thin context

`backend.md` covers a module containing hundreds of files. The context only included
file paths, symbol counts, and dependency paths — no file page content. Yet the output
correctly identifies major sub-systems (Pipecat, resume analyzer, email engine, thita_bot),
architecture patterns (service-oriented, middleware-based auth), and technology choices.

The LLM is doing pure inference from directory structure and file path names. This works
well for descriptively-named codebases. It would produce generic output for a codebase
with opaque naming (e.g. `svc1/`, `lib2/`).

**Verdict:** Module quality is better than the thin context would suggest, but is
entirely dependent on meaningful file and directory naming.

### 9.4 SCC pages — excellent despite minimal context

Both SCC pages reviewed (`scc-69.md`, `scc-1068.md`) produced genuinely useful output:
- `scc-69` correctly inferred the auth→payment→tier data flow cycle and why each link
  exists, purely from the three file names
- `scc-1068` correctly identified the `__init__.py` re-export + registration pattern
  anti-pattern, noted the deprecated status, and suggested deletion as the primary fix

Both provided concrete, specific refactoring strategies with code examples.

**Verdict:** SCC quality is excellent. The LLM extracts significant signal from file
path names alone. Gap 10.6 (thin context) is technically correct but practically less
impactful than initially assessed.

### 9.5 Repo overview — excellent

`repo.md` correctly identifies the monorepo structure, accurately reports 7 circular
dependencies, names the technology stack (Next.js, Pipecat, Supabase, Manim, Crawl4AI),
and lists real entry points. The architecture section is coherent and accurate.

**Verdict:** Repo overview quality is excellent.

### 9.6 Architecture diagram — good output but fragile approach

`interview-coach.md` contains a clean 7-node Mermaid diagram for a 2022-file repo.
The context passed in lists all 2022 node names and thousands of edges — the raw
prompt must have been massive. The LLM abstracted to high-level components anyway.

**This is coincidentally good, not reliably good.** A different model or a different
run could produce a 2022-node unrenderable Mermaid block. The approach of dumping all
nodes/edges relies on the LLM choosing to abstract, without any guidance to do so.
Gap 10.4 stands.

### 9.7 Job tracking — confirmed broken in production run

From `jobs/6b10db4b-....json` (a failed job):
```json
{
  "total_pages": 0,
  "completed_pages": 0,
  "failed_pages": 5,
  "failed_page_ids": ["level-1", "level-2", "level-3", "level-4", "level-6"]
}
```

Two confirmed bugs:
1. `total_pages: 0` and `completed_pages: 0` even though 33 pages exist in `state.json`.
   Root cause: `job_system.start_job(job_id, len(all_pages))` is called at the *end* of
   `generate_all`, after all generation completes. The counter is set to the final count
   post-hoc, never live during generation.
2. `failed_page_ids` contains level names (`"level-1"`) not page IDs. Root cause:
   `job_system.fail_page(job_id, f"level-{level}", str(r))` passes the level string
   as the page ID. The job log is unactionable for debugging specific failed pages.

---

## 10. Gap Analysis — What Is Not Yet Wired Up

The following gaps represent divergence between the intended architecture and the
current code. Severity ratings are informed by the actual output quality observed
in the `interview-coach` test run — where the LLM compensated for some gaps through
inference, and exposed others clearly.

---

### 10.1 RAG context is never populated during `file_page` generation (CRITICAL)

**What the architecture says:**
> "RAG context — vector store similarity search using this file's top exported symbols
> as the query. Returns the top 3 most relevant already-generated pages."

**What the code does:**
`ContextAssembler.assemble_file_page()` returns a `FilePageContext` with
`rag_context: list[str] = field(default_factory=list)` — always an empty list.
The vector store is never queried during context assembly. The field is only populated
in `assemble_update_context()` — and there it's used to pass trigger commit info,
not related page summaries.

**Impact:** The most powerful quality driver described in the architecture is not active.
File pages are generated without any knowledge of what their dependencies actually do.

**What needs to happen:** `assemble_file_page()` needs the vector store passed in,
and must call `vector_store.search(query=exported_symbol_names, top_k=3)` to retrieve
related page summaries before building the context.

---

### 10.2 Import summaries are missing — dependencies shown as bare paths only (CRITICAL)

**What the architecture says:**
> "Import summaries — for each file this file imports from: the summary of that file's
> already-generated wiki page (if available), or just its public API signatures."

**What the code does:**
`file_page.j2` renders `ctx.dependencies` as a list of file path strings:
```
## Dependencies (files this imports)
- `src/auth/jwt.py`
- `src/db/connection.py`
```

The LLM sees that `jwt.py` is imported but has no idea what `jwt.py` does. The
generation level ordering guarantees that `jwt.py`'s page was already generated — but
that page's content is never retrieved and included.

**Confirmed in output:** `supabase_service.py.md` lists `core.config`, `models.resume_analyzer_models`,
`services.supabase_service` as dependency names — these are module path strings inferred
from import statements. The LLM wrote accurate descriptions of what each dependency does,
but only because it read the source snippet and recognized the import patterns. Without the
source (e.g., for a large chunked file), dependency sections would be empty.

**Impact:** Dependency section quality is fragile — it works when source fits in the budget,
fails for large files. With import summaries, the LLM could document dependencies even when
source is truncated.

**What needs to happen:** After Level 2 completes, each file's generated page content
should be queryable by path. During context assembly, resolved dependency summaries
(or at minimum, their public symbol signatures) should be embedded in the prompt.

---

### 10.3 `module_git_summary` and `repo_git_summary` are dead template variables (SIGNIFICANT)

**What the templates do:**
Both `module_page.j2` and `repo_overview.j2` have blocks gated on these variables:
```jinja2
{% if module_git_summary is defined and module_git_summary %}
## Team ownership
...
{% endif %}
```

**What the code does:**
`PageGenerator.generate_module_page()` renders the template as:
```python
user_prompt = self._render("module_page.j2", ctx=ctx)
```

`module_git_summary` is never passed as a kwarg. In Jinja2 with `StrictUndefined`,
accessing an undefined variable raises an error — but the `is defined` check safely
returns False, so the block is silently skipped every time.

**Confirmed in output:** `backend.md` has no ownership section. `repo.md` has no health
signals section (no hotspot count, no top churn files). Both template blocks were silently
skipped on every call.

**Impact:** Module pages never show team ownership. Repo overview pages never show
health signals (hotspot count, stable files, top churn files, oldest file).

**What needs to happen:** Both generators need to query git metadata and pass
`module_git_summary=...` and `repo_git_summary=...` as keyword arguments to `_render()`.

---

### 10.4 Architecture diagram dumps ALL nodes and edges regardless of repo size (SIGNIFICANT)

**What the code does:**
`assemble_architecture_diagram()` passes every non-external node and every edge to
the template. For a 1,000-file repo:
```
**Nodes** (1,000 files):
- `src/auth/jwt.py`
- `src/auth/session.py`
... 998 more lines ...

**Edges** (3,500 dependencies):
- `src/app.py` → `src/auth/jwt.py`
... 3,499 more lines ...
```

This produces a prompt that will exceed any reasonable token budget and result in
an unrenderable or truncated Mermaid diagram.

**Observed in test run:** The 2022-file repo produced `interview-coach.md` with a clean
7-node Mermaid diagram. The LLM abstracted the thousands of nodes/edges to high-level
components. This happened despite receiving an enormous context — likely because the
prompt was so large the LLM had no choice but to summarise. The output was good *this
time*, but the approach is fragile: a less capable model, a stricter max_tokens setting,
or a repo with less descriptive paths would produce a broken or unrenderable diagram.

**Impact:** The architecture diagram is fragile at scale. It works by luck with capable
models but has no structural guarantee of producing a useful Mermaid diagram.

**What needs to happen:**
- Filter nodes to top-N by PageRank (e.g., top 50)
- Filter edges to only those connecting the selected nodes
- Collapse communities into single labeled nodes for the Mermaid output
- Provide a separate "module-level" diagram view using `module_page` membership

---

### 10.5 Large file chunking is not implemented — only truncation (SIGNIFICANT)

**What the architecture says:**
> "Files over `large_file_threshold_kb` (100KB) are handled differently: extracts
> public symbol signatures only for the top-level context, generates one sub-page per
> major class or function group, synthesizes a file page from the sub-pages."

**What the code does:**
`_trim_to_budget()` truncates source to whatever tokens remain after symbols and
imports. A 150KB file with many symbols may have its source truncated to ~500 tokens
or less. There is no sub-page generation, no chunking strategy, and no synthesis step.

**Impact:** Large files (common in tokio-rs, React internals) get documentation
built from heavily truncated source. The LLM cannot see the lower half of the file.

---

### 10.6 SCC context provides no symbol information (LOW — LLM compensates well)

**What the code does:**
`assemble_scc_page()` creates a string `"Circular dependency cycle: A → B → C"`
and a total symbol count. The template renders these two pieces.

**What the architecture says:**
> "Generate each SCC member with reduced context (own source + signatures of cycle
> partners only)"

The SCC page context has no signatures, no source, and no information about which
specific symbols create the cycle (the imports that close the loop).

**Observed in test run:** Despite this, `scc-69.md` and `scc-1068.md` both produced
excellent output. The LLM inferred cycle causes and provided specific, actionable
refactoring advice entirely from file path names. `scc-1068` correctly identified the
`__init__.py` re-export anti-pattern and even recommended deletion since the module
is deprecated — information visible only in the path `deprecated/interview_service_old/`.

**Revised impact:** For descriptively-named codebases, SCC quality is high despite
thin context. For codebases with opaque names (`module_a/`, `handler_v2/`), the
LLM would produce generic advice. Adding the specific cross-imports that close the
cycle would improve reliability across all codebases.

---

### 10.7 Cross-package generation is stubbed out for monorepos (SIGNIFICANT)

**What the code does:**
```python
# ---- Level 5: cross_package (only if monorepo) ----
if repo_structure.is_monorepo:
    log.info("Skipping cross_package generation (Phase 4)")
```

No `cross_package` pages are ever generated. For a monorepo like a React-scale project,
this means there is no documentation of how `@react/core` depends on `@react/scheduler`,
or what the public API boundary between packages is.

---

### 10.8 Symbol spotlight lacks source body (MODERATE — degrades for undocumented symbols)

`symbol_spotlight.j2` includes the signature and docstring but not the function body.
For a complex 80-line function with no docstring, the LLM gets only the signature line.
It cannot generate "## Example Usage" or explain implementation details without seeing
the code.

**Observed in test run:** `get_user_analyses.md` produced a correct, usable example.
But `get_user_analyses` has a full type signature with all parameter names and types —
the LLM had enough to infer usage. For internal symbols with opaque signatures and no
docstring, the output would degrade to a near-empty example section.

**What needs to happen:** Extract the source lines between `symbol.start_line` and
`symbol.end_line` from `source_bytes` and include them in the spotlight context.

---

### 10.9 Generation depth selection result is unused (MODERATE)

`_select_generation_depth()` computes `"minimal"` / `"standard"` / `"thorough"` based
on git metadata and PageRank, but the return value is never plumbed into `generate_all()`
or any template. All file pages receive identical treatment regardless of depth.

**What needs to happen:** Pass the computed depth to the template as a `ctx.depth`
field and use it to conditionally include/exclude context sections (e.g., skip source
snippet for `"minimal"` pages, include extra analysis instructions for `"thorough"`).

---

### 10.10 `_sort_level_files` is defined but never awaited (MINOR)

```python
async def _sort_level_files(self, files, git_meta_map, pagerank) -> list[ParsedFile]:
    ...
```

This method is defined but never called in `generate_all()`. Level 2 processes
`code_files` in the order they come from the traverser. Entry points, hotspots, and
high-PageRank files should be generated first to maximize their availability as RAG
context for later files — but this priority sort is never applied.

---

### 10.11 Token budget default (8K) doesn't match architecture doc (12K) (MINOR)

`GenerationConfig.token_budget` defaults to `8000`. The architecture document states:
> "Token budget: the total assembled context targets 12K tokens."

The architecture doc also describes a specific priority drop order (git context is the
*last* thing dropped), while the code drops git context first (it's outside the budget
calculation entirely — it's appended unconditionally after the budget loop).

**Observed in test run:** Config snapshot from `jobs/6b10db4b-....json` confirms
`token_budget: 8000` was used in the actual run.

---

### 10.12 Job progress tracking is broken (SIGNIFICANT — live UI will show wrong data)

`job_system.start_job(job_id, len(all_pages))` is called at the *end* of `generate_all`,
after all pages have been generated. This means:
- During generation, `total_pages` is always `0` in the DB
- `completed_pages` never increments during generation
- The SSE job stream sent to the web UI shows `0 / 0` pages for the entire run
- If the process crashes mid-run, the job log shows 0 completed even if 400 pages
  finished before the crash

**Confirmed in test run:** A failed job (`6b10db4b-....json`) shows
`total_pages: 0, completed_pages: 0` with `failed_pages: 5`, despite `state.json`
recording 33 successfully generated pages from a separate run.

**What needs to happen:** Call `start_job(job_id, estimated_total)` at the *start* of
`generate_all`, before the first level runs. Use the significance filter pre-computation
that already exists to estimate page count before generation begins.

---

### 10.13 `failed_page_ids` stores level names, not page IDs (MINOR)

```python
job_system.fail_page(job_id, f"level-{level}", str(r))
```

The second argument is the page ID, but `f"level-{level}"` is passed instead of an
actual page ID. The resulting `failed_page_ids` list contains entries like
`["level-1", "level-2", "level-3"]` — useless for identifying which specific pages
failed or retrying them on resume.

**Confirmed in test run:** `failed_page_ids: ["level-1", "level-2", "level-3", "level-4", "level-6"]`
in the failed job JSON.

**What needs to happen:** When catching exceptions from `asyncio.gather`, extract the
page ID from the coroutine context and pass that instead of the level string.

---

## Summary: Gaps by Priority

Severity ratings updated based on actual output from the `interview-coach` test run.

| # | Gap | Severity | Evidence | Effort |
|---|-----|----------|----------|--------|
| 10.1 | RAG context never populated | **Critical** | Always `[]` in code | Medium |
| 10.2 | Import summaries are bare paths | **Critical** | Confirmed in all file pages | Medium |
| 10.3 | `module_git_summary`/`repo_git_summary` dead | **Significant** | Confirmed missing in `backend.md`, `repo.md` | Low |
| 10.4 | Architecture diagram dumps all nodes/edges | **Significant** | Worked by luck in test run; fragile | Medium |
| 10.5 | No large file chunking | **Significant** | Not exercised in test run; logic gap clear | High |
| 10.6 | SCC context has no symbols | **Low** | LLM compensates well from path names alone | Low |
| 10.7 | Cross-package pages stubbed | **Significant** | Hardcoded skip in `generate_all` | High |
| 10.8 | Symbol spotlight lacks source body | **Moderate** | Good output for typed symbols; degrades for untyped | Low |
| 10.9 | Depth selection unused | **Moderate** | `_select_generation_depth` return value discarded | Low |
| 10.10 | `_sort_level_files` never called | **Minor** | Dead async method | Low |
| 10.11 | Budget is 8K not 12K | **Minor** | Confirmed 8K in job snapshot | Low |
| 10.12 | Job progress tracking broken | **Significant** | `total_pages: 0` in live jobs; SSE shows 0/0 | Low |
| 10.13 | `failed_page_ids` stores level names | **Minor** | Confirmed `["level-1","level-2",...]` in job JSON | Low |
