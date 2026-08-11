"""OpenRouter embedding support for repowise semantic search.

Uses the OpenAI-compatible endpoint at ``https://openrouter.ai/api/v1``.
No additional pip install required — uses the ``openai`` package.

Default model: google/gemini-embedding-001 (768 dims)

Usage:
    from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

    embedder = OpenRouterEmbedder(api_key="sk-or-...")
    vectors = await embedder.embed(["some text"])
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import ClassVar


class OpenRouterEmbedder:
    """OpenRouter embedding adapter implementing the repowise Embedder protocol.

    Args:
        api_key:    OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
        model:      Embedding model name. Default: "google/gemini-embedding-001".
        dimensions: Override the declared output width. Useful when an
                    OpenRouter-compatible endpoint returns a different number of
                    dimensions than the built-in ``_DIMS`` table records for that
                    model. Falls back to REPOWISE_EMBEDDING_DIMS, then ``_DIMS``.
                    Note: this overrides only the *declared* width that the vector
                    store trusts — it does not add a ``dimensions`` parameter to the
                    API request. Use it to correct a wrong ``_DIMS`` entry when the
                    model's real output differs.
    """

    _DIMS: ClassVar[dict[str, int]] = {
        "google/gemini-embedding-001": 768,
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
    }

    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "google/gemini-embedding-001",
        timeout: float = _DEFAULT_TIMEOUT,
        dimensions: int | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouter API key required. Pass api_key= or set OPENROUTER_API_KEY env var."
            )
        if model not in self._DIMS:
            known = ", ".join(sorted(self._DIMS))
            raise ValueError(
                f"Unknown embedding model {model!r}. Stored vectors would be mis-sized "
                f"against the model's real output, silently corrupting the vector store. "
                f"Add {model!r} to OpenRouterEmbedder._DIMS with its correct dimension count, "
                f"or pick a known model: {known}."
            )
        self._model = model
        self._timeout = timeout
        # Resolve declared width: explicit arg > REPOWISE_EMBEDDING_DIMS > _DIMS table.
        if dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DIMS")
            if env:
                try:
                    dimensions = int(env)
                except ValueError:
                    raise ValueError("dimensions must be a positive integer") from None
        self._dimensions = dimensions if dimensions is not None else self._DIMS[model]
        self._client: object | None = None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using OpenRouter.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.
        """
        if not texts:
            return []

        model = self._model
        timeout = self._timeout
        expected_dimensions = self._dimensions

        def _embed_sync() -> list[list[float]]:
            import openai

            if self._client is None:
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    base_url="https://openrouter.ai/api/v1",
                    timeout=timeout,
                )
            response = self._client.embeddings.create(model=model, input=texts)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {expected_dimensions}:
                actual = min(widths - {expected_dimensions})
                raise ValueError(
                    f"OpenRouterEmbedder declared {expected_dimensions}-dimensional vectors but the API"
                    f" returned {actual} (model={model!r}). Update"
                    f" OpenRouterEmbedder._DIMS[{model!r}] = {actual} to match the server's"
                    f" real output."
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
