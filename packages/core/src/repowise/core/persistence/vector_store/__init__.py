"""Vector store abstraction and implementations for repowise semantic search.

Three implementations are provided:

InMemoryVectorStore
    Pure Python, no external dependencies.  Cosine similarity search over
    an in-memory dict.  Suitable for tests and development.

LanceDBVectorStore
    Embedded vector database stored in a local directory.  Requires the
    ``repowise-core[search]`` extra (lancedb>=0.12).

PgVectorStore
    Stores embeddings in the ``wiki_pages.embedding`` pgvector column.
    Requires the ``repowise-core[pgvector]`` extra and a running PostgreSQL
    with the ``vector`` extension enabled.

All stores accept an :class:`Embedder` at construction time and handle
embedding internally — callers pass raw text, not pre-computed vectors.

Every store implements :meth:`VectorStore.embed_batch`, which embeds a whole
list of pages in a single model call. Large-repo indexing uses the batch path
to avoid one embedding round-trip per page; the single-item
:meth:`VectorStore.embed_and_upsert` path is preserved for incremental updates.
"""

from __future__ import annotations

from ._base import VectorStore, cosine_similarity
from .in_memory import InMemoryVectorStore
from .lancedb_store import LanceDBVectorStore
from .pgvector_store import PgVectorStore

__all__ = [
    "InMemoryVectorStore",
    "LanceDBVectorStore",
    "PgVectorStore",
    "VectorStore",
    "cosine_similarity",
]
