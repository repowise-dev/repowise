"""Ollama embedding support for repowise semantic search.

Uses Ollama's OpenAI-compatible /v1/embeddings endpoint.
Runs the synchronous SDK call in a thread pool to avoid blocking asyncio.

Installation:
    pip install openai  # already a dependency

Usage:
    import asyncio
    from repowise.core.providers.embedding.ollama import OllamaEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    embedder = OllamaEmbedder()
    store = InMemoryVectorStore(embedder)
    await store.embed_and_upsert("page-1", "Some wiki content...", {})
    results = await store.search("auth service", limit=5)

Recommended models:
    nomic-embed-text  → 768 dims  (pull with: ollama pull nomic-embed-text)
    mxbai-embed-large → 1024 dims (pull with: ollama pull mxbai-embed-large)
"""

from __future__ import annotations

import asyncio
import math
import os


class OllamaEmbedder:
    """Ollama embedding model adapter implementing the repowise Embedder protocol.

    Args:
        model:    Ollama embedding model name. Default: "nomic-embed-text".
        base_url: Ollama API base URL. Falls back to OLLAMA_BASE_URL env var,
                  then "http://localhost:11434".
        dimensions: Override the output dimension. Auto-detected from known
                    models; falls back to 768 for unknown models.
    """

    _DIMS: dict[str, int] = {
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
        "snowflake-arctic-embed": 1024,
        "bge-m3": 1024,
    }

    _DEFAULT_TIMEOUT: float = 30.0

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str | None = None,
        dimensions: int | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        env_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        raw_url = base_url or env_url
        # Ensure we use the /v1 path for OpenAI-compatible endpoint
        self._base_url = raw_url.rstrip("/")
        if not self._base_url.endswith("/v1"):
            self._base_url = self._base_url + "/v1"
        self._model = model
        self._timeout = timeout
        self._dims = dimensions or self._DIMS.get(model, 768)
        self._client: object | None = None

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using Ollama.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of L2-normalized float vectors.
        """
        if not texts:
            return []

        model = self._model
        base_url = self._base_url
        timeout = self._timeout

        def _embed_sync() -> list[list[float]]:
            import openai  # type: ignore[import-untyped]

            if self._client is None:
                self._client = openai.OpenAI(
                    api_key="ollama",  # Ollama doesn't check the key
                    base_url=base_url,
                    timeout=timeout,
                )
            response = self._client.embeddings.create(model=model, input=texts)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
