"""Ollama embedding support for repowise semantic search.

Uses Ollama's native ``/api/embed`` endpoint, so local embedding models do not
need to be exposed through the OpenAI-compatible API.
"""

from __future__ import annotations

import math
import os
from typing import Any

import httpx

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "embeddinggemma"
_DEFAULT_TIMEOUT = 30.0


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def _infer_dimensions(model: str) -> int:
    """Best-effort dimension hint for common Ollama embedding models."""
    name = model.lower()
    if "qwen3-embedding" in name:
        if "4b" in name:
            return 2560
        if "8b" in name:
            return 4096
        return 1024
    if "all-minilm" in name or "minilm" in name:
        return 384
    if "mxbai-embed-large" in name or "bge-m3" in name:
        return 1024
    if "nomic-embed-text" in name or "embeddinggemma" in name:
        return 768
    return 768


class OllamaEmbedder:
    """Ollama embedding adapter implementing the repowise Embedder protocol.

    Args:
        model: Ollama embedding model name. Defaults to ``embeddinggemma``.
        base_url: Ollama server URL. Defaults to ``http://localhost:11434``.
        dimensions: Optional output dimension hint. Also sent to Ollama as
            ``dimensions`` when provided.
        timeout: Per-request timeout in seconds. Falls back to the
            ``OLLAMA_EMBEDDING_TIMEOUT`` / ``REPOWISE_EMBEDDING_TIMEOUT`` env
            vars, then ``30.0``. Raise it when embedding long pages on a slow
            local model that would otherwise exceed the default and be dropped.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self._model = (
            model
            or os.environ.get("OLLAMA_EMBEDDING_MODEL")
            or os.environ.get("REPOWISE_EMBEDDING_MODEL")
            or _DEFAULT_MODEL
        )
        self._base_url = _normalize_base_url(
            base_url or os.environ.get("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL
        )
        env_dimensions = os.environ.get("OLLAMA_EMBEDDING_DIMS") or os.environ.get(
            "REPOWISE_EMBEDDING_DIMS"
        )
        self._requested_dimensions = dimensions or (int(env_dimensions) if env_dimensions else None)
        self._dimensions = self._requested_dimensions or _infer_dimensions(self._model)
        env_timeout = os.environ.get("OLLAMA_EMBEDDING_TIMEOUT") or os.environ.get(
            "REPOWISE_EMBEDDING_TIMEOUT"
        )
        self._timeout = (
            timeout
            if timeout is not None
            else (float(env_timeout) if env_timeout else _DEFAULT_TIMEOUT)
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using Ollama's native API."""
        if not texts:
            return []

        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }
        if self._requested_dimensions is not None:
            payload["dimensions"] = self._requested_dimensions

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}/api/embed", json=payload)
            response.raise_for_status()
            data = response.json()

        raw_vectors = data.get("embeddings")
        if raw_vectors is None and "embedding" in data:
            raw_vectors = [data["embedding"]]
        if not isinstance(raw_vectors, list):
            raise ValueError("Ollama embedding response did not include embeddings.")
        if len(raw_vectors) != len(texts):
            raise ValueError(
                f"Ollama returned {len(raw_vectors)} embeddings for {len(texts)} inputs."
            )

        return [_l2_normalize([float(value) for value in vector]) for vector in raw_vectors]


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
