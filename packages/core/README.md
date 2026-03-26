# repowise-core

Core library for repowise — the ingestion pipeline, dependency graph engine, documentation generation system, and persistence layer. All other repowise packages depend on this.

**Python >= 3.11 · Apache-2.0**

---

## What's in this package

| Module | Purpose |
|--------|---------|
| `repowise.core.ingestion` | File traversal, AST parsing (tree-sitter), dependency graph, change detection |
| `repowise.core.generation` | Context assembly, page generation, Jinja2 prompt templates, job system |
| `repowise.core.persistence` | SQLAlchemy models, async CRUD, full-text search, vector store |
| `repowise.core.providers` | LLM provider abstraction (Anthropic, OpenAI, Ollama, LiteLLM) |
| `repowise.core.analysis` | Dead code detection (unreachable files, unused exports, zombie packages) |
| `repowise.core.rate_limiter` | Token-bucket rate limiter (RPM + TPM) for all LLM providers |

---

## Installation

```bash
# Core only (SQLite backend, no vector search)
pip install repowise-core

# With LanceDB vector search (recommended for local/single-server deployments)
pip install "repowise-core[search]"

# With pgvector (PostgreSQL deployments)
pip install "repowise-core[pgvector]"
```

---

## Supported Languages

AST parsing uses [tree-sitter](https://tree-sitter.github.io/) with one `.scm` query file per language. Adding a new language means writing one `.scm` file — `ASTParser` itself does not change.

| Language | Extensions | Query file |
|----------|-----------|------------|
| Python | `.py` | `queries/python.scm` |
| TypeScript | `.ts`, `.tsx` | `queries/typescript.scm` |
| JavaScript | `.js`, `.jsx` | `queries/javascript.scm` |
| Go | `.go` | `queries/go.scm` |
| Rust | `.rs` | `queries/rust.scm` |
| Java | `.java` | `queries/java.scm` |
| C / C++ | `.c`, `.cpp`, `.h`, `.cc` | `queries/c.scm`, `queries/cpp.scm` |
| Ruby | `.rb` | `queries/ruby.scm` |
| Kotlin | `.kt` | `queries/kotlin.scm` |

**Special handlers** (purpose-built parsers, not tree-sitter):

- OpenAPI / Swagger specs (`.yaml`, `.json`)
- Protobuf (`.proto`)
- GraphQL schemas (`.graphql`)
- Dockerfiles
- GitHub Actions / CI YAML

---

## LLM Providers

| Provider | Name | Notes |
|----------|------|-------|
| Anthropic | `anthropic` | Prompt caching and Message Batches API (50% cost reduction on init) |
| OpenAI | `openai` | Any OpenAI-compatible endpoint |
| Ollama | `ollama` | Fully offline, no API key required |
| LiteLLM | `litellm` | 100+ providers through one interface (optional dependency) |
| Mock | `mock` | In-memory stub for tests — no network calls |

```python
from repowise.core.providers import get_provider

provider = get_provider("anthropic", api_key="sk-ant-...", model="claude-sonnet-4-6")
response = await provider.generate(system_prompt="...", user_prompt="...")
print(response.content)
print(f"{response.input_tokens} in / {response.output_tokens} out")
```

### Registering a custom provider

```python
from repowise.core.providers import register_provider
from my_package import MyProvider

register_provider("my_provider", lambda **kw: MyProvider(**kw))
provider = get_provider("my_provider", model="my-model-v1")
```

---

## Key Modules

### Ingestion

`FileTraverser` walks a repository tree respecting `.gitignore`, `.repowiseIgnore`, a hardcoded blocklist (node_modules, `__pycache__`, build artifacts), auto-detected generated files, and binary files.

`ASTParser` is a single class for all languages. It loads the matching `.scm` query file, runs tree-sitter queries to extract symbols and imports, and returns a `ParsedFile` with a consistent shape regardless of language.

`GraphBuilder` converts all `ParsedFile` outputs into a `networkx.DiGraph` and computes PageRank, strongly connected components (SCCs), betweenness centrality, and community detection (Louvain). The resulting graph drives generation ordering, change propagation, and dead code detection.

`ChangeDetector` diffs two git refs with GitPython, identifies added/modified/deleted/renamed files, detects symbol renames using a signature-similarity heuristic, and determines which wiki pages need regeneration via cascade analysis.

```python
from repowise.core.ingestion import FileTraverser, ASTParser, GraphBuilder, ChangeDetector
from pathlib import Path

# Traverse
traverser = FileTraverser("/path/to/repo")
file_infos = list(traverser.traverse())

# Parse all files
parser = ASTParser()
graph_builder = GraphBuilder()
parsed_files = []

for fi in file_infos:
    source = Path(fi.abs_path).read_bytes()
    parsed = parser.parse_file(fi, source)
    parsed_files.append(parsed)
    graph_builder.add_file(parsed)

graph_builder.build()          # PageRank, SCCs, centrality
graph = graph_builder.graph()  # networkx.DiGraph

# Change detection
detector = ChangeDetector("/path/to/repo")
diffs = detector.get_changed_files("a1b2c3d", "HEAD")
# → [FileDiff(path="src/auth.py", status="modified"), ...]
```

### Generation

`ContextAssembler` builds the prompt context for each page. For every file it assembles (in priority order, dropping lower-priority items when over the 12K-token budget): full source, symbol signatures, graph metrics, git ownership and commit history, import summaries from already-generated pages, RAG context from the vector store, co-change partner pages, dead code findings, and reverse import context.

`PageGenerator` orchestrates hierarchical generation across 9 levels (API contracts → symbol spotlights → file pages → SCC pages → module pages → cross-package pages → repo overview → infra pages → index pages). Within each level, up to `concurrent_jobs` tasks run in parallel via `asyncio.Semaphore`.

`JobSystem` checkpoints progress after every completed page so that a long init job can be resumed after an interruption.

```python
from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator

config = GenerationConfig()
assembler = ContextAssembler(config)
generator = PageGenerator(provider, assembler, config)

pages = await generator.generate_all(
    parsed_files, source_map, graph_builder, repo_structure, repo_name="my-repo"
)

for page in pages:
    print(page.title, page.page_type, f"{page.confidence_score:.2f}")
```

### Persistence

Two SQL backends (SQLite / PostgreSQL) with identical async SQLAlchemy models. Pass a plain `sqlite:///...` or `postgresql://...` URL — `get_db_url()` injects the correct async driver automatically.

```python
from repowise.core.persistence import (
    create_engine, init_db, create_session_factory,
    get_session, upsert_repository, upsert_page_from_generated,
)

engine = create_engine("sqlite:///path/to/wiki.db")
await init_db(engine)   # creates tables + FTS5 index (idempotent)
sf = create_session_factory(engine)

async with get_session(sf) as session:
    repo = await upsert_repository(session, name="my-repo", local_path="/path/to/repo")
    for page in pages:
        await upsert_page_from_generated(session, page, repo.id)
```

Key tables: `repos`, `wiki_pages`, `page_versions`, `wiki_symbols`, `generation_jobs`, `webhook_events`, `graph_nodes`, `graph_edges`, `git_metadata`, `dead_code_findings`.

### Search

```python
from repowise.core.persistence import FullTextSearch, InMemoryVectorStore, MockEmbedder

# Full-text search (SQLite FTS5 or PostgreSQL GIN index)
fts = FullTextSearch(engine)
await fts.ensure_index()
results = await fts.search("authentication middleware", limit=10)

# Semantic search — swap MockEmbedder for a real embedder in production
store = InMemoryVectorStore(embedder=MockEmbedder())
results = await store.search("how does rate limiting work?", limit=5)
```

Vector store backends:

| Backend | When to use |
|---------|-------------|
| `InMemoryVectorStore` | Development / testing (no persistence) |
| `LanceDBVectorStore` | SQLite mode — embedded, no separate server, stored in `.repowise/lancedb/` |
| `PgVectorStore` | PostgreSQL mode — HNSW index via the `pgvector` extension |

### Dead Code Detection

Finds unreachable files (in-degree 0 in the dependency graph), unused exports, unused internal symbols, and zombie packages. No LLM calls — purely graph analysis and git metadata.

```python
from repowise.core.analysis.dead_code import DeadCodeAnalyzer

analyzer = DeadCodeAnalyzer(graph, git_meta_map)
report = analyzer.analyze({"min_confidence": 0.5})

for finding in report.findings:
    print(finding.kind.value, finding.file_path, f"{finding.confidence:.0%}")
    if finding.safe_to_delete:
        print("  -> safe to remove")

print(f"Deletable lines: {report.deletable_lines:,}")
```

---

## Page Types

repowise generates 9 distinct page types in a strict dependency-aware order:

| Level | Page Type | Description |
|-------|-----------|-------------|
| 0 | `api_contract` | OpenAPI specs, Protobuf, GraphQL schemas |
| 1 | `symbol_spotlight` | High-PageRank symbols (top 10% by centrality) |
| 2 | `file_page` | One page per source file |
| 3 | `scc_page` | Circular dependency cluster summary |
| 4 | `module_page` | Package/directory overview |
| 5 | `cross_package` | Cross-package relationships (monorepos) |
| 6 | `repo_overview` | Repository overview and architecture diagram |
| 7 | `infra_page` | Dockerfile, CI YAML, Makefile documentation |
| 8 | `index_page` | Symbol index, search index |

---

## Database Backends

| Backend | Config | Vector search |
|---------|--------|---------------|
| SQLite + LanceDB | `sqlite:///path/to/wiki.db` + `[search]` extra | LanceDB embedded in `.repowise/lancedb/` |
| PostgreSQL + pgvector | `postgresql://user:pass@host/db` + `[pgvector]` extra | HNSW index on `wiki_pages.embedding` column |

---

## Development

```bash
# Install for development (from repo root)
uv pip install -e "packages/core[search]"

# Run tests
pytest tests/unit/core/
```

See [ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for a deep dive into how the ingestion pipeline, generation engine, and three stores fit together.
