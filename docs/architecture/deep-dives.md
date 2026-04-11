# Repowise Deep Dives — Complete Guide

This document covers systems that are referenced but not fully explained in `architecture-guide.md` and `graph-algorithms-guide.md`. Each section is self-contained with full intuition, implementation details, and the math behind the algorithms.

---

## Table of Contents

1. [Dead Code Detection](#1-dead-code-detection)
2. [Decision Records (ADR) System](#2-decision-records-adr-system)
3. [Search and Vector Store Internals](#3-search-and-vector-store-internals)
4. [Incremental Updates and Webhooks](#4-incremental-updates-and-webhooks)
5. [Change Cascade Algorithm](#5-change-cascade-algorithm)

---

## 1. Dead Code Detection

### What problem does it solve?

Every codebase accumulates files and functions that nothing uses anymore. A refactor removes the last caller of `old_parser.py` but nobody deletes the file. Over months, these dead files pile up — increasing maintenance burden, confusing new developers, and inflating CI times.

Repowise's dead code analyzer finds these automatically using **pure graph traversal + git metadata**. No LLM calls. Runs in under 10 seconds.

### The four detection strategies

#### Strategy 1: Unreachable Files

**Question:** "Is anything importing this file?"

**Algorithm:**

```
For each node in the dependency graph:
    Skip if: external package, non-code language, entry point, test file,
             fixture directory, __init__.py, config file, migration, etc.

    if in_degree(node) == 0:
        This file has zero importers → candidate for dead code
```

`in_degree` is just the count of incoming edges — files that import this one. If nobody imports it and it's not an entry point or test, it's suspicious.

**Why in_degree alone isn't enough:**

Consider `plugin_auth.py`. Nothing imports it directly because the plugin framework loads it dynamically at runtime via `importlib.import_module()`. The graph doesn't capture dynamic imports as edges because they don't appear in the AST as static import statements.

Repowise handles this with multiple layers of filtering, including **dynamic import detection** — files in the same package as `importlib.import_module()` or `__import__()` calls automatically get reduced confidence scores:

**Layer 1 — Structural exclusions** (never flagged):

| Exclusion | Why |
|-----------|-----|
| Entry points (`main.py`, `index.ts`, `app.py`) | They're where execution starts, nothing imports them |
| Test files | Tests import production code, not the other way around |
| `__init__.py` | Package initializers, loaded by Python automatically |
| Config files (`setup.py`, `next.config.js`, `vite.config.ts`) | Loaded by frameworks |
| Migrations (`*migrations*`) | Run by migration tools, not imported |
| Schema/seed files | Data definitions, not code |
| Fixture directories (`fixtures/`, `testdata/`, `sample_repo/`) | Test data, not production code |
| Non-code languages (JSON, YAML, Markdown, SQL, Terraform) | No import semantics |
| API contracts (proto, graphql marked `is_api_contract`) | Consumed by code generators |

**Layer 2 — Dynamic pattern matching:**

```python
_DEFAULT_DYNAMIC_PATTERNS = (
    "*Plugin",       # Plugin discovery systems
    "*Handler",      # Event handler registration
    "*Adapter",      # Adapter patterns
    "*Middleware",    # Middleware chains
    "*Mixin",        # Mixin classes
    "*Command",      # CLI/management commands
    "register_*",    # Registration functions
    "on_*",          # Event callbacks
    "*_view",        # Django/Flask views
    "*_endpoint",    # API endpoints
    "*_route",       # Route handlers
    "*_callback",    # Callback functions
    "*_signal",      # Signal handlers
    "*_task",        # Background tasks
)
```

Files matching these patterns aren't marked `safe_to_delete` even if confidence is high, because they're likely loaded dynamically.

**Layer 2b — Framework decorator awareness:**

Functions decorated with framework-specific route/endpoint decorators are never flagged as dead code:

- Flask: `@app.route`, `@blueprint.route`, `@app.before_request`, `@app.errorhandler`, etc.
- FastAPI: `@app.get`, `@app.post`, `@router.get`, `@app.on_event`, `@app.middleware`, etc.
- Django: `@admin.register`, `@receiver`, `@login_required`, etc.

**Layer 2c — Dynamic import detection:**

Files in the same package as `importlib.import_module()` or `__import__()` calls automatically receive a reduced confidence score (capped at 0.4), since they may be loaded dynamically at runtime.

**Layer 3 — Confidence scoring with git metadata:**

This is where it gets interesting. A file with zero importers might be dead, or it might be actively used via dynamic loading. Git history helps distinguish:

```
if no_commits_in_90_days AND file_older_than_180_days:
    confidence = 1.0     # Almost certainly dead
    reasoning: Nobody imports it AND nobody has touched it in 6 months

elif no_commits_in_90_days:
    confidence = 0.7     # Probably dead
    reasoning: Nobody imports it AND no recent activity, but file isn't ancient

else:  # has recent commits
    confidence = 0.4     # Suspicious but uncertain
    reasoning: Nobody imports it BUT someone is actively changing it
              (maybe dynamically loaded, maybe a script run manually)
```

**Intuition:** If a file is truly dead, it stops receiving commits. Active files — even dynamically loaded ones — still get bug fixes and updates. The combination of in_degree=0 (structural signal) and no-recent-commits (behavioral signal) gives high confidence.

**safe_to_delete computation:**

```
safe_to_delete = (confidence >= 0.7) AND (not matches_dynamic_patterns)
```

A file is only marked safe to delete if we're confident it's dead AND it doesn't look like a plugin/handler/adapter that might be dynamically loaded.

#### Strategy 2: Unused Exports

**Question:** "Is this public function/class imported by anything?"

This is more granular than unreachable files. A file might be imported, but specific exports within it might be unused.

**Algorithm:**

```
For each file in the graph:
    Skip if: external, non-code, test, fixture, never-flag pattern

    For each PUBLIC symbol in the file:
        Skip if: has framework decorator (pytest.fixture, pytest.mark)
        Skip if: matches dynamic pattern (*Handler, register_*, etc.)

        has_importers = False
        For each file that imports this file (predecessors):
            Check edge's imported_names list
            if symbol_name in imported_names OR "*" in imported_names:
                has_importers = True
                break

        if not has_importers:
            → This export is unused
```

**Edge data is key here.** Each edge in the graph stores `imported_names` — the specific names imported across that edge. For example:

```python
# In service.py:
from auth import login, validate_token

# Edge: service.py → auth.py, imported_names = ["login", "validate_token"]
```

If `auth.py` also exports `reset_password` but no edge's `imported_names` includes it, then `reset_password` is an unused export.

**Confidence scoring for unused exports:**

```
if symbol_name ends with _DEPRECATED, _LEGACY, or _COMPAT:
    confidence = 0.3     # Already marked as legacy by developer

elif file_has_other_importers:  # file is used, but this symbol isn't
    confidence = 1.0     # Very suspicious — file is active but this export isn't

else:  # file itself has no importers
    confidence = 0.7     # File and symbol both unused
```

**Why the file-imported distinction matters:**

If `auth.py` is imported by 10 files but none of them import `reset_password`, that's a strong signal — developers actively use this file but skip this function. Confidence = 1.0.

If `auth.py` itself has zero importers, the unused export is less interesting — the whole file is dead, and the unused export is just a consequence. Confidence = 0.7.

**safe_to_delete for exports:**

```
safe = (confidence >= 0.7) AND (complexity_estimate < 5)
```

Low-complexity symbols (simple functions, constants) are safer to remove than complex ones that might have non-obvious side effects.

#### Strategy 3: Unused Internals

**Question:** "Is this private function called within its own file?"

**Status: Not implemented** (returns empty list). The comment says "Higher false positive rate — off by default."

**Why it's hard:** Private functions might be called via string dispatch, decorators, metaclasses, or closures that the AST parser doesn't trace. False positives here are more disruptive than for public symbols because developers expect private functions to be internal and are less likely to question the analyzer.

#### Strategy 4: Zombie Packages

**Question:** "Does any other package in this monorepo actually use this package?"

**Algorithm:**

```
# Group files by top-level directory (= package)
packages = group_by(all_files, first_path_segment)

# Only applies to monorepos (2+ packages)
if len(packages) < 2: return []

For each package:
    has_external_importers = False
    For each file in this package:
        For each predecessor (file importing this one):
            if predecessor is from a DIFFERENT package:
                has_external_importers = True
                break

    if not has_external_importers:
        → This package is a zombie (nothing outside it uses it)
```

**Example:**

```
packages/
├── auth/          # imported by api/ and cli/
│   ├── login.py
│   └── jwt.py
├── api/           # imported by cli/
│   └── routes.py
├── cli/           # entry point, imports auth/ and api/
│   └── main.py
└── legacy-reports/ # NOTHING outside this package imports it
    ├── generator.py
    └── formatter.py
```

`legacy-reports/` is a zombie package. Its files might import each other internally, but no other package depends on it.

**Confidence:** Always 0.5 (medium). Packages might be used as standalone entry points, scripts, or tooling that the dependency graph doesn't capture.

**safe_to_delete:** Always `False`. Deleting an entire package is too risky for automatic recommendation.

### How findings flow to the user

```
DeadCodeAnalyzer.analyze()
    │
    ├── _detect_unreachable_files()    → findings with confidence + safe_to_delete
    ├── _detect_unused_exports()       → findings with confidence + safe_to_delete
    ├── _detect_zombie_packages()      → findings at 0.5 confidence, never safe
    │
    ▼ apply min_confidence filter (default 0.4)
    │
    ▼ persist to dead_code_findings table
    │
    ├── REST API: /api/dead-code (filter by kind, confidence, status)
    ├── MCP Tool: get_dead_code (grouped into tiers: high/medium/low)
    └── Web UI: tabbed view with bulk resolve/acknowledge/false-positive
```

**The MCP tool groups findings into three action tiers:**

| Tier | Confidence | Action |
|------|-----------|--------|
| **High** (>= 0.8) | Almost certainly dead | Start here. Safe quick wins. |
| **Medium** (0.5 - 0.8) | Probably dead | Review with team before deleting. |
| **Low** (< 0.5) | Suspicious | Investigate — might be dynamic loading. |

### Incremental dead code analysis

When files change (via `repowise update`), full re-analysis is wasteful. `analyze_partial()` only checks affected files:

```python
def analyze_partial(affected_files, config):
    for node in affected_files:
        if node not in graph: continue
        if in_degree(node) == 0 and not entry_point and not test:
            → Check if this file became unreachable due to the change
```

This is O(affected_files) instead of O(all_files). Only detects newly-unreachable files — it doesn't re-check unused exports or zombie packages (those need the full graph).

---

## 2. Decision Records (ADR) System

### What problem does it solve?

Code tells you **what** the system does. Comments sometimes tell you **how**. But almost nothing tells you **why** — why was this approach chosen over alternatives? What constraints forced this design? What was the tradeoff?

When those decisions live only in someone's head or a forgotten Slack thread, every new developer has to reverse-engineer the reasoning. Worse, they might unknowingly undo a deliberate tradeoff, reintroducing a problem that was already solved.

Repowise's decision system **automatically discovers architectural decisions** from four sources, stores them as structured records, tracks their staleness as code evolves, and surfaces them when developers need context.

### How decisions are discovered

#### Source 1: Inline Markers (confidence 0.95)

Developers sometimes leave breadcrumbs in code:

```python
# WHY: We use bcrypt instead of argon2 because our deployment target
# doesn't have the argon2 C bindings available.
password_hash = bcrypt.hashpw(password, bcrypt.gensalt())

# DECISION: Rate limiting is done at the application layer, not the
# load balancer, because we need per-user limits, not per-IP.
```

The extractor scans source files for regex markers:

```
# WHY: ...
# DECISION: ...
# TRADEOFF: ...
# ADR: ...
# RATIONALE: ...
# REJECTED: ...
```

When found, it captures a ±20-line context window around the marker and sends it to the LLM for structuring into a decision record (title, context, decision, rationale, alternatives, consequences).

**Why 0.95 confidence?** The developer explicitly wrote a decision marker. The intent is unambiguous. 0.95 instead of 1.0 because the LLM structuring might misinterpret the context.

#### Source 2: Git Archaeology (confidence 0.70-0.85)

Most decisions aren't marked in code. They're implicit in commit messages:

```
commit abc123: "Migrate from REST to GraphQL for the admin API — reduces
round trips from 12 to 3 for the dashboard view"

commit def456: "Switch from moment.js to date-fns — moment is 300KB,
date-fns is 15KB with tree-shaking"
```

The extractor scores commits by **decision signal keywords**:

```
"migrate", "switch to", "replace", "refactor to", "deprecate",
"remove", "adopt", "introduce", "upgrade", "rewrite", "extract",
"split", "convert", "transition", "revert"
```

Commits with these keywords are batched (groups of 5) and sent to the LLM to identify which ones represent actual architectural decisions vs routine changes.

**Why variable confidence (0.70-0.85)?** Git commits are noisier than inline markers. A commit saying "migrate database" could be a major architectural decision or just a routine migration script. The LLM assesses this and assigns confidence.

#### Source 3: README Mining (confidence 0.60)

Documentation files often contain architectural rationale:

```markdown
## Architecture

We use a message queue between the API and worker services because...

### Why SQLite?

For single-tenant deployments, PostgreSQL is overkill. SQLite gives us...
```

The extractor processes README.md, ARCHITECTURE.md, CONTRIBUTING.md, DESIGN.md, DECISIONS.md, and `docs/*.md` (up to 10 files, 50KB each).

**Why 0.60 confidence?** README content is often aspirational or outdated. It describes what the code *should* be, not necessarily what it *is* today. Lower confidence reflects this uncertainty.

#### Source 4: CLI Capture (confidence 1.0)

```bash
repowise decision add
```

Interactive prompt for manual entry. The developer directly states the decision — no extraction uncertainty.

**All four sources run in parallel** via `asyncio.gather()`. If one source fails (e.g., LLM timeout during git archaeology), the others still complete.

### Decision data model

Each decision record stores:

```
DecisionRecord
├── title: "Migrate admin API from REST to GraphQL"
├── status: "active" | "proposed" | "deprecated" | "superseded"
├── context: "Dashboard required 12 API calls to render..."
├── decision: "Use GraphQL for the admin API"
├── rationale: "Reduces round trips from 12 to 3..."
├── alternatives: ["Keep REST with batching", "Use gRPC"]
├── consequences: ["Need GraphQL schema maintenance", "Client complexity increases"]
├── affected_files: ["src/admin/schema.py", "src/admin/resolvers.py"]
├── affected_modules: ["admin"]
├── tags: ["api", "performance"]
├── source: "git_archaeology"
├── evidence_file: "src/admin/schema.py"
├── evidence_commits: ["abc123"]
├── confidence: 0.80
├── staleness_score: 0.15
└── superseded_by: null
```

**Deduplication key:** `(repository_id, title, source, evidence_file)`. The same decision discovered from two sources (e.g., inline marker + readme mention) creates separate records intentionally — this preserves provenance and lets you see where each piece of evidence came from.

### Staleness computation

Decisions go stale when the code they govern changes but the decision itself doesn't get updated. The staleness algorithm detects this drift.

**Per-file score:**

For each file in the decision's `affected_files`:

```
if file has no git metadata:
    file_score = 1.0    (can't verify → assume stale)

elif file's last commit is BEFORE the decision was created:
    file_score = 0.0    (file hasn't changed since decision was made → still fresh)

else:  # file changed AFTER the decision
    base = min(1.0,  (commit_count_90d / 15) × 0.7
                    + (age_days / 365) × 0.3)

    conflict_boost = 0.0
    For each significant commit AFTER the decision:
        if commit message contains conflict keywords:
            ("replace", "remove", "deprecate", "migrate away",
             "drop", "revert", "undo", "disable", "eliminate")
            AND shares 2+ meaningful words with decision text:
                conflict_boost = 0.3

    file_score = min(1.0, base + conflict_boost)
```

**Breaking down the base score:**

- **70% weight on recent activity:** `commit_count_90d / 15`. If 15+ commits in 90 days, this maxes out at 1.0. Files with heavy churn since the decision was made are likely to have drifted from the original intent.

- **30% weight on age:** `age_days / 365`. Decisions older than a year get a staleness penalty simply because codebases evolve. Even without heavy churn, a year-old decision might not reflect current reality.

**The conflict boost:**

The most interesting part. If a commit message after the decision contains words like "replace", "remove", "deprecate" AND shares meaningful words with the decision text itself, that's a strong signal that someone is actively working against the decision.

**Example:**

```
Decision: "Use bcrypt for password hashing" (created 2025-06-01)
Commit (2026-01-15): "Replace bcrypt with argon2 for password hashing"

The commit contains "replace" (conflict keyword) and shares "bcrypt",
"password", "hashing" with the decision text.
→ conflict_boost = 0.3
→ This decision is likely stale (someone replaced what it decided)
```

**Aggregate score:** Average across all affected files, rounded to 3 decimals.

**Interpretation:**
- **0.0 - 0.3:** Fresh. Code hasn't materially changed since the decision.
- **0.3 - 0.5:** Moderate. Some drift, worth a review.
- **0.5 - 1.0:** Stale. High churn and/or explicit contradictory commits.

### Ungoverned hotspot detection

**Question:** "Which files change a lot but have no documented decisions explaining why?"

```
hotspot_files = files where churn_percentile >= 0.75 AND commit_count_90d > 0

governed_files = union of all affected_files across active decisions

ungoverned_hotspots = hotspot_files - governed_files
```

These are the most dangerous files in the codebase: they change frequently (risky) and nobody has documented why they're designed the way they are (opaque). New developers are most likely to introduce bugs here.

### Alignment scoring

When you query `get_why("src/auth/login.py")`, the system computes an **alignment score** — how well-governed is this file?

**Algorithm:**

```
1. Find all decisions governing this file
   (file in affected_files OR module in affected_modules)

2. If no decisions → score = "none"
   "This file is ungoverned — no documented rationale."

3. Count statuses:
   active_count = decisions with status "active"
   deprecated_count = decisions with status "deprecated" or "superseded"
   stale_count = decisions with staleness_score > 0.5
   proposed_count = decisions with status "proposed"

4. Compute sibling coverage:
   sibling_files = other files in the same directory
   sibling_decisions = decisions governing siblings
   coverage = |shared_decisions| / |sibling_decisions|

5. Score decision tree:
   ┌─ All deprecated, no active → "low" (technical debt)
   ├─ >= 50% stale             → "low" (rationale may be invalid)
   ├─ Has active + sibling_coverage >= 0.5 → "high" (well-governed)
   ├─ Has active + sibling_coverage < 0.5  → "medium" (unique pattern)
   ├─ Has active, no siblings  → "high"
   ├─ Only proposed            → "medium" (unreviewed)
   └─ Mixed                    → "medium"
```

**Why sibling coverage matters:**

If `auth/login.py` is governed by a decision about "Use JWT for authentication" and its sibling `auth/jwt.py` is also governed by the same decision, that's a well-structured module where files share consistent architectural direction. Coverage >= 50% → "high" alignment.

If `auth/login.py` has a unique decision that no sibling shares, it might be an outlier — the decision applies narrowly, or the file doesn't fit the module's pattern. Coverage < 50% → "medium."

### Origin story

When you look up a file's decisions, the system also builds an **origin story** — a narrative reconstruction of how this file came to be:

```
Origin Story for src/auth/login.py:

Created:     2024-03-15 (732 days ago)
Created by:  Alice Chen (47% of commits)
Last change: 2026-03-20 by Bob Kim
Commits:     89 total, 12 in last 90 days

Key commits:
  - abc123 (2024-03-15): "Initial auth module with JWT" → [Alice]
  - def456 (2024-08-22): "Migrate from session cookies to JWT" → [Alice]
  - ghi789 (2025-11-03): "Add MFA support to login flow" → [Bob]

Linked decisions:
  - "Use JWT for authentication" (active, confidence 0.85)
    Evidence commits: abc123, def456 (messages share "JWT" keyword)
  - "Add multi-factor authentication" (active, confidence 0.75)
    Evidence commits: ghi789 (message shares "MFA" keyword)
```

**Commit-decision linkage** works by keyword overlap: if a commit message shares 2+ meaningful words (after removing stop words) with a decision's text, they're linked as evidence.

### Decision lifecycle

```
proposed → active → deprecated
             ↓           ↓
          superseded ← superseded_by link

CLI commands:
  repowise decision add        → creates with status "active"
  repowise decision confirm    → proposed → active
  repowise decision deprecate  → sets status "deprecated"
  repowise decision dismiss    → deletes a proposed decision
  repowise decision health     → shows stale, ungoverned, proposed
```

---

## 3. Search and Vector Store Internals

### The problem with keyword search

If you search for "authentication" with keyword matching, you find pages containing the word "authentication." But you miss pages about "login flow", "credential validation", or "session management" — concepts that are semantically identical but use different words.

Vector search solves this by comparing **meaning**, not characters.

### How vector search works — from text to numbers

**Step 1: Embedding.** Convert text to a vector (list of numbers):

```
"authentication module"  →  [0.12, -0.45, 0.78, ..., 0.03]  (1536 numbers)
"login credential check" →  [0.11, -0.43, 0.80, ..., 0.05]  (1536 numbers)
"database connection"    →  [0.67, 0.22, -0.15, ..., 0.91]  (1536 numbers)
```

The embedding model (OpenAI, Gemini, or mock) maps semantically similar text to nearby vectors. "Authentication" and "login" end up close together. "Database connection" ends up far away.

**Step 2: Normalize.** All vectors are L2-normalized to unit length:

```
normalized = vector / ||vector||

where ||vector|| = sqrt(v[0]² + v[1]² + ... + v[n]²)
```

After normalization, every vector has length 1.0. This is crucial because it makes cosine similarity equal to the dot product, which is cheaper to compute:

```
cosine_similarity(a, b) = (a · b) / (||a|| × ||b||)

If ||a|| = 1 and ||b|| = 1, then:
cosine_similarity(a, b) = a · b = Σ(a[i] × b[i])
```

**Step 3: Store.** Save each vector alongside its page_id and metadata.

**Step 4: Search.** Embed the query, compute similarity against all stored vectors, return top-k.

### The three vector store implementations

#### InMemoryVectorStore

Simplest implementation. Stores vectors in a Python dict:

```python
_store: dict[page_id] → (vector, metadata)
```

Search computes cosine similarity against every vector:

```python
def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if (norm_a * norm_b) > 0 else 0.0
```

**Time complexity:** O(N × D) per search, where N = number of pages, D = vector dimensions.

For 55 pages with 1536 dimensions, this is ~84,000 multiplications per search — trivial. For 100,000 pages, you'd want something smarter. That's where LanceDB comes in.

#### LanceDBVectorStore

Embedded vector database stored as local files in `.repowise/lancedb/`. Uses Apache Arrow columnar format with an IVF-PQ (Inverted File Index with Product Quantization) index for fast approximate nearest neighbor search.

**Schema:**

```
page_id:         string
vector:          list<float32>[dim]
title:           string
page_type:       string
target_path:     string
content_snippet: string (first 200 chars)
```

**Upsert strategy:**

```python
# LanceDB 0.12+: atomic merge_insert
table.merge_insert("page_id")
    .when_matched_update_all()      # existing page → update vector + metadata
    .when_not_matched_insert_all()  # new page → insert
    .execute([row])

# Fallback for older LanceDB: two operations
table.delete(f"page_id = '{safe_id}'")    # delete old
table.add([row])                           # insert new
```

**Why merge_insert matters:** Without it, there's a window between delete and add where the page doesn't exist. If a search runs during that window, it misses the page. merge_insert is atomic — the old and new versions are swapped in one operation.

#### PgVectorStore

Uses PostgreSQL's pgvector extension. Stores embeddings directly in the `wiki_pages` table:

```sql
-- Upsert
UPDATE wiki_pages SET embedding = CAST('[0.12,-0.45,...]' AS vector) WHERE id = 'page_id';

-- Search (cosine distance operator <=>)
SELECT id, title, content, page_type, target_path,
       1 - (embedding <=> CAST('[0.11,-0.43,...]' AS vector)) AS score
FROM wiki_pages
WHERE embedding IS NOT NULL
ORDER BY embedding <=> CAST('[0.11,-0.43,...]' AS vector)
LIMIT 10;
```

**The `<=>` operator** computes cosine distance (0 = identical, 2 = opposite). Subtracting from 1 converts to similarity.

**Why raw SQL instead of ORM?** The `embedding` column is a pgvector type, which isn't declared in the SQLAlchemy ORM model. This keeps the models dialect-neutral (they work with both SQLite and PostgreSQL). The pgvector column is added by an Alembic migration that only runs on PostgreSQL.

### Full-text search (FTS)

Vector search is powerful but slow (needs embeddings, API calls). Full-text search is fast and works with exact keywords.

#### SQLite FTS5

**Index creation:**

```sql
CREATE VIRTUAL TABLE page_fts USING fts5(
    page_id UNINDEXED,    -- stored but not searchable
    title,                 -- searchable
    content                -- searchable
);
```

**Query construction:**

The user's query is transformed into an FTS5 MATCH expression:

```
Input: "Python decorator pattern"

Step 1: Tokenize → ["python", "decorator", "pattern"]
Step 2: Remove stop words → ["python", "decorator", "pattern"] (none removed)
Step 3: Add prefix matching → "python"* OR "decorator"* OR "pattern"*
```

The `*` suffix enables prefix matching: `"auth"*` matches "authentication", "authorization", "authoring".

OR between terms gives **broad recall** — a page matching any term is returned. FTS5's built-in BM25 ranking naturally boosts pages matching **more** terms.

**Another example:**

```
Input: "the async await system"

Step 1: Tokenize → ["the", "async", "await", "system"]
Step 2: Remove stop words → ["async", "await", "system"] (removes "the")
Step 3: → "async"* OR "await"* OR "system"*
```

127 stop words are removed (a, an, the, is, are, was, were, be, been, have, has, had, do, does, did, will, would, etc.).

**Edge case:** If all words are stop words (e.g., "a the is"), the query falls back to exact phrase matching: `"a the is"`.

#### PostgreSQL

Uses `to_tsvector('english', ...)` for document representation and `plainto_tsquery('english', ...)` for queries. PostgreSQL handles stemming (running → run), stop word removal, and ranking via `ts_rank()`.

A GIN index makes searches fast without scanning every row.

### Search ranking in the MCP tool

The MCP `search_codebase` tool applies additional ranking on top of raw search scores:

**Step 1: Try semantic search** (vector store, 8-second timeout)

**Step 2: Fallback to FTS** if semantic fails or returns empty

**Step 3: Freshness boost** — recently modified files rank higher:

```
if file has commits in last 30 days: recency = 1.0
elif file has commits in last 90 days: recency = 0.5
else: recency = 0.0

boosted_score = raw_score × (1 + 0.2 × recency)
```

A file modified yesterday gets a 20% boost. A file untouched for a year gets no boost.

**Why boost freshness?** If you're searching for "authentication" and two pages match equally, the one that was recently updated is more likely to be accurate and relevant to current development.

**Step 4: Normalize confidence** relative to the best result:

```
confidence = relevance_score / max_relevance_score
```

The top result gets confidence ≈ 1.0. Other results are proportionally lower. This gives the user a relative quality signal without needing to interpret raw cosine similarity values.

---

## 4. Incremental Updates and Webhooks

### The problem

Initial documentation generation is expensive — potentially hundreds of LLM calls. After that, you want to keep docs fresh as code changes, but regenerating everything on every commit is wasteful and slow.

Repowise's incremental update system regenerates **only what changed** and its dependencies.

### Three triggers

#### Trigger 1: CLI update command

```bash
repowise update
```

Reads `.repowise/state.json` to find the last synced commit, diffs against current HEAD, and regenerates affected pages.

#### Trigger 2: Filesystem watcher

```bash
repowise watch --debounce 2000
```

Uses `watchdog` library to monitor the filesystem. When files change:

1. Add changed paths to a set
2. Start a debounce timer (default 2 seconds)
3. If more changes arrive, reset the timer
4. When the timer fires (filesystem quiet for 2 seconds), run `repowise update`

**Why debounce?** Saving a file in an editor often triggers multiple filesystem events (write, metadata change, backup file creation). Without debouncing, each save would trigger 3-4 redundant update runs. The 2-second quiet period waits until all save-related events settle.

#### Trigger 3: Webhooks

GitHub and GitLab can send POST requests when code is pushed:

**GitHub verification:**

```
Webhook arrives with:
  Body: {...}
  Header: X-Hub-Signature-256: sha256=abc123...

Server computes:
  expected = HMAC-SHA256(secret, body)

Verification:
  hmac.compare_digest(expected, received)  # constant-time comparison
```

`compare_digest` is critical for security — it takes the same time regardless of where the strings differ, preventing timing attacks that could leak the secret byte by byte.

**GitLab verification:**

```
Header: X-Gitlab-Token: my-secret-token
Compare: hmac.compare_digest(expected_token, received_token)
```

Simpler — just a shared token, no HMAC computation.

**After verification:**

```
1. Store raw webhook event in database (audit trail)
2. Find matching repository by URL
3. Create GenerationJob:
   - status: "pending"
   - mode: "incremental"
   - config: {before: "old_commit_sha", after: "new_commit_sha"}
4. Link webhook event to job
```

### The update pipeline

```
1. LOAD STATE
   Read last_sync_commit from .repowise/state.json
   │
2. DIFF
   ChangeDetector.get_changed_files(last_sync_commit, HEAD)
   → List of FileDiff objects (added, deleted, modified, renamed)
   │
3. RE-INGEST
   Re-run FileTraverser + ASTParser + GraphBuilder on entire repo
   (Need fresh graph to compute cascades correctly)
   │
4. RE-INDEX GIT
   GitIndexer.index_changed_files() — only for changed files
   Update churn percentiles, hotspot flags
   │
5. DETECT DECISIONS
   Scan changed files for inline decision markers (WHY:, DECISION:, etc.)
   Update decision staleness scores
   │
6. CASCADE ANALYSIS
   ChangeDetector.get_affected_pages(file_diffs, graph, cascade_budget)
   → regenerate: pages to fully regenerate (budget-limited)
   → rename_patch: pages with symbol renames (text replacement)
   → decay_only: pages to mark stale without regeneration
   │
7. GENERATE
   PageGenerator.generate_all() with only affected files
   │
8. PERSIST
   Upsert pages, git metadata, decisions
   Update FTS index, vector store
   Update CLAUDE.md if enabled
   │
9. SAVE STATE
   Write current HEAD to .repowise/state.json
```

### SSE progress streaming

Long-running jobs report progress via Server-Sent Events:

```
Client: GET /api/jobs/{job_id}/stream
        Accept: text/event-stream

Server: (every 1 second)
        event: progress
        data: {"job_id": "abc", "status": "running", "completed_pages": 12, "total_pages": 55}

        event: progress
        data: {"job_id": "abc", "status": "running", "completed_pages": 25, "total_pages": 55}

        ...

        event: done
        data: {"job_id": "abc", "status": "completed", "completed_pages": 55, "total_pages": 55}
```

The server checks for client disconnection each iteration and stops streaming when the client goes away. Headers include `Cache-Control: no-cache` and `X-Accel-Buffering: no` (prevents nginx from buffering the stream).

### Scheduler polling fallback

Webhooks can fail (network issues, misconfiguration, GitHub outages). The scheduler runs two background jobs every 15 minutes:

**Job 1: Staleness checker** — finds stale/expired pages across all repos and logs them.

**Job 2: Polling fallback** — for each local repo, compares the stored HEAD commit against actual `git rev-parse HEAD`. If they differ, a webhook was missed. (Currently logs only; full auto-sync is future work.)

---

## 5. Change Cascade Algorithm

### The problem

Changing `utils.py` doesn't just affect `utils.py`'s documentation. Every file that imports `utils.py` might now have incorrect documentation too — it might reference old function names, outdated behavior, or changed signatures.

But propagating changes through the entire dependency graph could trigger hundreds of regenerations. You need a smart cascade with a budget.

### The algorithm

```
Input:
  file_diffs: list of changed files with their old/new parsed versions
  graph: dependency graph (directed, import edges)
  cascade_budget: max pages to fully regenerate (adaptive, hard cap 50)

Output:
  regenerate: set of page IDs for full LLM regeneration
  rename_patch: set of page IDs needing text replacement (for renames)
  decay_only: set of page IDs to mark stale without regeneration
```

**Step 1: Direct changes**

```
directly_changed = {file.path for file in file_diffs}
```

These always get regenerated (they changed, their docs are definitely wrong).

**Step 2: 1-hop cascade (reverse dependencies)**

```
one_hop = set()
for file in directly_changed:
    for predecessor in graph.predecessors(file):
        # predecessor imports file → predecessor's docs may reference file
        one_hop.add(predecessor)
one_hop -= directly_changed  # don't double-count
```

**Example:**

```
auth.py changed
graph.predecessors("auth.py") = ["main.py", "middleware.py", "api.py"]

one_hop = {"main.py", "middleware.py", "api.py"}
```

These files import `auth.py`. If `auth.py` renamed a function, their documentation might reference the old name.

**Step 3: Symbol rename detection**

For each changed file, compare old and new parsed versions to detect renames:

```
Old file symbols: [calculate, Config, validate]
New file symbols: [compute, Config, validate]

"calculate" removed, "compute" added, both are functions
Name similarity = SequenceMatcher("calculate", "compute").ratio() = 0.63
Line proximity = start lines within ±5 → bonus = 0.2
Combined = 0.63 + 0.2 = 0.83 (above threshold 0.65)

→ Detected rename: calculate → compute
```

Files referencing the old name need a text patch (string replacement in the existing doc) rather than a full regeneration.

**Step 4: Co-change decay**

```
co_change = set()
for file in directly_changed:
    for partner in graph edges with edge_type="co_changes":
        if partner is file's co-change partner:
            co_change.add(partner)
co_change -= directly_changed
co_change -= one_hop
```

Files that frequently change alongside the modified files are marked for decay — their docs might be stale but the evidence is weaker (correlation, not causation).

**Step 5: 2-hop weak cascade (for renames only)**

```
two_hop = set()
for file in one_hop:
    if file has symbol renames:
        for predecessor in graph.predecessors(file):
            two_hop.add(predecessor)
two_hop -= directly_changed
two_hop -= one_hop
```

If a rename propagated to a 1-hop file, files importing that 1-hop file might also need updating. But this is speculative — marked for decay only.

**Step 6: Budget application**

```
candidates = directly_changed ∪ one_hop
sorted_by_pagerank = sort(candidates, key=pagerank, descending)

regenerate = sorted_by_pagerank[:cascade_budget]
decay_only = sorted_by_pagerank[cascade_budget:] ∪ two_hop ∪ co_change
```

**Why sort by PageRank?** If the budget is 50 and there are 80 candidates, you want to regenerate the 50 most important files first. A high-PageRank file is imported by many others — if its documentation is wrong, the error propagates further. Low-PageRank leaf files can wait.

**Adaptive budget:** The budget is no longer fixed. `compute_adaptive_budget()` scales it based on change magnitude:

| Files changed | Budget |
|---------------|--------|
| 0 | 0 |
| 1 | 10 |
| 2-5 | 30 |
| 6+ | min(n × 3, 50) |

Hard cap at 50. Users can override with `--cascade-budget N`.

### Worked example

```
Codebase: 200 files, adaptive cascade_budget = 30 (3 files changed)

Developer changes: config.py

graph.predecessors("config.py") = [
    "auth.py", "api.py", "db.py", "cache.py", "logger.py",
    "middleware.py", "scheduler.py", "worker.py", "mailer.py",
    "validator.py", "serializer.py", "router.py"
]  # 12 files

directly_changed = {"config.py"}                       # 1 file
one_hop = {12 files above}                             # 12 files
candidates = 1 + 12 = 13 files  (under budget of 30)

config.py renamed: LOG_LEVEL → LOGGING_LEVEL
→ rename_patch candidates: files importing LOG_LEVEL

co_change partners of config.py: ["docker-compose.yml", ".env.example"]
→ decay_only

Result:
  regenerate: 13 pages (all fit within budget)
  rename_patch: pages referencing LOG_LEVEL
  decay_only: docker-compose.yml, .env.example docs
```

Now imagine `config.py` is imported by 80 files:

```
candidates = 1 + 80 = 81 files  (over budget of 30)

Sort by PageRank:
  Top 30: main.py, auth.py, api.py, ... (highest PageRank)
  Remaining 51: leaf files, utilities, tests

Result:
  regenerate: 30 pages (budget-limited, most important first)
  decay_only: 51 pages (confidence decayed, regenerated on next run)
```

The 51 files that didn't make the cut have their confidence score reduced. They'll show as "stale" in the UI and be prioritized for regeneration on the next update.

### Confidence decay

Pages in `decay_only` don't get regenerated but their freshness score changes:

```
confidence decays linearly:
  1.0 at generation time → 0.0 after expiry_threshold_days (default 30)

freshness_status:
  "fresh" if hash matches AND age < 7 days
  "stale" if hash changed OR age >= 7 days
  "expired" if age >= 30 days (forces regeneration on next run)
```

This creates a natural queue: pages that got bumped from the cascade budget eventually hit "expired" status and get regenerated in a future update cycle, ensuring nothing stays stale forever.
