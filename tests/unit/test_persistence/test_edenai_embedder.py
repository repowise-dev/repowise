"""Unit tests for EdenAIEmbedder.

All tests mock openai.OpenAI. No real API calls are made.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.embedding.edenai import EdenAIEmbedder

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("EDENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Eden AI API key required"):
        EdenAIEmbedder(api_key=None)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("EDENAI_API_KEY", "eden-test")
    emb = EdenAIEmbedder()
    assert emb._api_key == "eden-test"


def test_default_model():
    """The default is EU-region, which is the reason to reach for this gateway."""
    emb = EdenAIEmbedder(api_key="k")
    assert emb._model == "amazon/amazon.titan-embed-text-v2:0"


def test_default_base_url():
    emb = EdenAIEmbedder(api_key="k")
    assert emb._base_url == "https://api.edenai.run/v3"


def test_eu_base_url_from_env(monkeypatch):
    monkeypatch.setenv("EDENAI_BASE_URL", "https://api.eu.edenai.run/v3")
    emb = EdenAIEmbedder(api_key="k")
    assert emb._base_url == "https://api.eu.edenai.run/v3"


def test_dimensions_openai_small():
    emb = EdenAIEmbedder(api_key="k", model="openai/text-embedding-3-small")
    assert emb.dimensions == 1536


def test_dimensions_openai_large():
    emb = EdenAIEmbedder(api_key="k", model="openai/text-embedding-3-large")
    assert emb.dimensions == 3072


def test_dimensions_titan_v2():
    emb = EdenAIEmbedder(api_key="k", model="amazon/amazon.titan-embed-text-v2:0")
    assert emb.dimensions == 1024


def test_dimensions_gemini_is_full_width_here():
    """Eden AI returns gemini-embedding-001 at 3072, where OpenRouter records 768.

    Asserted explicitly so the two tables are not "reconciled" into one wrong one.
    """
    emb = EdenAIEmbedder(api_key="k", model="google/gemini-embedding-001")
    assert emb.dimensions == 3072


def test_dimensions_cohere_multilingual_eu():
    emb = EdenAIEmbedder(api_key="k", model="amazon/cohere.embed-multilingual-v3")
    assert emb.dimensions == 1024


def test_explicit_dimensions_override_the_table():
    emb = EdenAIEmbedder(api_key="k", dimensions=256)
    assert emb.dimensions == 256


def test_dimensions_from_env(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "512")
    emb = EdenAIEmbedder(api_key="k")
    assert emb.dimensions == 512


def test_malformed_env_dimensions_raise(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "abc")
    with pytest.raises(ValueError, match="dimensions must be a positive integer"):
        EdenAIEmbedder(api_key="k")


def test_unknown_model_raises_at_construction():
    """Unknown models must fail fast: a silent dim fallback would corrupt the vector store."""
    with pytest.raises(ValueError, match="Unknown embedding model"):
        EdenAIEmbedder(api_key="k", model="some/future-model")


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
    emb = EdenAIEmbedder(api_key="k")
    result = await emb.embed([])
    assert result == []


async def test_embed_returns_normalized_vectors():
    # dimensions=4 so the declared width matches the 4-wide mock.
    raw = [3.0, 0.0, 0.0, 0.0]
    emb = EdenAIEmbedder(api_key="k", dimensions=4)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([raw])
        result = await emb.embed(["hello"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6


async def test_embed_passes_model_and_input():
    # dimensions=2 so the 2-wide mock passes the width guard.
    emb = EdenAIEmbedder(api_key="k", model="openai/text-embedding-3-large", dimensions=2)
    captured: list = []

    def fake_create(model, input):
        captured.append({"model": model, "input": input})
        return _make_mock_response([[1.0, 0.0]])

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test text"])

    assert captured[0]["model"] == "openai/text-embedding-3-large"
    assert captured[0]["input"] == ["test text"]


async def test_embed_uses_edenai_base_url():
    """Verify the client is created with the Eden AI base URL."""
    # dimensions=1 so the 1-wide mock passes the width guard.
    emb = EdenAIEmbedder(api_key="eden-test", dimensions=1)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([[1.0]])
        await emb.embed(["test"])

    assert mock_client.call_args.kwargs.get("base_url") == "https://api.edenai.run/v3"


async def test_embed_raises_when_api_returns_wrong_width():
    """A mis-sized vector must fail loudly rather than corrupt the store."""
    emb = EdenAIEmbedder(api_key="k", model="google/gemini-embedding-001")

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([[1.0, 0.0]])
        with pytest.raises(ValueError) as excinfo:
            await emb.embed(["test"])

    msg = str(excinfo.value)
    assert "3072" in msg
    assert "2" in msg
    assert "_DIMS" in msg


async def test_embed_width_check_not_triggered_on_empty():
    emb = EdenAIEmbedder(api_key="k")
    assert await emb.embed([]) == []


def test_timeout_honours_the_shared_env_var(monkeypatch):
    """Every other embedder reads REPOWISE_EMBEDDING_TIMEOUT; this one must too."""
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "42")
    emb = EdenAIEmbedder(api_key="k")
    assert emb._timeout == 42.0


def test_timeout_provider_env_wins(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_TIMEOUT", "42")
    monkeypatch.setenv("EDENAI_EMBEDDING_TIMEOUT", "7")
    emb = EdenAIEmbedder(api_key="k")
    assert emb._timeout == 7.0
