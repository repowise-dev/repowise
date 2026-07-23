"""Unit tests for OllamaEmbedder.

All tests mock httpx.AsyncClient, so no local Ollama daemon is required.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import pytest

from repowise.core.providers.embedding.ollama import OllamaEmbedder


def test_registry_keeps_mock_and_adds_ollama() -> None:
    from repowise.core.providers.embedding.base import MockEmbedder
    from repowise.core.providers.embedding.registry import get_embedder, list_embedders

    embedders = list_embedders()

    assert "mock" in embedders
    assert "ollama" in embedders
    assert isinstance(get_embedder("mock"), MockEmbedder)
    assert isinstance(get_embedder("ollama", model="embeddinggemma"), OllamaEmbedder)


class _FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeAsyncClient:
    calls: ClassVar[list[dict[str, Any]]] = []
    response_data: ClassVar[dict[str, Any]] = {"embeddings": [[1.0, 0.0]]}

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": self.timeout})
        return _FakeResponse(self.response_data)


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch: pytest.MonkeyPatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0]]}
    monkeypatch.setattr(
        "repowise.core.providers.embedding.ollama.httpx.AsyncClient",
        _FakeAsyncClient,
    )


async def test_embed_empty_returns_empty() -> None:
    embedder = OllamaEmbedder(model="embeddinggemma")
    assert await embedder.embed([]) == []


async def test_embed_posts_batch_to_native_endpoint() -> None:
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0], [0.0, 2.0]]}
    embedder = OllamaEmbedder(
        model="qwen3-embedding:0.6b",
        base_url="http://localhost:11434/",
        dimensions=1024,
    )

    vectors = await embedder.embed(["first", "second"])

    assert _FakeAsyncClient.calls == [
        {
            "url": "http://localhost:11434/api/embed",
            "json": {
                "model": "qwen3-embedding:0.6b",
                "input": ["first", "second"],
                "dimensions": 1024,
            },
            "timeout": 30.0,
        }
    ]
    assert len(vectors) == 2
    assert all(abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-6 for vec in vectors)


async def test_embed_supports_legacy_single_embedding_response() -> None:
    _FakeAsyncClient.response_data = {"embedding": [3.0, 4.0]}
    embedder = OllamaEmbedder(model="embeddinggemma")

    result = await embedder.embed(["one"])

    assert result == [[0.6, 0.8]]


def test_model_and_base_url_can_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:11434/")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "all-minilm")

    embedder = OllamaEmbedder()

    assert embedder._base_url == "http://ollama.local:11434"
    assert embedder._model == "all-minilm"
    assert embedder.dimensions == 384


def test_qwen3_dimensions_are_inferred() -> None:
    assert OllamaEmbedder(model="qwen3-embedding:0.6b").dimensions == 1024
    assert OllamaEmbedder(model="qwen3-embedding:4b").dimensions == 2560
    assert OllamaEmbedder(model="qwen3-embedding:8b").dimensions == 4096


def test_timeout_defaults_to_thirty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_EMBEDDING_TIMEOUT", raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDING_TIMEOUT", raising=False)
    assert OllamaEmbedder(model="embeddinggemma")._timeout == 30.0


def test_timeout_can_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "120")
    assert OllamaEmbedder(model="embeddinggemma")._timeout == 120.0


def test_repowise_embedding_timeout_is_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_EMBEDDING_TIMEOUT", raising=False)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "90")
    assert OllamaEmbedder(model="embeddinggemma")._timeout == 90.0


def test_explicit_timeout_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "120")
    assert OllamaEmbedder(model="embeddinggemma", timeout=5.0)._timeout == 5.0


async def test_env_timeout_is_applied_to_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "300")
    embedder = OllamaEmbedder(model="embeddinggemma")

    await embedder.embed(["one"])

    assert _FakeAsyncClient.calls[0]["timeout"] == 300.0


def test_timeout_invalid_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.embedding.base import EmbedderConfigError

    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "invalid")
    with pytest.raises(EmbedderConfigError, match="Invalid OLLAMA_EMBEDDING_TIMEOUT: 'invalid'"):
        OllamaEmbedder(model="embeddinggemma")


def test_dimensions_invalid_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.embedding.base import EmbedderConfigError

    monkeypatch.setenv("OLLAMA_EMBEDDING_DIMS", "-5")
    with pytest.raises(EmbedderConfigError, match="Invalid OLLAMA_EMBEDDING_DIMS: '-5'"):
        OllamaEmbedder(model="embeddinggemma")
