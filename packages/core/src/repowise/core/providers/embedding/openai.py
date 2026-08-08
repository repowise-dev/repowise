"""OpenAI embedding support for repowise semantic search.

Uses the openai SDK with text-embedding-3-small by default (1536 dims).
Runs the synchronous SDK call in a thread pool to avoid blocking asyncio.

Installation:
    pip install openai

Usage:
    import asyncio
    from repowise.core.providers.embedding.openai import OpenAIEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    embedder = OpenAIEmbedder(api_key="sk-...")
    store = InMemoryVectorStore(embedder)
    await store.embed_and_upsert("page-1", "Some wiki content...", {})
    results = await store.search("auth service", limit=5)

Dimensions:
    text-embedding-3-small  → 1536 dims
    text-embedding-3-large  → 3072 dims
    text-embedding-ada-002  → 1536 dims

    REPOWISE_EMBEDDING_DIMS (or the ``dimensions`` arg) overrides the width and
    is passed to the API so the returned vectors match. See ``OpenAIEmbedder``.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any, ClassVar


class OpenAIEmbedder:
    """OpenAI embedding model adapter implementing the repowise Embedder protocol.

    Args:
        api_key: OpenAI API key. Falls back to OPENAI_API_KEY env var.
        model:   Embedding model name. Default: "text-embedding-3-small".
        base_url: Optional custom base URL for OpenAI-compatible endpoints.
        dimensions: Output width for a model not in ``_DIMS`` (e.g. a local
            OpenAI-compatible embedder). Falls back to REPOWISE_EMBEDDING_DIMS,
            then to the known-model table, then 1536. An overridden width is
            also sent to the API so the returned vectors match the declaration;
            an endpoint that does not implement the parameter ignores it.
    """

    _DIMS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    # Default timeout for embedding API calls (seconds).
    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        timeout: float = _DEFAULT_TIMEOUT,
        base_url: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Pass api_key= or set OPENAI_API_KEY env var."
            )
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._model = model
        self._timeout = timeout
        # When the user overrides the width, request that width from the API too,
        # so the returned vectors match the declaration instead of the model's
        # default — otherwise the store is sized to a width the vectors never
        # have. Servers that don't implement the parameter ignore it (returning
        # their native width, which the override then correctly declares); one
        # that can't honour it rejects the request loudly rather than silently
        # returning a mismatched width. No model is special-cased here: the
        # endpoint, not a hardcoded name table, decides what it accepts.
        self._dimensions, self._request_dimensions = self._resolve_dimensions(dimensions, model)
        self._client: object | None = None  # cached; created once on first embed()

    @classmethod
    def _resolve_dimensions(cls, dimensions: int | None, model: str) -> tuple[int, int | None]:
        """Resolve ``(declared_width, override)``.

        ``override`` is the user-chosen width — explicit arg or
        ``REPOWISE_EMBEDDING_DIMS`` — once validated, or ``None`` when the width
        falls back to the known-model table. It doubles as the value to request
        from the API: ``None`` means send no ``dimensions`` (keep the stock
        request byte-identical). Precedence for the declared width:
        explicit arg > REPOWISE_EMBEDDING_DIMS > known-model table > 1536.

        A local OpenAI-compatible embedder (e.g. a self-hosted model) is not in
        ``_DIMS``; without an override its width would silently default to 1536
        and mismatch the store. Mirrors the Ollama/Gemini embedders, which
        already honour REPOWISE_EMBEDDING_DIMS.
        """
        if dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DIMS")
            if env:
                try:
                    dimensions = int(env)
                except ValueError:
                    # Match the message every other bad value raises below,
                    # instead of a raw "invalid literal for int()".
                    raise ValueError("dimensions must be a positive integer") from None
        if dimensions is None:
            return cls._DIMS.get(model, 1536), None
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        return dimensions, dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

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

        model = self._model
        timeout = self._timeout
        request_dimensions = self._request_dimensions
        expected_dimensions = self._dimensions

        def _embed_sync() -> list[list[float]]:
            import openai  # type: ignore[import-untyped]

            # Cache client — create once with timeout, reuse across calls.
            if self._client is None:
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    timeout=timeout,
                    base_url=self._base_url,
                )
            create_kwargs: dict[str, Any] = {"model": model, "input": texts}
            if request_dimensions is not None:
                create_kwargs["dimensions"] = request_dimensions
            response = self._client.embeddings.create(**create_kwargs)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {expected_dimensions}:
                actual = min(widths - {expected_dimensions})
                if request_dimensions is not None:
                    hint = (
                        f"Set REPOWISE_EMBEDDING_DIMS={actual} to match the server's"
                        f" native output, or remove the override to use the model's default."
                    )
                else:
                    hint = (
                        f"The width {expected_dimensions} came from the built-in _DIMS table for"
                        f" {model!r}. Add or update OpenAIEmbedder._DIMS[{model!r}] = {actual},"
                        f" or set REPOWISE_EMBEDDING_DIMS={actual}."
                    )
                raise ValueError(
                    f"OpenAIEmbedder declared {expected_dimensions}-dimensional vectors but the"
                    f" API returned {actual} (model={model!r}). The endpoint likely ignored"
                    f" the 'dimensions' parameter. {hint}"
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
