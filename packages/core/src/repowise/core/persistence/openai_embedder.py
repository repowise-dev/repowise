"""OpenAI embedding support for repowise semantic search.

Uses the openai SDK with text-embedding-3-small by default (1536 dims).
Runs the synchronous SDK call in a thread pool to avoid blocking asyncio.

Installation:
    pip install openai

Usage:
    import asyncio
    from repowise.core.persistence.openai_embedder import OpenAIEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    embedder = OpenAIEmbedder(api_key="sk-...")
    store = InMemoryVectorStore(embedder)
    await store.embed_and_upsert("page-1", "Some wiki content...", {})
    results = await store.search("auth service", limit=5)

Dimensions:
    text-embedding-3-small  → 1536 dims
    text-embedding-3-large  → 3072 dims
    text-embedding-ada-002  → 1536 dims
"""

from __future__ import annotations

import asyncio
import math
import os


class OpenAIEmbedder:
    """OpenAI embedding model adapter implementing the repowise Embedder protocol.

    Args:
        api_key: OpenAI API key. Falls back to OPENAI_API_KEY env var.
        model:   Embedding model name. Default: "text-embedding-3-small".
    """

    _DIMS: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Pass api_key= or set OPENAI_API_KEY env var."
            )
        self._model = model

    @property
    def dimensions(self) -> int:
        return self._DIMS.get(self._model, 1536)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using OpenAI.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of L2-normalized float vectors.
        """
        if not texts:
            return []

        api_key = self._api_key
        model = self._model

        def _embed_sync() -> list[list[float]]:
            import openai  # type: ignore[import-untyped]

            client = openai.OpenAI(api_key=api_key)
            response = client.embeddings.create(model=model, input=texts)
            raw_vectors = [list(item.embedding) for item in response.data]
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
