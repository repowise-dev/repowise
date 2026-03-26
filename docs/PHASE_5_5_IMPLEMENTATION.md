# Phase 5.5 — Git Intelligence + Dead Code Detection: Implementation Prompt

This document contains everything needed to implement Phase 5.5 of repowise.
Read the full document before writing any code.

**Context:** Phases 1-5 are complete and passing (473 tests, 11.48s). Phase 5.5 is
a standalone feature detour before Phase 6 (Server). All additions are additive —
do not modify any existing Phase 1-5 code, only extend it.

**Important files to read first:**
- `docs/ARCHITECTURE.md` — sections 7 (Git Intelligence) and 8 (Dead Code Detection)
- `BUILD_STATUS.md` — Phase 5.5 step breakdown
- `PLAN.md` — full project plan with git/dead-code integrated

---

## Codebase Layout (What Already Exists)

```
packages/core/src/repowise/core/
├── generation/
│   ├── context_assembler.py    # ContextAssembler class
│   ├── page_generator.py       # PageGenerator class (8-level pipeline)
│   ├── models.py               # GeneratedPage, GenerationConfig, confidence logic
│   ├── job_system.py           # JobSystem + Checkpoint
│   └── templates/              # 9 Jinja2 .j2 templates
├── ingestion/
│   ├── graph.py                # GraphBuilder (NetworkX)
│   ├── change_detector.py      # ChangeDetector, FileDiff, AffectedPages
│   ├── models.py               # ParsedFile, Symbol, FileInfo, RepoStructure
│   ├── parser.py               # ASTParser
│   └── traverser.py            # FileTraverser (extra_exclude_patterns + per-dir .repowiseIgnore)
├── persistence/
│   ├── models.py               # SQLAlchemy ORM: Repository, Page, GraphNode, etc.
│   ├── database.py             # Engine/session creation
│   ├── crud.py                 # Async CRUD operations
│   ├── search.py               # Full-text search
│   └── vector_store.py         # Vector storage
└── providers/                  # LLM providers (unchanged)

packages/cli/src/repowise/cli/
├── commands/
│   ├── init_cmd.py             # repowise init pipeline
│   ├── update_cmd.py           # repowise update pipeline
│   └── ...
├── helpers.py
├── cost_estimator.py
└── main.py                     # Click group + command registrations
```

---

## Existing Key Interfaces (Exact Signatures)

### persistence/models.py

```python
# Imports
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Helper functions used throughout
def _new_uuid() -> str: return uuid4().hex
def _now_utc() -> datetime: return datetime.now(timezone.utc)

class Base(DeclarativeBase): pass

# Existing tables: Repository, GenerationJob, Page, PageVersion, GraphNode,
# GraphEdge, WebhookEvent, WikiSymbol (all use Mapped[] type hints)
```

### generation/models.py

```python
from dataclasses import dataclass, field
from typing import Literal

PageType = Literal["api_contract", "symbol_spotlight", "file_page", "scc_page",
                   "module_page", "cross_package", "repo_overview",
                   "architecture_diagram", "infra_page", "diff_summary"]
FreshnessStatus = Literal["fresh", "stale", "expired", "unknown"]

@dataclass(frozen=True)
class GenerationConfig:
    max_tokens: int = 4096
    temperature: float = 0.3
    token_budget: int = 8000
    max_concurrency: int = 5
    cache_enabled: bool = True
    staleness_threshold_days: int = 7
    expiry_threshold_days: int = 30
    top_symbol_percentile: float = 0.10
    file_page_top_percentile: float = 0.20
    file_page_min_symbols: int = 1
    jobs_dir: str = ".repowise/jobs"

# Confidence: linear decay based on days since update
def compute_freshness(...) -> str: ...
def decay_confidence(...) -> ConfidenceDecayResult: ...
```

### generation/context_assembler.py

```python
@dataclass
class FilePageContext:
    file_path: str
    language: str
    docstring: str | None
    symbols: list[dict[str, Any]]
    imports: list[str]
    exports: list[str]
    file_source_snippet: str
    pagerank_score: float
    betweenness_score: float
    community_id: int
    dependents: list[str]
    dependencies: list[str]
    is_api_contract: bool
    is_entry_point: bool
    is_test: bool
    parse_errors: list[str]
    estimated_tokens: int
    rag_context: list[str] = field(default_factory=list)

class ContextAssembler:
    def __init__(self, config: GenerationConfig) -> None: ...
    def assemble_file_page(self, parsed, graph, pagerank, betweenness, community, source_bytes) -> FilePageContext: ...
    def assemble_module_page(self, module_path, language, file_contexts, graph) -> ModulePageContext: ...
    def assemble_repo_overview(self, repo_structure, pagerank, sccs, community) -> RepoOverviewContext: ...
    def assemble_diff_summary(self, file_diffs, affected_pages, from_ref, to_ref) -> DiffSummaryContext: ...
    # ... other assemble_* methods
```

### generation/page_generator.py

```python
class PageGenerator:
    def __init__(self, provider, assembler, config, jinja_env=None): ...

    async def generate_all(self, parsed_files, source_map, graph_builder, repo_structure, repo_name, job_system=None) -> list[GeneratedPage]:
        # Levels 0-7 run sequentially. Within each level:
        # - Selects files/symbols for that level
        # - Uses asyncio.Semaphore(config.max_concurrency)
        # - Level 2 (file pages): filters by _is_significant_file(), groups by module
        ...
```

### ingestion/graph.py

```python
class GraphBuilder:
    def __init__(self) -> None: ...
    def add_file(self, parsed: ParsedFile) -> None: ...
    def build(self) -> nx.DiGraph: ...
    def graph(self) -> nx.DiGraph: ...
    def pagerank(self, alpha=0.85) -> dict[str, float]: ...
    def betweenness_centrality(self) -> dict[str, float]: ...
    def strongly_connected_components(self) -> list[frozenset[str]]: ...
    def community_detection(self) -> dict[str, int]: ...
    # Node attrs: language, symbol_count, has_error, is_test, is_entry_point, pagerank, betweenness, community_id
    # Edge attrs: imported_names (list[str])
```

### ingestion/change_detector.py

```python
@dataclass
class FileDiff:
    path: str
    status: Literal["added", "deleted", "modified", "renamed"]
    old_path: str | None
    old_parsed: ParsedFile | None
    new_parsed: ParsedFile | None
    symbol_diff: SymbolDiff | None

@dataclass
class AffectedPages:
    regenerate: list[str]
    rename_patch: list[str]
    decay_only: list[str]

class ChangeDetector:
    def __init__(self, repo_path: Path) -> None: ...
    def get_changed_files(self, base_ref="HEAD~1", until_ref="HEAD") -> list[FileDiff]: ...
    def get_affected_pages(self, file_diffs, graph, cascade_budget=30) -> AffectedPages: ...
```

### persistence/crud.py

```python
# Key existing functions:
async def upsert_repository(session, *, name, local_path, ...) -> Repository
async def upsert_page_from_generated(session, generated_page, repository_id) -> Page
async def batch_upsert_graph_nodes(session, repository_id, nodes) -> None
async def batch_upsert_graph_edges(session, repository_id, edges) -> None
async def batch_upsert_symbols(session, repository_id, symbols) -> None
async def get_page(session, page_id) -> Page | None
async def list_pages(session, repository_id, ...) -> list[Page]

_BATCH_SIZE = 500  # Used for all batch operations
```

### CLI: init_cmd.py pipeline (abbreviated)

```python
# Pipeline order:
# 1. Resolve repo path
# 2. Ensure .repowise dir
# 2.5 Load config; merge exclude_patterns from config + --exclude/-x flags
# 3. Resolve LLM provider
# 4. FileTraverser(extra_exclude_patterns=...) → list[FileInfo]
# 5. ASTParser → list[ParsedFile]
# 6. GraphBuilder → build graph, compute metrics
# 7. Cost estimate + confirmation
# 8. PageGenerator.generate_all() → list[GeneratedPage]
# 9. Persist pages, graph, symbols to DB
# 10. FTS indexing
# 11. Update state file
```

### CLI: update_cmd.py pipeline (abbreviated)

```python
# Pipeline order:
# 1. Load state, get base_ref
# 2. ChangeDetector.get_changed_files()
# 3. Re-ingest full repo for graph
# 4. ChangeDetector.get_affected_pages()
# 5. PageGenerator.generate_all() for affected files
# 6. Persist + update state
```

---

## What to Build

### New Files to Create

```
packages/core/src/repowise/core/
├── ingestion/
│   └── git_indexer.py               # GitIndexer class
├── analysis/
│   ├── __init__.py
│   └── dead_code.py                 # DeadCodeAnalyzer class

packages/cli/src/repowise/cli/
└── commands/
    └── dead_code_cmd.py             # repowise dead-code CLI command

tests/
├── unit/
│   ├── test_git_indexer.py
│   ├── test_dead_code.py
│   └── test_confidence_with_git.py
└── integration/
    ├── test_git_intelligence_integration.py
    └── test_dead_code_integration.py

tests/fixtures/sample_repo/
├── dead/
│   └── unreachable_module.py        # nothing imports this
└── utils/
    └── helpers.py                   # has unused_helper() export
```

### Existing Files to Modify (additive only)

| File | Change |
|------|--------|
| `persistence/models.py` | Add `GitMetadata` + `DeadCodeFinding` ORM classes |
| `persistence/crud.py` | Add git metadata + dead code CRUD functions |
| `generation/models.py` | Add `GitConfig`, `DeadCodeConfig` dataclasses; extend `GenerationConfig`; add `compute_confidence_decay_with_git()` |
| `generation/context_assembler.py` | Add `git_metadata`, `co_change_pages`, `dead_code_findings` to `FilePageContext`; add `assemble_update_context()` method; add `_select_generation_depth()` method |
| `generation/page_generator.py` | Add `_sort_level_files()` method for hotspot priority ordering |
| `generation/templates/file_page.j2` | Add git context block + dead code block (all `{% if %}` gated) |
| `generation/templates/module_page.j2` | Add ownership summary block |
| `generation/templates/repo_overview.j2` | Add codebase health signals block |
| `generation/templates/diff_summary.j2` | Add trigger commit + diff context |
| `ingestion/graph.py` | Add `add_co_change_edges()` + `update_co_change_edges()` methods |
| `ingestion/change_detector.py` | Extend `FileDiff` with trigger commit fields; include co-change partners in staleness |
| `cli/commands/init_cmd.py` | Add Steps 3.5 (git indexing) and 3.6 (dead code analysis) |
| `cli/commands/update_cmd.py` | Add git re-index + dead code re-analysis |
| `cli/main.py` | Register `dead-code` command |

---

## Detailed Specifications

### 1. GitMetadata ORM Model (`persistence/models.py`)

Add to existing file — do NOT modify existing models:

```python
class GitMetadata(Base):
    __tablename__ = "git_metadata"
    __table_args__ = (
        UniqueConstraint("repository_id", "file_path", name="uq_git_metadata"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)

    # Commit volume
    commit_count_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count_30d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timeline
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ownership
    primary_owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_owner_commit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # JSON fields (stored as Text, parsed/serialized in CRUD layer)
    top_authors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    significant_commits_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    co_change_partners_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Derived signals
    is_hotspot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_stable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    churn_percentile: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc)
```

**Important:** The existing codebase does NOT use SQLAlchemy JSON type — it stores JSON
as Text and parses in the CRUD layer (see `config_json`, `imported_names_json`, etc.).
Follow this same pattern for `top_authors_json`, `significant_commits_json`, and
`co_change_partners_json`.

### 2. DeadCodeFinding ORM Model (`persistence/models.py`)

```python
class DeadCodeFinding(Base):
    __tablename__ = "dead_code_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # unreachable_file, unused_export, etc.
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    symbol_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    commit_count_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    safe_to_delete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    primary_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")  # open, acknowledged, resolved, false_positive
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now_utc)
```

### 3. Alembic Migration

Create a new Alembic migration file. Follow the pattern of the existing
`0001_initial_schema.py` migration. The migration should:
- Create `git_metadata` table with all columns
- Create `dead_code_findings` table with all columns
- Add indexes on `repository_id` for both tables
- Add index on `(repository_id, file_path)` for git_metadata

### 4. CRUD Functions (`persistence/crud.py`)

Add these functions — follow existing patterns (async, use `_BATCH_SIZE = 500`):

```python
# Git metadata CRUD
async def upsert_git_metadata(session, *, repository_id, file_path, **kwargs) -> GitMetadata
async def get_git_metadata(session, repository_id, file_path) -> GitMetadata | None
async def get_git_metadata_bulk(session, repository_id, file_paths: list[str]) -> dict[str, GitMetadata]
async def get_all_git_metadata(session, repository_id) -> dict[str, GitMetadata]
async def upsert_git_metadata_bulk(session, repository_id, metadata_list: list[dict]) -> None

# Dead code CRUD
async def save_dead_code_findings(session, repository_id, findings: list[dict]) -> None
async def get_dead_code_findings(session, repository_id, *, kind=None, min_confidence=0.0, status="open") -> list[DeadCodeFinding]
async def update_dead_code_status(session, finding_id, status, note=None) -> DeadCodeFinding | None
async def get_dead_code_summary(session, repository_id) -> dict
```

### 5. GitIndexer (`ingestion/git_indexer.py`)

```python
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class GitIndexSummary:
    files_indexed: int
    hotspots: int
    stable_files: int
    duration_seconds: float = 0.0


class GitIndexer:
    """
    Mines git history into the git_metadata table.

    Uses gitpython (already a dependency) for git operations.
    Parallelizes per-file git log calls with asyncio.Semaphore(20).

    Non-blocking: if git is unavailable or repo has no history, log a warning
    and return an empty summary. All downstream features degrade gracefully.
    """

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path)

    async def index_repo(self, repo_id: str) -> tuple[GitIndexSummary, list[dict]]:
        """
        Full index of all tracked files. Returns summary + list of metadata dicts
        ready for bulk upsert.

        Steps:
        1. Get list of tracked files from git
        2. Per-file git log (parallelized with Semaphore(20))
        3. Per-file git blame for ownership (if blame_enabled)
        4. Co-change analysis (walk last N commits)
        5. Compute repo-wide percentiles for hotspot detection
        """

    async def index_changed_files(self, changed_file_paths: list[str]) -> list[dict]:
        """
        Incremental update: re-index only changed files.
        Also re-index any file whose co_change_partners include a changed file.
        """

    # Internal methods:
    # _get_tracked_files() -> list[str]
    # _index_file(file_path, repo) -> dict  (runs in executor to avoid blocking)
    # _get_blame_ownership(file_path, repo) -> tuple[name, email, pct]
    # _is_significant_commit(message) -> bool
    # _compute_co_changes(repo, all_files, commit_limit=500) -> dict[str, list[dict]]
    # _compute_percentiles(metadata_list) -> None  (mutates in place)
```

**Key implementation notes:**
- Use `git.Repo(self.repo_path)` from gitpython
- Run `git log` and `git blame` in `asyncio.get_event_loop().run_in_executor()` since
  gitpython is synchronous
- `_is_significant_commit()`: skip prefixes "Merge ", "Bump ", "chore:", "ci:", "style:",
  "build:", "release:", "revert:"; skip authors containing "dependabot", "renovate",
  "github-actions"; skip messages < 20 chars
- Co-change analysis: walk commits, for each commit touching >= 2 tracked files,
  record co-occurrence pairs. Return pairs with count >= `co_change_min_count` (default 3).
  Limit to last `co_change_commit_limit` commits (default 500).
- Percentiles: `churn_percentile = rank / total_files`. `is_hotspot = commit_count_90d > p75
  AND complexity_estimate > p75` (get complexity from GraphNode if available, else use file size).
  `is_stable = commit_count_total > 10 AND commit_count_90d == 0`.

### 6. DeadCodeAnalyzer (`analysis/dead_code.py`)

Pure graph traversal + SQL — no LLM calls. Must complete in < 10 seconds.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

import structlog


class DeadCodeKind(str, Enum):
    UNREACHABLE_FILE = "unreachable_file"
    UNUSED_EXPORT = "unused_export"
    UNUSED_INTERNAL = "unused_internal"
    ZOMBIE_PACKAGE = "zombie_package"


@dataclass
class DeadCodeFindingData:
    kind: DeadCodeKind
    file_path: str
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str
    last_commit_at: datetime | None
    commit_count_90d: int
    lines: int
    package: str | None
    evidence: list[str]
    safe_to_delete: bool
    primary_owner: str | None
    age_days: int | None


@dataclass
class DeadCodeReport:
    repo_id: str
    analyzed_at: datetime
    total_findings: int
    findings: list[DeadCodeFindingData]
    deletable_lines: int
    confidence_summary: dict  # {"high": N, "medium": N, "low": N}


class DeadCodeAnalyzer:
    """
    Detects unreachable files, unused exports, unused internals, and
    zombie packages using the dependency graph and git metadata.

    All analysis is graph traversal + SQL. No LLM calls.
    """

    def __init__(self, graph, git_meta_map: dict | None = None):
        self.graph = graph  # nx.DiGraph
        self.git_meta_map = git_meta_map or {}

    def analyze(self, config: dict | None = None) -> DeadCodeReport:
        """Full analysis. Returns report with all findings."""

    def analyze_partial(self, affected_files: list[str], config: dict | None = None) -> DeadCodeReport:
        """Partial analysis for incremental updates."""
```

**Confidence rules (implement exactly):**

Unreachable files (`graph.in_degree(node) == 0` AND not entry point AND not test AND not config):
- 1.0 if in_degree==0 AND commit_count_90d==0 AND last_commit > 6 months ago
- 0.7 if in_degree==0 AND commit_count_90d==0
- 0.4 if in_degree==0 but recently touched
- `safe_to_delete=True` only if confidence >= 0.7 AND NOT filename matches dynamic patterns

Unused exports (symbol `visibility=="public"` AND no incoming edges):
- 1.0 if symbol has no importers AND file IS imported elsewhere
- 0.7 if symbol has no importers AND file also has no importers
- 0.3 if name matches `*_DEPRECATED`, `*_LEGACY`, `*_COMPAT`
- `safe_to_delete=True` only if confidence >= 0.7 AND complexity_estimate < 5

**Never flag as dead:**
- Files in `__init__.py` / files that are re-export barrels
- `@pytest.fixture`, `@pytest.mark.*` symbols
- Files matching `*migrations*`, `*schema*`, `*seed*`
- `.d.ts` files
- `is_api_contract == True` files
- Files/symbols matching `config.dead_code.dynamic_patterns`
- Files in whitelist

### 7. Template Updates

**file_page.j2** — add at the END of the existing template, BEFORE the closing
instructions. All blocks gated on `{% if %}`:

```jinja2
{# ── Git context block ────────────────────────────────────────── #}
{% if git_metadata %}

{% if git_metadata.primary_owner_name %}
## Ownership
Primary maintainer: **{{ git_metadata.primary_owner_name }}**
({{ (git_metadata.primary_owner_commit_pct * 100) | round | int }}% of lines)
{% if git_metadata.top_authors | length > 1 %}
Also contributed by: {% for a in git_metadata.top_authors[1:3] %}{{ a.name }}{% if not loop.last %}, {% endif %}{% endfor %}
{% endif %}
{% endif %}

{% if git_metadata.significant_commit_messages %}
## Evolution context
This file has been shaped by {{ git_metadata.commit_count_total }} commits
over {{ git_metadata.age_days }} days. Key changes:
{% for commit in git_metadata.significant_commit_messages %}
- **{{ commit.date[:10] }}** — {{ commit.message }} *({{ commit.author }})*
{% endfor %}

Use this history to explain *why* the code is structured as it is.
{% endif %}

{% if git_metadata.is_hotspot %}
IMPORTANT: This file is a HOTSPOT — top 25% for both change frequency and
complexity. Note this prominently. High-risk area for bugs.
{% endif %}

{% if git_metadata.is_stable %}
NOTE: This file is STABLE — {{ git_metadata.commit_count_total }} commits
but unchanged in 90+ days. Emphasize stability and reliability.
{% endif %}

{% if git_metadata.co_change_partners %}
## Hidden coupling (co-change analysis)
These files have no import relationship but frequently change together:
{% for partner in git_metadata.co_change_partners[:3] %}
- `{{ partner.file_path }}` (co-changed {{ partner.co_change_count }} times)
{% endfor %}
Mention this relationship explicitly.
{% endif %}

{% endif %}
{# ── End git context block ────────────────────────────────────── #}

{% if dead_code_findings %}
## Potentially unused code
{% for finding in dead_code_findings %}
- `{{ finding.symbol_name }}` — {{ finding.reason }}
  (confidence: {{ (finding.confidence * 100) | int }}%{% if finding.safe_to_delete %}, safe to remove{% endif %})
{% endfor %}
{% endif %}
```

**module_page.j2** — add at end:
```jinja2
{% if module_git_summary %}
## Team ownership
{% for pkg_owner in module_git_summary.top_owners %}
- **{{ pkg_owner.file_count }} files** primarily maintained by {{ pkg_owner.name }}
{% endfor %}
Most active recently: `{{ module_git_summary.most_active_file }}`
({{ module_git_summary.most_active_commits_90d }} commits in 90 days)
{% endif %}
```

**repo_overview.j2** — add at end:
```jinja2
{% if repo_git_summary %}
## Codebase health signals
- **Hotspots:** {{ repo_git_summary.hotspot_count }} files are high-churn AND high-complexity
- **Stable core:** {{ repo_git_summary.stable_count }} files unchanged in 90+ days
- **Most changed (90d):** {{ repo_git_summary.top_churn_files[:3] | join(', ') }}
- **Oldest file:** `{{ repo_git_summary.oldest_file }}` ({{ repo_git_summary.oldest_file_age_days }} days)
{% endif %}
```

### 8. Context Assembler Extensions

**Extend `FilePageContext`** — add three new fields with defaults:

```python
@dataclass
class FilePageContext:
    # ... all existing fields unchanged ...
    git_metadata: dict | None = None           # NEW
    co_change_pages: list[dict] = field(default_factory=list)  # NEW
    dead_code_findings: list[dict] = field(default_factory=list)  # NEW
```

**Extend `assemble_file_page()`** — add optional `git_meta` and `dead_code_findings`
parameters. If provided, include them in the returned context. If not, the fields
default to None/[].

**Add `_select_generation_depth()` method:**
```python
def _select_generation_depth(self, file, git_meta, config_depth) -> str:
    # Upgrade to "thorough" if: hotspot, >100 commits with >10 in 90d,
    #   >=8 significant commits, or has co-change partners
    # Downgrade to "minimal" if: stable AND pagerank < 0.3 AND commit_count < 5
```

**Add `assemble_update_context()` method** for maintenance regeneration using
the triggering commit + diff instead of full file source.

### 9. Generation Ordering

Add `_sort_level_files()` to `PageGenerator`:

```python
async def _sort_level_files(self, files: list[ParsedFile], git_meta_map: dict) -> list[ParsedFile]:
    """Priority (highest first): entry points, hotspots, high PageRank, high commit count, alphabetical."""
```

Call this in `generate_all()` when processing Level 2 (file pages).

### 10. Confidence Decay Git Modifiers

Add `compute_confidence_decay_with_git()` to `generation/models.py`:

```python
def compute_confidence_decay_with_git(
    base_decay: float,  # e.g. 0.85 for direct
    relationship: str,  # "direct", "1hop", "2hop"
    git_meta: dict | None,
    commit_message: str | None,
) -> float:
    """Apply git modifiers multiplicatively on base decay."""
    # Hotspot: direct *= 0.94, 1hop *= 0.95
    # Stable: direct *= 1.03
    # "rewrite"/"refactor"/"migrate" in message: direct *= 0.71, 1hop *= 0.84
    # "typo"/"lint"/"format" in message: direct *= 1.12
```

### 11. Co-Change Edges in Graph

Add to `GraphBuilder`:

```python
def add_co_change_edges(self, git_meta_map: dict, min_count: int = 3) -> int:
    """Add co_changes edges. Returns count of edges added.
    These DO NOT affect PageRank — filter them out before computing."""

def update_co_change_edges(self, updated_meta: dict, min_count: int = 3) -> None:
    """Remove old co_changes edges for updated files, add new ones."""
```

### 12. Extended FileDiff

Add optional fields to `FileDiff` in `change_detector.py`:

```python
@dataclass
class FileDiff:
    # ... existing fields ...
    trigger_commit_sha: str | None = None       # NEW
    trigger_commit_message: str | None = None   # NEW
    trigger_commit_author: str | None = None    # NEW
    diff_text: str | None = None                # NEW (unified diff, capped at 4K chars)
```

### 13. Config Extensions

Extend `GenerationConfig` in `models.py`. Since it's `frozen=True`, add new config
as separate dataclasses:

```python
@dataclass(frozen=True)
class GitConfig:
    enabled: bool = True
    co_change_commit_limit: int = 500
    co_change_min_count: int = 3
    blame_enabled: bool = True
    prompt_commit_count: int = 10
    depth_auto_upgrade: bool = True

@dataclass(frozen=True)
class DeadCodeConfig:
    enabled: bool = True
    detect_unreachable_files: bool = True
    detect_unused_exports: bool = True
    detect_unused_internals: bool = False
    detect_zombie_packages: bool = True
    min_confidence: float = 0.4
    safe_to_delete_threshold: float = 0.7
    dynamic_patterns: tuple[str, ...] = ("*Plugin", "*Handler", "*Adapter", "*Middleware", "register_*", "on_*")
    analyze_on_update: bool = True
```

Pass these as parameters where needed rather than modifying the frozen `GenerationConfig`.

### 14. Init Pipeline Integration

In `init_cmd.py`, add between graph computation and cost estimation:

```python
# Step 3.5: Git indexing
git_indexer = GitIndexer(repo_path)
git_summary, git_metadata_list = await git_indexer.index_repo(repo_id)
# Persist git metadata to DB
await upsert_git_metadata_bulk(session, repo_id, git_metadata_list)
# Add co-change edges to graph
git_meta_map = {m["file_path"]: m for m in git_metadata_list}
graph_builder.add_co_change_edges(git_meta_map)

# Step 3.6: Dead code analysis
analyzer = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map)
dead_code_report = analyzer.analyze()
await save_dead_code_findings(session, repo_id, dead_code_report.findings)
```

Show progress output:
```
[3.5] Indexing git history...  done  3,247 files · 14 hotspots · 892 stable  (2m 14s)
[3.6] Detecting dead code...   done  7 unreachable files · 23 unused exports (~2,847 lines)
```

### 15. Update Pipeline Integration

In `update_cmd.py`, add after change detection:

```python
# Re-index git metadata for changed files
git_indexer = GitIndexer(repo_path)
updated_meta = await git_indexer.index_changed_files(changed_file_paths)
await upsert_git_metadata_bulk(session, repo_id, updated_meta)
# Update co-change edges
git_meta_map = {m["file_path"]: m for m in updated_meta}
graph_builder.update_co_change_edges(git_meta_map)
```

### 16. CLI: `repowise dead-code` Command

```python
@click.command("dead-code")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.option("--min-confidence", default=0.4, type=float)
@click.option("--safe-only", is_flag=True)
@click.option("--kind", type=click.Choice(["unreachable_file", "unused_export", "unused_internal", "zombie_package"]))
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "md"]))
def dead_code_command(path, min_confidence, safe_only, kind, fmt):
    """Detect dead and unused code."""
```

Register in `main.py` via `cli.add_command(dead_code_command)`.

### 17. Tests

**test_git_indexer.py** (7 tests):
```python
def test_significant_commit_filter()          # merges, bumps, chore filtered out
def test_co_change_detection()                # files changed together >= 3 times detected
def test_hotspot_classification()             # top 25% churn + complexity = is_hotspot
def test_stable_classification()              # >10 commits, 0 in 90d = is_stable
def test_git_unavailable_graceful()           # no history → empty metadata, no crash
def test_co_change_below_threshold_skipped()  # pairs with count < min_count not stored
def test_blame_ownership_computed()           # primary_owner set to dominant author
```

**test_dead_code.py** (12 tests):
```python
def test_unreachable_file_detected()
def test_entry_point_not_flagged()
def test_test_files_excluded()
def test_unused_export_detected()
def test_framework_decorator_excluded()
def test_dynamic_pattern_excluded()
def test_confidence_low_for_recent_files()
def test_confidence_high_for_stale_unreachable()
def test_zombie_package_detected()
def test_whitelist_respected()
def test_safe_to_delete_conservative()
def test_report_deletable_lines_sum()
```

**test_confidence_with_git.py** (4 tests):
```python
def test_hotspot_decays_faster()
def test_stable_decays_slower()
def test_rewrite_commit_hard_decay()
def test_typo_commit_soft_decay()
```

**Integration tests** (use sample_repo with added fixtures):
```python
async def test_git_indexer_on_sample_repo()
async def test_dead_code_detects_unreachable_fixture()
async def test_co_change_edges_in_graph()
async def test_hotspot_sorted_first_in_generation()
```

### 18. Test Fixtures

Add to `tests/fixtures/sample_repo/`:

**`dead/unreachable_module.py`** — a Python file with functions that nothing imports:
```python
"""This module is intentionally unreachable — used for dead code detection tests."""

def orphaned_function():
    """No one calls this."""
    return 42

class OrphanedClass:
    """No one imports this."""
    pass
```

**`utils/helpers.py`** — add an unused export (if this file already exists, add to it):
```python
def unused_helper():
    """This function is exported but never imported anywhere."""
    return "unused"
```

---

## Implementation Order

Build in this exact order (each step should be testable independently):

1. `GitMetadata` + `DeadCodeFinding` ORM models + migration
2. CRUD functions for git metadata and dead code
3. `GitIndexer` class + `test_git_indexer.py` tests
4. `DeadCodeAnalyzer` class + `test_dead_code.py` tests
5. `GitConfig` + `DeadCodeConfig` dataclasses
6. Template updates (file_page.j2, module_page.j2, repo_overview.j2)
7. `FilePageContext` extensions + `ContextAssembler` extensions
8. `_sort_level_files()` in PageGenerator
9. `_select_generation_depth()` in ContextAssembler
10. Confidence decay git modifiers + `test_confidence_with_git.py`
11. Co-change edges in GraphBuilder
12. FileDiff trigger commit extensions
13. diff_summary.j2 update + `assemble_update_context()`
14. ChangeDetector co-change partner staleness
15. Init pipeline integration (Steps 3.5 + 3.6)
16. Update pipeline integration
17. `repowise dead-code` CLI command
18. Integration tests
19. Run full test suite: `pytest tests/ -q` — all must pass

---

## Critical Constraints

1. **Additive only.** Do NOT rewrite existing methods — add new methods, extend dataclasses
   with defaulted fields, add `{% if %}` blocks to templates.

2. **Graceful degradation.** Every use of `git_metadata` must handle `None`. If `GitIndexer`
   fails or git is unavailable, all features fall back silently to pre-feature behavior.

3. **Follow existing patterns.** JSON stored as Text (not SQLAlchemy JSON type). UUIDs via
   `_new_uuid()`. Timestamps via `_now_utc()`. `Mapped[]` for all ORM columns. `_BATCH_SIZE = 500`
   for bulk operations. `structlog` for logging.

4. **No new dependencies.** `gitpython` is already in the dependency tree. All analysis is
   pure Python + NetworkX + SQL.

5. **Performance.** GitIndexer < 3 min for 3K files. DeadCodeAnalyzer < 10 seconds.
   Co-change computation < 30 seconds.

6. **Conservative dead code.** When in doubt, do NOT flag. `safe_to_delete` is very conservative.

7. **Test everything.** 23 new unit tests + integration tests. All existing 473 tests must
   continue passing.

8. **MCP tools, REST API endpoints, and Web UI pages are NOT built in this phase.** They are
   deferred to Phases 6, 7, and 8 respectively. Only the core logic, templates, CLI, and
   pipeline integration are built here.
