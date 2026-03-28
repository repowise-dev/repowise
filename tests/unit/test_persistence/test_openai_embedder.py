"""Unit tests for OpenAIEmbedder.

All tests mock openai.OpenAI — no real API calls are made.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from repowise.core.providers.embedding.openai import OpenAIEmbedder


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenAI API key required"):
        OpenAIEmbedder(api_key=None)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    emb = OpenAIEmbedder()
    assert emb._api_key == "sk-test"


def test_dimensions_small():
    emb = OpenAIEmbedder(api_key="k", model="text-embedding-3-small")
    assert emb.dimensions == 1536


def test_dimensions_large():
    emb = OpenAIEmbedder(api_key="k", model="text-embedding-3-large")
    assert emb.dimensions == 3072


def test_dimensions_unknown_model_defaults_to_1536():
    emb = OpenAIEmbedder(api_key="k", model="some-future-model")
    assert emb.dimensions == 1536


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
    emb = OpenAIEmbedder(api_key="k")
    result = await emb.embed([])
    assert result == []


async def test_embed_returns_normalized_vectors():
    raw = [1.0, 0.0, 0.0]
    emb = OpenAIEmbedder(api_key="k")

    with patch("openai.OpenAI") as MockClient:
        MockClient.return_value.embeddings.create.return_value = _make_mock_response([raw])
        result = await emb.embed(["hello"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6


async def test_embed_batch_returns_correct_count():
    texts = ["a", "b", "c"]
    raw_vecs = [[1.0, 0.0], [0.0, 1.0], [0.707, 0.707]]
    emb = OpenAIEmbedder(api_key="k")

    with patch("openai.OpenAI") as MockClient:
        MockClient.return_value.embeddings.create.return_value = _make_mock_response(raw_vecs)
        result = await emb.embed(texts)

    assert len(result) == 3


async def test_embed_passes_model_and_input():
    emb = OpenAIEmbedder(api_key="k", model="text-embedding-3-large")
    captured: list = []

    def fake_create(model, input):
        captured.append({"model": model, "input": input})
        return _make_mock_response([[1.0, 0.0]])

    with patch("openai.OpenAI") as MockClient:
        MockClient.return_value.embeddings.create.side_effect = fake_create
        await emb.embed(["test text"])

    assert captured[0]["model"] == "text-embedding-3-large"
    assert captured[0]["input"] == ["test text"]
