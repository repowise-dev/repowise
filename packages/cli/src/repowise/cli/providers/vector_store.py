"""Vector-store construction shared across CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_vector_store(repo_path: Path, embedder: Any) -> Any:
    """Build the repo-local vector store, preferring LanceDB.

    Uses LanceDB at ``.repowise/lancedb`` so previously-embedded pages and
    decisions stay matchable across runs; falls back to an in-memory store
    (which only sees this run's vectors) when LanceDB isn't installed.
    """
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    lance_dir = repo_path / ".repowise" / "lancedb"
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        lance_dir.mkdir(parents=True, exist_ok=True)
        return LanceDBVectorStore(str(lance_dir), embedder=embedder)
    except ImportError:
        return InMemoryVectorStore(embedder)
