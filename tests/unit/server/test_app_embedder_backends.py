"""`_build_embedder` must honour every documented `REPOWISE_EMBEDDER` value.

``docs/reference/CONFIG.md`` lists ``edenai`` among the accepted values, but the
HTTP server resolved backends through a hardcoded chain that stopped at
``openrouter``, so ``REPOWISE_EMBEDDER=edenai`` fell through to the keyless
mock with only a warning: the server then reported healthy while semantic
search ran on vectors that cannot match the index. That is the same failure the
MCP server fixed in #324 by going through the shared registry; this chain is
the remaining hardcoded copy.
"""

from __future__ import annotations

import pytest

from repowise.core.providers.embedding.base import KeylessEmbedder
from repowise.core.providers.embedding.caching import CachingEmbedder
from repowise.core.providers.embedding.edenai import EdenAIEmbedder
from repowise.server import app as app_module
from repowise.server.app import _build_embedder, _build_query_embedder


@pytest.fixture(autouse=True)
def _clean_embedder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("REPOWISE_EMBEDDER", "REPOWISE_EMBEDDING_MODEL", "EDENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_edenai_builds_a_real_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOWISE_EMBEDDER", "edenai")
    monkeypatch.setenv("EDENAI_API_KEY", "test-key-not-a-real-one")
    assert isinstance(_build_embedder(), EdenAIEmbedder)


def test_edenai_honours_the_indexed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard as the other backends: serve must not silently re-embed at a
    different width than the one init wrote (issue #426)."""
    monkeypatch.setenv("REPOWISE_EMBEDDER", "edenai")
    monkeypatch.setenv("EDENAI_API_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("REPOWISE_EMBEDDING_MODEL", "openai/text-embedding-3-large")
    embedder = _build_embedder()
    assert isinstance(embedder, EdenAIEmbedder)
    assert embedder.dimensions == 3072


def test_an_unknown_backend_still_degrades_to_the_keyless_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPOWISE_EMBEDDER", "not-a-backend")
    assert isinstance(_build_embedder(), KeylessEmbedder)


@pytest.mark.asyncio
async def test_query_embedder_caches_repeated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class SpyEmbedder:
        dimensions = 2

        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            return [[1.0, 2.0] for _ in texts]

    inner = SpyEmbedder()
    monkeypatch.setattr(app_module, "_build_embedder", lambda: inner)

    embedder = _build_query_embedder()
    assert isinstance(embedder, CachingEmbedder)

    assert await embedder.embed(["same query"]) == [[1.0, 2.0]]
    assert await embedder.embed(["same query"]) == [[1.0, 2.0]]
    assert inner.calls == 1


def test_query_embedder_leaves_keyless_embedder_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = KeylessEmbedder()
    monkeypatch.setattr(app_module, "_build_embedder", lambda: inner)

    assert _build_query_embedder() is inner
