"""Eden AI embedding support for repowise semantic search.

Uses Eden AI's OpenAI-compatible endpoint at ``https://api.edenai.run/v3``.
No additional pip install required, it uses the ``openai`` package. Set
``EDENAI_BASE_URL=https://api.eu.edenai.run/v3`` to keep requests on Eden AI's
EU gateway.

Default model: amazon/amazon.titan-embed-text-v2:0 (1024 dims, EU region)

Usage:
    from repowise.core.providers.embedding.edenai import EdenAIEmbedder

    embedder = EdenAIEmbedder(api_key="...")
    vectors = await embedder.embed(["some text"])
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import ClassVar

from repowise.core.providers.embedding.base import resolve_embedding_timeout


class EdenAIEmbedder:
    """Eden AI embedding adapter implementing the repowise Embedder protocol.

    Args:
        api_key:    Eden AI API key. Falls back to EDENAI_API_KEY env var.
        model:      Embedding model in ``vendor/model`` form. Default:
                    "amazon/amazon.titan-embed-text-v2:0".
        base_url:   Override the Eden AI API URL. Falls back to EDENAI_BASE_URL,
                    then the global endpoint.
        dimensions: Override the declared output width. Falls back to
                    REPOWISE_EMBEDDING_DIMS, then ``_DIMS``. Note: this overrides
                    only the *declared* width that the vector store trusts, it
                    does not add a ``dimensions`` parameter to the API request.
                    Use it to correct a wrong ``_DIMS`` entry when the model's
                    real output differs.
    """

    # Widths measured against Eden AI's /v3/embeddings endpoint, not copied from
    # the upstream vendors. Eden AI returns google/gemini-embedding-001 at its
    # full 3072 width, where OpenRouterEmbedder._DIMS records 768 for the same
    # model id, so the two tables are correct and must not be reconciled.
    # The default is EU-region and the cheapest of these per token.
    _DIMS: ClassVar[dict[str, int]] = {
        "amazon/amazon.titan-embed-text-v2:0": 1024,  # EU region
        "amazon/cohere.embed-multilingual-v3": 1024,  # EU region
        "databricks/databricks-bge-large-en": 1024,  # EU region
        "google/gemini-embedding-001": 3072,  # EU region
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
    }

    _DEFAULT_BASE_URL: str = "https://api.edenai.run/v3"
    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "amazon/amazon.titan-embed-text-v2:0",
        base_url: str | None = None,
        timeout: float | None = None,
        dimensions: int | None = None,
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
        self._base_url = (
            base_url or os.environ.get("EDENAI_BASE_URL") or self._DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = resolve_embedding_timeout(
            timeout, self._DEFAULT_TIMEOUT, provider_env="EDENAI_EMBEDDING_TIMEOUT"
        )
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
        """Embed a batch of texts using Eden AI.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.
        """
        if not texts:
            return []

        model = self._model
        timeout = self._timeout
        base_url = self._base_url
        expected_dimensions = self._dimensions

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
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {expected_dimensions}:
                actual = min(widths - {expected_dimensions})
                raise ValueError(
                    f"EdenAIEmbedder declared {expected_dimensions}-dimensional vectors but the API"
                    f" returned {actual} (model={model!r}). Update"
                    f" EdenAIEmbedder._DIMS[{model!r}] = {actual} to match the server's"
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
