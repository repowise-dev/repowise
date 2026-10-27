"""Unit tests for OpenRouterEmbedder.

All tests mock openai.OpenAI — no real API calls are made.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenRouter API key required"):
        OpenRouterEmbedder(api_key=None)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    emb = OpenRouterEmbedder()
    assert emb._api_key == "sk-or-test"


def test_default_model():
    emb = OpenRouterEmbedder(api_key="k")
    assert emb._model == "google/gemini-embedding-001"


def test_dimensions_gemini():
    emb = OpenRouterEmbedder(api_key="k", model="google/gemini-embedding-001")
    assert emb.dimensions == 768


def test_dimensions_openai_small():
    emb = OpenRouterEmbedder(api_key="k", model="openai/text-embedding-3-small")
    assert emb.dimensions == 1536


def test_dimensions_openai_large():
    emb = OpenRouterEmbedder(api_key="k", model="openai/text-embedding-3-large")
    assert emb.dimensions == 3072


def test_unknown_model_raises_at_construction():
    """Unknown models must fail fast — a silent dim fallback would corrupt the vector store."""
    with pytest.raises(ValueError, match="Unknown embedding model"):
        OpenRouterEmbedder(api_key="k", model="some/future-model")


def test_malformed_env_raises_the_same_message(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "abc")
    with pytest.raises(ValueError, match="dimensions must be a positive integer"):
        OpenRouterEmbedder(api_key="k")


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _make_mock_embedding(values: list[float]) -> MagicMock:
    item = MagicMock()
    item.embedding = values
    return item


def _make_mock_response(vectors: list[list[float]]) -> MagicMock:
    response = MagicMock()
    response.data = [_make_mock_embedding(v) for v in vectors]
    return response


async def test_embed_empty_returns_empty():
    emb = OpenRouterEmbedder(api_key="k")
    result = await emb.embed([])
    assert result == []


async def test_embed_returns_normalized_vectors():
    # dimensions=3 so the declared width matches the 3-wide mock — original vector preserved.
    raw = [1.0, 0.0, 0.0]
    emb = OpenRouterEmbedder(api_key="k", dimensions=3)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([raw])
        result = await emb.embed(["hello"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6


async def test_embed_batch_returns_correct_count():
    # dimensions=2 so the declared width matches the 2-wide mock — original vectors preserved.
    texts = ["a", "b", "c"]
    raw_vecs = [[1.0, 0.0], [0.0, 1.0], [0.707, 0.707]]
    emb = OpenRouterEmbedder(api_key="k", dimensions=2)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(raw_vecs)
        result = await emb.embed(texts)

    assert len(result) == 3


async def test_embed_passes_model_and_input():
    # dimensions=2 so the 2-wide mock response passes the width guard — original intent preserved.
    emb = OpenRouterEmbedder(api_key="k", model="openai/text-embedding-3-large", dimensions=2)
    captured: list = []

    def fake_create(**kwargs):
        captured.append({"model": kwargs["model"], "input": kwargs["input"]})
        return _make_mock_response([[1.0, 0.0]])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test text"])

    assert captured[0]["model"] == "openai/text-embedding-3-large"
    assert captured[0]["input"] == ["test text"]


async def test_embed_uses_openrouter_base_url():
    """Verify the client is created with the OpenRouter base URL."""
    # dimensions=1 so the 1-wide mock response passes the width guard — original mock preserved.
    emb = OpenRouterEmbedder(api_key="sk-or-test", dimensions=1)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([[1.0]])
        await emb.embed(["test"])

    call_kwargs = mock_client.call_args
    assert call_kwargs.kwargs.get("base_url") == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Width verification — the check added in fix/embedder-width-verification
# ---------------------------------------------------------------------------


async def test_embed_raises_when_api_returns_wrong_width():
    # google/gemini-embedding-001 is declared as 768 in _DIMS.
    # Fake API returns 3-wide vectors (wrong width).
    # Error message should name both numbers and mention _DIMS.
    emb = OpenRouterEmbedder(api_key="k", model="google/gemini-embedding-001")
    assert emb.dimensions == 768

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response(
            [[1.0, 0.0, 0.0]]
        )
        with pytest.raises(ValueError, match="768") as exc_info:
            await emb.embed(["hello"])

    msg = str(exc_info.value)
    assert "3" in msg       # actual width named
    assert "_DIMS" in msg   # points at the table entry


async def test_embed_width_check_not_triggered_on_empty():
    emb = OpenRouterEmbedder(api_key="k")
    assert await emb.embed([]) == []

