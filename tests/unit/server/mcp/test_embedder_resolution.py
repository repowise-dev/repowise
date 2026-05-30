"""Tests for MCP embedder resolution + degradation surfacing (issue #306).

When an explicitly-configured embedder fails to initialise (missing key,
missing SDK, unknown name) the MCP server must NOT silently masquerade as
healthy. It still falls back to MockEmbedder so non-RAG tools keep working, but
records the degradation so `build_meta` surfaces it in every tool's `_meta`.

These tests also pin the "all embedders work" contract: resolution goes through
the shared registry, so openrouter and custom-registered embedders are honoured
— not just the hardcoded openai/gemini branches that the old code special-cased.
"""

from __future__ import annotations

import pytest

from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _server, _state
from repowise.server.mcp_server._meta import build_meta

# Embedder env vars that, if present in the real environment, would let an
# explicitly-configured embedder succeed and break the "missing key" tests.
_EMBEDDER_ENV_VARS = (
    "REPOWISE_EMBEDDER",
    "REPOWISE_EMBEDDING_MODEL",
    "REPOWISE_EMBEDDING_DIMS",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env_and_state(monkeypatch):
    """Strip embedder env vars + reset status so each test starts from scratch."""
    for var in _EMBEDDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_state, "_repo_path", None, raising=False)
    monkeypatch.setattr(_state, "_embedder_status", None, raising=False)
    yield
    _state._embedder_status = None


def test_no_config_uses_mock_not_degraded(monkeypatch):
    """Nothing configured → MockEmbedder is the intended default, not degraded."""
    embedder = _server._resolve_embedder()
    assert isinstance(embedder, MockEmbedder)
    assert _state._embedder_status == {
        "active": "mock",
        "requested": None,
        "degraded": False,
    }


def test_explicit_mock_not_degraded(monkeypatch):
    """Explicitly requesting mock is a deliberate choice, never a degradation."""
    monkeypatch.setenv("REPOWISE_EMBEDDER", "mock")
    embedder = _server._resolve_embedder()
    assert isinstance(embedder, MockEmbedder)
    assert _state._embedder_status["degraded"] is False


def test_openai_without_key_degrades_and_names_remediation(monkeypatch):
    """Explicit openai + no key → fall back to mock, flag degraded, name the key."""
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")
    embedder = _server._resolve_embedder()

    assert isinstance(embedder, MockEmbedder)
    status = _state._embedder_status
    assert status["degraded"] is True
    assert status["active"] == "mock"
    assert status["requested"] == "openai"
    assert "OPENAI_API_KEY" in status["reason"]


def test_openrouter_without_key_degrades(monkeypatch):
    """openrouter is NOT one of the old hardcoded branches — it must still degrade
    (and not be silently treated as mock). Proves all registry embedders work."""
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openrouter")
    embedder = _server._resolve_embedder()

    assert isinstance(embedder, MockEmbedder)
    status = _state._embedder_status
    assert status["degraded"] is True
    assert status["requested"] == "openrouter"
    assert "OPENROUTER_API_KEY" in status["reason"]


def test_unknown_embedder_name_degrades(monkeypatch):
    """A typo'd / unknown embedder name surfaces as degraded, not silent mock."""
    monkeypatch.setenv("REPOWISE_EMBEDDER", "definitely-not-an-embedder")
    embedder = _server._resolve_embedder()

    assert isinstance(embedder, MockEmbedder)
    status = _state._embedder_status
    assert status["degraded"] is True
    assert status["requested"] == "definitely-not-an-embedder"


def test_custom_registered_embedder_is_honoured(monkeypatch):
    """A custom embedder registered via register_embedder must resolve cleanly —
    the server resolves through the shared registry, not a hardcoded subset."""
    from repowise.core.providers.embedding import register_embedder
    from repowise.core.providers.embedding.registry import _custom_embedders

    class _FakeEmbedder:
        dimensions = 4

        async def embed(self, texts):
            return [[0.0, 0.0, 0.0, 1.0] for _ in texts]

    register_embedder("fake-test-embedder", lambda **kw: _FakeEmbedder())
    try:
        monkeypatch.setenv("REPOWISE_EMBEDDER", "fake-test-embedder")
        embedder = _server._resolve_embedder()
        assert isinstance(embedder, _FakeEmbedder)
        assert _state._embedder_status == {
            "active": "fake-test-embedder",
            "requested": "fake-test-embedder",
            "degraded": False,
        }
    finally:
        _custom_embedders.pop("fake-test-embedder", None)


def test_config_yaml_embedder_is_read(monkeypatch, tmp_path):
    """The embedder name is read from .repowise/config.yaml when no env var set."""
    repo_dir = tmp_path / "repo"
    (repo_dir / ".repowise").mkdir(parents=True)
    (repo_dir / ".repowise" / "config.yaml").write_text(
        "provider: deepseek\nembedder: openai\n", encoding="utf-8"
    )
    monkeypatch.setattr(_state, "_repo_path", str(repo_dir))

    embedder = _server._resolve_embedder()
    assert isinstance(embedder, MockEmbedder)  # no key → fell back
    assert _state._embedder_status["requested"] == "openai"
    assert _state._embedder_status["degraded"] is True


def test_build_meta_surfaces_degraded_embedder(monkeypatch):
    """A degraded embedder shows up in the _meta envelope so callers can detect it."""
    monkeypatch.setattr(
        _state,
        "_embedder_status",
        {"active": "mock", "requested": "openai", "degraded": True, "reason": "boom"},
    )
    meta = build_meta(timing_ms=1.0)
    assert meta["embedder"] == "mock"
    assert meta["embedder_degraded"] is True
    assert meta["embedder_warning"] == "boom"


def test_build_meta_clean_when_healthy(monkeypatch):
    """A healthy (or unresolved) embedder leaves _meta clean — no noise fields."""
    monkeypatch.setattr(
        _state,
        "_embedder_status",
        {"active": "openai", "requested": "openai", "degraded": False},
    )
    meta = build_meta(timing_ms=1.0)
    assert "embedder_degraded" not in meta
    assert "embedder" not in meta
    assert "embedder_warning" not in meta

    # And when nothing has been resolved at all.
    monkeypatch.setattr(_state, "_embedder_status", None)
    assert "embedder_degraded" not in build_meta(timing_ms=1.0)
