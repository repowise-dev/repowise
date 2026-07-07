"""Eden AI embedding support for repowise semantic search.

Uses Eden AI's OpenAI-compatible endpoint at ``https://api.edenai.run/v3``.
No additional pip install required — uses the ``openai`` package. Set
``EDENAI_BASE_URL=https://api.eu.edenai.run/v3`` for EU data residency.

Default model: openai/text-embedding-3-small (1536 dims)

Usage:
    from repowise.core.providers.embedding.edenai import EdenAIEmbedder

    embedder = EdenAIEmbedder(api_key="...")
    vectors = await embedder.embed(["some text"])
"""

from __future__ import annotations

import asyncio
import math
import os


class EdenAIEmbedder:
    """Eden AI embedding adapter implementing the repowise Embedder protocol.

    Args:
        api_key:  Eden AI API key. Falls back to EDENAI_API_KEY env var.
        model:    Embedding model in ``vendor/model`` form. Default:
                  "openai/text-embedding-3-small".
        base_url: Override the Eden AI API URL. Falls back to EDENAI_BASE_URL,
                  then the global endpoint.
    """

    _DIMS: dict[str, int] = {
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
        "cohere/embed-multilingual-v3.0": 1024,
    }

    _DEFAULT_BASE_URL: str = "https://api.edenai.run/v3"
    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "openai/text-embedding-3-small",
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key or os.environ.get("EDENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Eden AI API key required. Pass api_key= or set EDENAI_API_KEY env var."
            )
        if model not in self._DIMS:
            known = ", ".join(sorted(self._DIMS))
            raise ValueError(
                f"Unknown embedding model {model!r}. Stored vectors would be mis-sized "
                f"against the model's real output, silently corrupting the vector store. "
                f"Add {model!r} to EdenAIEmbedder._DIMS with its correct dimension count, "
                f"or pick a known model: {known}."
            )
        self._model = model
        self._base_url = (base_url or os.environ.get("EDENAI_BASE_URL") or self._DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self._timeout = timeout
        self._client: object | None = None

    @property
    def dimensions(self) -> int:
        return self._DIMS[self._model]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using Eden AI.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.
        """
        if not texts:
            return []

        model = self._model
        timeout = self._timeout
        base_url = self._base_url

        def _embed_sync() -> list[list[float]]:
            import openai

            if self._client is None:
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    base_url=base_url,
                    timeout=timeout,
                )
            response = self._client.embeddings.create(model=model, input=texts)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
