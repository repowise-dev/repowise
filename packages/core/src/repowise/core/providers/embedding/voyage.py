"""Voyage AI embedding support for repowise semantic search.

Uses the voyageai SDK with ``voyage-3`` by default (1024 dims). Runs the
synchronous SDK call in a thread pool to avoid blocking asyncio.

Installation:
    pip install voyageai

Usage:
    import asyncio
    from repowise.core.providers.embedding.voyage import VoyageEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    embedder = VoyageEmbedder(api_key="pa-...")
    store = InMemoryVectorStore(embedder)
    await store.embed_and_upsert("page-1", "Some wiki content...", {})
    results = await store.search("auth service", limit=5)

Dimensions:
    voyage-3       → 1024 dims
    voyage-3-large → 1024 dims
    voyage-3.5     → 1024 dims
    voyage-code-3  → 1024 dims

    REPOWISE_EMBEDDING_DIMS (or the ``dimensions`` arg) overrides the width.
    See ``VoyageEmbedder``.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import ClassVar

from repowise.core.providers.embedding.base import resolve_embedding_timeout


class VoyageEmbedder:
    """Voyage AI embedding model adapter implementing the repowise Embedder
    protocol.

    Args:
        api_key: Voyage AI API key. Falls back to VOYAGE_API_KEY env var.
        model:   Embedding model name. Default: "voyage-3".
        timeout: Per-request timeout in seconds. Falls back to the
            VOYAGE_EMBEDDING_TIMEOUT / REPOWISE_EMBEDDING_TIMEOUT env vars,
            then 10.0.
        dimensions: Output width override; falls back to
            REPOWISE_EMBEDDING_DIMS, then the known-model table, then 1024.
    """

    _DIMS: ClassVar[dict[str, int]] = {
        "voyage-3": 1024,
        "voyage-3-large": 1024,
        "voyage-3.5": 1024,
        "voyage-code-3": 1024,
    }

    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "voyage-3",
        timeout: float | None = None,
        dimensions: int | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Voyage AI API key required. Pass api_key= or set VOYAGE_API_KEY env var."
            )
        self._model = model
        self._timeout = resolve_embedding_timeout(
            timeout, self._DEFAULT_TIMEOUT, provider_env="VOYAGE_EMBEDDING_TIMEOUT"
        )
        self._dimensions = self._resolve_dimensions(dimensions)
        self._client: object | None = None  # cached; created once on first embed()

    def _resolve_dimensions(self, dimensions: int | None) -> int:
        """Declared vector width: explicit arg > REPOWISE_EMBEDDING_DIMS >
        known-model table > 1024."""
        if dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DIMS")
            if env:
                try:
                    dimensions = int(env)
                except ValueError:
                    raise ValueError("dimensions must be a positive integer") from None
        if dimensions is None:
            return self._DIMS.get(self._model, 1024)
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        return dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using Voyage AI.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop. Vectors are L2-normalized (the SDK already returns
        unit vectors; normalizing again is a no-op safeguard).
        """
        if not texts:
            return []

        model = self._model
        timeout = self._timeout
        expected_dimensions = self._dimensions

        def _embed_sync() -> list[list[float]]:
            import voyageai  # type: ignore[import-untyped]

            if self._client is None:
                self._client = voyageai.Client(api_key=self._api_key, timeout=timeout)
            response = self._client.embed(inputs=texts, model=model)  # type: ignore[union-attr]
            raw_vectors = [list(v) for v in response.embeddings]
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {expected_dimensions}:
                actual = min(widths)
                raise ValueError(
                    f"VoyageEmbedder declared {expected_dimensions}-dimensional vectors but the"
                    f" API returned {actual} (model={model!r}). Set"
                    f" REPOWISE_EMBEDDING_DIMS={actual} to match the model's actual width."
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
