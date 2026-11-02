"""Voyage AI embedder unit tests.

Pins the parts that need no network: construction (key required), the
known-model dimensions table + override precedence, and the normalize
safeguard. The batch call itself is exercised only through the mocked sync
path so the test stays deterministic.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest

from repowise.core.providers.embedding.voyage import VoyageEmbedder


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        VoyageEmbedder()


def test_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    assert VoyageEmbedder()._api_key == "pa-test"


def test_known_model_dimensions() -> None:
    assert VoyageEmbedder(api_key="x").dimensions == 1024  # voyage-3 default


def test_explicit_dimensions_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "256")
    assert VoyageEmbedder(api_key="x").dimensions == 256
    assert VoyageEmbedder(api_key="x", dimensions=512).dimensions == 512


def test_invalid_dimensions_raise() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        VoyageEmbedder(api_key="x", dimensions=0)


def test_embed_routes_through_thread_pool_and_normalizes() -> None:
    """The sync SDK path runs in a thread and the vector is unit length."""
    seen: dict[str, object] = {}

    class _FakeResponse:
        # 1024 dims to match voyage-3's declared width; the guard below
        # catches a mismatch, so the fake must be honest.
        def __init__(self) -> None:
            self.embeddings: list[list[float]] = [[3.0] + [0.0] * 1022 + [4.0]]

    class _FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        # The SDK's own parameter names: an adapter that called this with
        # ``inputs=`` (the keyword the REST layer uses) would raise TypeError
        # here instead of passing and breaking only against the real package.
        def embed(self, texts, model=None):
            seen["texts"] = list(texts)
            seen["model"] = model
            return _FakeResponse()

    fake_sdk = type(sys)("voyageai")
    fake_sdk.Client = _FakeClient
    with patch.dict(sys.modules, {"voyageai": fake_sdk}):
        embedder = VoyageEmbedder(api_key="pa-test")
        vectors = asyncio.run(embedder.embed(["hello world"], kind="query"))
    assert len(vectors) == 1
    _l2 = (sum(x * x for x in vectors[0])) ** 0.5
    assert _l2 == pytest.approx(1.0)
    assert seen == {"texts": ["hello world"], "model": "voyage-3"}


def test_a_width_mismatch_raises_instead_of_passing_missized_vectors() -> None:
    """The declared width is what the vector table is sized to.

    A model whose real output differs would fill a 1024-wide table with
    whatever it returned, and every later search would compare across widths.
    """

    class _FakeResponse:
        def __init__(self) -> None:
            self.embeddings: list[list[float]] = [[0.1, 0.2, 0.3]]

    class _FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        def embed(self, texts, model=None):
            return _FakeResponse()

    fake_sdk = type(sys)("voyageai")
    fake_sdk.Client = _FakeClient
    with patch.dict(sys.modules, {"voyageai": fake_sdk}):
        embedder = VoyageEmbedder(api_key="pa-test")
        with pytest.raises(ValueError, match="REPOWISE_EMBEDDING_DIMS=3"):
            asyncio.run(embedder.embed(["hello world"]))
