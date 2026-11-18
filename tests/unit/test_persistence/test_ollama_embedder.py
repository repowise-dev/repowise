"""Unit tests for OllamaEmbedder.

All tests mock httpx.AsyncClient, so no local Ollama daemon is required.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import httpx
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
    # When set, post() answers with this real httpx.Response instead.
    error_response: ClassVar[httpx.Response | None] = None

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse | httpx.Response:
        self.calls.append({"url": url, "json": json, "timeout": self.timeout})
        if self.error_response is not None:
            return self.error_response
        return _FakeResponse(self.response_data)


def _refusal(status: int, **body: Any) -> httpx.Response:
    request = httpx.Request("POST", "http://localhost:11434/api/embed")
    return httpx.Response(status, request=request, **body)


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch: pytest.MonkeyPatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0]]}
    _FakeAsyncClient.error_response = None
    monkeypatch.setattr(
        "repowise.core.providers.embedding.ollama.httpx.AsyncClient",
        _FakeAsyncClient,
    )


async def test_embed_empty_returns_empty() -> None:
    embedder = OllamaEmbedder(model="embeddinggemma")
    assert await embedder.embed([]) == []


async def test_embed_posts_batch_to_native_endpoint() -> None:
    # Response vectors must be 1024-wide to match the declared dimensions=1024.
    _FakeAsyncClient.response_data = {
        "embeddings": [
            [1.0] + [0.0] * 1023,
            [0.0, 1.0] + [0.0] * 1022,
        ]
    }
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
            "timeout": pytest.approx(30.0 + len("firstsecond") * 0.002),
        }
    ]
    assert len(vectors) == 2
    assert all(abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-6 for vec in vectors)


async def test_embed_supports_legacy_single_embedding_response() -> None:
    # dimensions=2 so the declared width matches the 2-wide legacy response.
    _FakeAsyncClient.response_data = {"embedding": [3.0, 4.0]}
    embedder = OllamaEmbedder(model="embeddinggemma", dimensions=2)

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
    # dimensions=2 so the default 2-wide fixture response passes the width guard.
    embedder = OllamaEmbedder(model="embeddinggemma", dimensions=2)

    await embedder.embed(["one"])

    assert _FakeAsyncClient.calls[0]["timeout"] == 300.0


async def test_default_timeout_grows_with_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_EMBEDDING_TIMEOUT", raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDING_TIMEOUT", raising=False)
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0]] * 16}
    embedder = OllamaEmbedder(model="embeddinggemma", dimensions=2)

    # A full chunk of long pages measured 171.6s on a CPU-only Ollama.
    await embedder.embed(["x" * 16_000] * 16)

    assert _FakeAsyncClient.calls[0]["timeout"] > 171.6
    assert _FakeAsyncClient.calls[0]["timeout"] == pytest.approx(30.0 + 16 * 16_000 * 0.002)


async def test_short_batch_keeps_about_the_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_EMBEDDING_TIMEOUT", raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDING_TIMEOUT", raising=False)
    embedder = OllamaEmbedder(model="embeddinggemma", dimensions=2)

    await embedder.embed(["one"])

    assert _FakeAsyncClient.calls[0]["timeout"] == pytest.approx(30.006)


async def test_configured_timeout_is_not_scaled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_EMBEDDING_TIMEOUT", raising=False)
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "45")
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0]] * 4}

    await OllamaEmbedder(model="embeddinggemma", dimensions=2).embed(["x" * 16_000] * 4)
    await OllamaEmbedder(model="embeddinggemma", dimensions=2, timeout=5.0).embed(
        ["x" * 16_000] * 4
    )

    assert [call["timeout"] for call in _FakeAsyncClient.calls] == [45.0, 5.0]


async def test_invalid_env_timeout_still_scales(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "soon")
    monkeypatch.delenv("REPOWISE_EMBEDDING_TIMEOUT", raising=False)
    embedder = OllamaEmbedder(model="embeddinggemma", dimensions=2)

    await embedder.embed(["x" * 1_000])

    assert _FakeAsyncClient.calls[0]["timeout"] == pytest.approx(32.0)


# ---------------------------------------------------------------------------
# Width verification — the check added in fix/embedder-width-verification
# ---------------------------------------------------------------------------


async def test_embed_raises_when_server_returns_wrong_width_inferred() -> None:
    # embeddinggemma → _infer_dimensions() → 768.
    # Server returns 3-wide vectors (ignores 'dimensions' parameter).
    # Hint should reference OLLAMA_EMBEDDING_DIMS because the user never set it.
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0, 0.0]]}
    embedder = OllamaEmbedder(model="embeddinggemma")
    assert embedder.dimensions == 768

    with pytest.raises(ValueError, match="768") as exc_info:
        await embedder.embed(["hello"])

    msg = str(exc_info.value)
    assert "3" in msg  # actual width named
    assert "OLLAMA_EMBEDDING_DIMS" in msg  # points at the env var fix


async def test_embed_raises_when_server_returns_wrong_width_explicit() -> None:
    # User set dimensions=1024 explicitly; server returns 3-wide vectors.
    # Hint should also reference OLLAMA_EMBEDDING_DIMS / REPOWISE_EMBEDDING_DIMS.
    _FakeAsyncClient.response_data = {"embeddings": [[1.0, 0.0, 0.0]]}
    embedder = OllamaEmbedder(model="qwen3-embedding:0.6b", dimensions=1024)
    assert embedder.dimensions == 1024

    with pytest.raises(ValueError, match="1024") as exc_info:
        await embedder.embed(["hello"])

    msg = str(exc_info.value)
    assert "3" in msg
    assert "OLLAMA_EMBEDDING_DIMS" in msg


async def test_embed_width_check_not_triggered_on_empty() -> None:
    # embed([]) short-circuits and returns [] without calling the server.
    embedder = OllamaEmbedder(model="embeddinggemma")
    assert await embedder.embed([]) == []


# ---------------------------------------------------------------------------
# Refused requests carry Ollama's own reason (issue #5738)
# ---------------------------------------------------------------------------


async def test_a_width_larger_than_the_model_names_the_setting() -> None:
    # A leftover REPOWISE_EMBEDDING_DIMS=3072 against a 1024-wide model: Ollama
    # answers 400 with its reason in the body, and the bare status line said nothing.
    _FakeAsyncClient.error_response = _refusal(
        400, json={"error": "dimensions 3072 exceeds embedding length 1024"}
    )
    embedder = OllamaEmbedder(model="qwen3-embedding:0.6b", dimensions=3072)

    with pytest.raises(ValueError) as exc_info:
        await embedder.embed(["hello"])

    msg = str(exc_info.value)
    assert "dimensions=3072" in msg
    assert "OLLAMA_EMBEDDING_DIMS=1024" in msg
    assert "REPOWISE_EMBEDDING_DIMS=1024" in msg
    # A configuration error, not one bad input: nothing on it reads as an HTTP status.
    assert getattr(exc_info.value, "status_code", None) is None
    assert getattr(exc_info.value, "response", None) is None


async def test_another_refusal_keeps_its_status_and_adds_the_reason() -> None:
    _FakeAsyncClient.error_response = _refusal(
        400, json={"error": "the input length exceeds the context length"}
    )
    embedder = OllamaEmbedder(model="qwen3-embedding:0.6b")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await embedder.embed(["hello"])

    assert exc_info.value.response.status_code == 400
    assert "the input length exceeds the context length" in str(exc_info.value)


async def test_a_refusal_without_json_carries_the_body_text() -> None:
    _FakeAsyncClient.error_response = _refusal(500, text="model runner crashed")
    embedder = OllamaEmbedder(model="qwen3-embedding:0.6b")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await embedder.embed(["hello"])

    assert exc_info.value.response.status_code == 500
    assert "model runner crashed" in str(exc_info.value)


async def test_a_refusal_with_an_empty_body_is_raised_as_it_was() -> None:
    _FakeAsyncClient.error_response = _refusal(502)
    embedder = OllamaEmbedder(model="qwen3-embedding:0.6b")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await embedder.embed(["hello"])

    assert exc_info.value.response.status_code == 502
