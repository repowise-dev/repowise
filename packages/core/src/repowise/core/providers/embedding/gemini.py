"""Gemini embedding support for repowise semantic search.

Uses the Google GenAI SDK (google-genai) with the gemini-embedding-001 model.
This embedder satisfies the Embedder protocol and can be passed to any
repowise vector store (InMemoryVectorStore, LanceDBVectorStore, PgVectorStore).

Installation:
    pip install google-genai

Usage:
    import asyncio
    from repowise.core.providers.embedding.gemini import GeminiEmbedder
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
import logging
import math
import os

# Suppress "Both GOOGLE_API_KEY and GEMINI_API_KEY are set" from google-genai SDK.
# We resolve and pass the key explicitly, so the env-var conflict warning is noise.
logging.getLogger("google_genai._api_client").setLevel(logging.ERROR)


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

    # Default timeout for embedding API calls (seconds).
    # Prevents search_codebase from hanging on slow / unreachable API.
    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-embedding-001",
        task_type: str = "SEMANTIC_SIMILARITY",
        output_dimensionality: int = 768,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Gemini API key required. Pass api_key= or set GEMINI_API_KEY env var."
            )
        self._model = model
        self._task_type = task_type
        self._output_dimensionality = output_dimensionality
        self._timeout = timeout
        self._client: object | None = None  # cached; created once on first embed()

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

        api_key = self._api_key
        model = self._model
        task_type = self._task_type
        output_dimensionality = self._output_dimensionality
        timeout = self._timeout

        def _embed_sync() -> list[list[float]]:
            from google import genai  # type: ignore[import-untyped]
            from google.genai import types as genai_types  # type: ignore[import-untyped]

            # Cache client — create once, reuse across calls.
            if self._client is None:
                try:
                    http_options = genai_types.HttpOptions(timeout=int(timeout * 1000))
                except Exception:
                    http_options = None  # older SDK versions may not support this
                kwargs: dict = {"api_key": api_key}
                if http_options is not None:
                    kwargs["http_options"] = http_options
                self._client = genai.Client(**kwargs)

            config = genai_types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=output_dimensionality,
            )

            result = self._client.models.embed_content(  # type: ignore[union-attr]
                model=model,
                contents=texts,
                config=config,
            )

            raw_vectors = [list(e.values) for e in result.embeddings]
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {output_dimensionality}:
                actual = min(widths - {output_dimensionality})
                raise ValueError(
                    f"GeminiEmbedder declared {output_dimensionality}-dimensional vectors but"
                    f" the API returned {actual} (model={model!r}). The API ignored"
                    f" 'output_dimensionality'. Verify the model supports {output_dimensionality}"
                    f" dimensions, or construct GeminiEmbedder with output_dimensionality={actual}."
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
