# `vector_store`

Pluggable semantic-search backends for repowise wiki pages.

## Purpose

Embed generated wiki pages into vectors and answer nearest-neighbour
queries. Callers pass raw text; each store owns an `Embedder` and handles
embedding internally.

## Public API

Imported as before via `repowise.core.persistence.vector_store`:

- `VectorStore` — abstract base class.
- `InMemoryVectorStore` — pure-Python dict, no extra deps (tests / dev).
- `LanceDBVectorStore` — embedded LanceDB (`repowise-core[search]` extra).
- `PgVectorStore` — `wiki_pages.embedding` pgvector column (`[pgvector]` extra).
- `cosine_similarity(a, b)` — shared helper.

### Methods

- `embed_and_upsert(page_id, text, metadata)` — single-item write.
- `embed_batch(items: list[tuple[id, text, metadata]])` — embed a whole batch
  in one model call. The ABC provides a correct sequential default; each
  concrete store overrides it to make a single `embed()` call plus a bulk
  upsert. Use this on the hot indexing path; keep `embed_and_upsert` for
  incremental single-page updates.
- `search`, `delete`, `close`, `list_page_ids`,
  `get_page_summary_by_path`, `get_page_summaries_by_paths`.

## Internal layout

| Module | Contents |
|--------|----------|
| `_base.py` | `VectorStore` ABC + `cosine_similarity` |
| `in_memory.py` | `InMemoryVectorStore` |
| `lancedb_store.py` | `LanceDBVectorStore` |
| `pgvector_store.py` | `PgVectorStore` |

The package `__init__` re-exports every public name so the historical
`from repowise.core.persistence.vector_store import ...` path is unchanged.

## Extension points

Author a new backend by subclassing `VectorStore` and implementing the four
abstract methods. Override `embed_batch` if your backend can amortise batched
writes; otherwise the default sequential implementation is used.

## Tests

`tests/unit/persistence/test_vector_store.py`.
