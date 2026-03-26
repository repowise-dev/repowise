"""Gemini embedding support for repowise semantic search.

Uses the Google GenAI SDK (google-genai) with the gemini-embedding-001 model.
This embedder satisfies the Embedder protocol and can be passed to any
repowise vector store (InMemoryVectorStore, LanceDBVectorStore, PgVectorStore).

Installation:
    pip install google-genai

Usage:
    import asyncio
    from repowise.core.persistence.gemini_embedder import GeminiEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    embedder = GeminiEmbedder(api_key="AIza...")
    store = InMemoryVectorStore(embedder)
    await store.embed_and_upsert("page-1", "Some wiki content...", {})
    results = await store.search("auth service", limit=5)

Dimensions:
    gemini-embedding-001 produces 768-dimensional vectors by default.
    You can request up to 3072 dimensions via output_dimensionality.
"""

from __future__ import annotations

import asyncio
import math
import os


class GeminiEmbedder:
    """Gemini embedding model adapter implementing the repowise Embedder protocol.

    Args:
        api_key:              Google Gemini API key. Falls back to GEMINI_API_KEY env var.
        model:                Embedding model name. Default: "gemini-embedding-001".
        task_type:            Embedding task type. "SEMANTIC_SIMILARITY" is best for
                              semantic search. Other options: "RETRIEVAL_DOCUMENT",
                              "RETRIEVAL_QUERY", "CLUSTERING", "CLASSIFICATION".
        output_dimensionality: Override output vector size (default: 768, max: 3072).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-embedding-001",
        task_type: str = "SEMANTIC_SIMILARITY",
        output_dimensionality: int = 768,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Gemini API key required. Pass api_key= or set GEMINI_API_KEY env var."
            )
        self._model = model
        self._task_type = task_type
        self._output_dimensionality = output_dimensionality

    @property
    def dimensions(self) -> int:
        return self._output_dimensionality

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using Gemini.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of unit-length (L2-normalized) float vectors.
        """
        if not texts:
            return []

        def _embed_sync() -> list[list[float]]:
            from google import genai  # type: ignore[import-untyped]
            from google.genai import types as genai_types  # type: ignore[import-untyped]

            client = genai.Client(api_key=self._api_key)

            config = genai_types.EmbedContentConfig(
                task_type=self._task_type,
                output_dimensionality=self._output_dimensionality,
            )

            result = client.models.embed_content(
                model=self._model,
                contents=texts,
                config=config,
            )

            raw_vectors = [list(e.values) for e in result.embeddings]
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
