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


@pytest.mark.parametrize("model", sorted(OpenRouterEmbedder._DIMS))
async def test_every_known_model_requests_its_declared_width(monkeypatch, model):
    # OpenRouter serves google/gemini-embedding-001 (the default) at 3072 unless
    # a width is requested, so a request without ``dimensions`` could never match
    # the 768 declared in _DIMS. For every model in the table, the width the
    # request asks for must equal the width the code declares.
    monkeypatch.delenv("REPOWISE_EMBEDDING_DIMS", raising=False)
    emb = OpenRouterEmbedder(api_key="k", model=model)

    with patch("openai.OpenAI") as mock_client:
        create = mock_client.return_value.embeddings.create
        create.return_value = _make_mock_response([[1.0] * emb.dimensions])
        await emb.embed(["hello"])

    assert (
        create.call_args.kwargs["dimensions"] == emb.dimensions == OpenRouterEmbedder._DIMS[model]
    )


async def test_default_model_requests_768(monkeypatch):
    monkeypatch.delenv("REPOWISE_EMBEDDING_DIMS", raising=False)
    emb = OpenRouterEmbedder(api_key="k")

    with patch("openai.OpenAI") as mock_client:
        create = mock_client.return_value.embeddings.create
        create.return_value = _make_mock_response([[1.0] * 768])
        await emb.embed(["hello"])

    assert create.call_args.kwargs["dimensions"] == 768


async def test_configured_width_is_sent_to_the_api(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "1536")
    emb = OpenRouterEmbedder(api_key="k")
    assert emb.dimensions == 1536

    with patch("openai.OpenAI") as mock_client:
        create = mock_client.return_value.embeddings.create
        create.return_value = _make_mock_response([[1.0] * 1536])
        vectors = await emb.embed(["hello"])

    assert create.call_args.kwargs["dimensions"] == 1536
    assert len(vectors[0]) == 1536


@pytest.fixture
def _no_width_env(monkeypatch):
    monkeypatch.delenv("REPOWISE_EMBEDDING_DIMS", raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDING_DECLARED_DIMS", raising=False)


@pytest.mark.usefixtures("_no_width_env")
@pytest.mark.parametrize("source", ["arg", "env"])
async def test_declared_width_is_never_sent_to_the_api(monkeypatch, source):
    # For a fixed-width model that refuses ``dimensions`` outright, the width is
    # declared without being requested, as on OpenAIEmbedder.
    if source == "env":
        monkeypatch.setenv("REPOWISE_EMBEDDING_DECLARED_DIMS", "3072")
        emb = OpenRouterEmbedder(api_key="k")
    else:
        emb = OpenRouterEmbedder(api_key="k", declared_dimensions=3072)
    assert emb.dimensions == 3072

    with patch("openai.OpenAI") as mock_client:
        create = mock_client.return_value.embeddings.create
        create.return_value = _make_mock_response([[1.0] * 3072])
        vectors = await emb.embed(["hello"])

    assert "dimensions" not in create.call_args.kwargs
    assert len(vectors[0]) == 3072


@pytest.mark.usefixtures("_no_width_env")
async def test_configured_width_wins_over_declared_width(monkeypatch):
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "1536")
    monkeypatch.setenv("REPOWISE_EMBEDDING_DECLARED_DIMS", "3072")
    emb = OpenRouterEmbedder(api_key="k", declared_dimensions=3072)

    with patch("openai.OpenAI") as mock_client:
        create = mock_client.return_value.embeddings.create
        create.return_value = _make_mock_response([[1.0] * 1536])
        await emb.embed(["hello"])

    assert emb.dimensions == 1536
    assert create.call_args.kwargs["dimensions"] == 1536


@pytest.mark.usefixtures("_no_width_env")
async def test_declared_width_mismatch_names_the_declared_setting():
    emb = OpenRouterEmbedder(api_key="k", declared_dimensions=768)

    with patch("openai.OpenAI") as mock_client:
        mock_client.return_value.embeddings.create.return_value = _make_mock_response([[1.0] * 3])
        with pytest.raises(ValueError, match="REPOWISE_EMBEDDING_DECLARED_DIMS"):
            await emb.embed(["hello"])


@pytest.mark.usefixtures("_no_width_env")
@pytest.mark.parametrize("kwarg", ["dimensions", "declared_dimensions"])
@pytest.mark.parametrize("value", [0, -1, True])
def test_bad_explicit_width_raises(kwarg, value):
    with pytest.raises(ValueError, match="dimensions must be a positive integer"):
        OpenRouterEmbedder(api_key="k", **{kwarg: value})


def test_malformed_declared_env_raises_the_same_message(monkeypatch):
    monkeypatch.delenv("REPOWISE_EMBEDDING_DIMS", raising=False)
    monkeypatch.setenv("REPOWISE_EMBEDDING_DECLARED_DIMS", "abc")
    with pytest.raises(ValueError, match="dimensions must be a positive integer"):
        OpenRouterEmbedder(api_key="k")


async def test_embed_width_check_not_triggered_on_empty():
    emb = OpenRouterEmbedder(api_key="k")
    assert await emb.embed([]) == []

