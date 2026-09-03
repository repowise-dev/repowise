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

from pathlib import Path

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
    "OLLAMA_BASE_URL",
    "OLLAMA_EMBEDDING_MODEL",
    "OLLAMA_EMBEDDING_DIMS",
)


@pytest.fixture(autouse=True)
def _clean_env_and_state(monkeypatch, tmp_path):
    """Strip embedder env vars + reset status so each test starts from scratch.

    ``Path.home()`` is redirected at an empty directory too: resolution falls
    back to the ``embedder_api_key`` saved in ``~/.repowise/config.yaml``, so a
    developer who has one would otherwise see the "missing key" tests resolve a
    real embedder and fail locally while passing in CI.
    """
    for var in _EMBEDDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_state, "_repo_path", None, raising=False)
    monkeypatch.setattr(_state, "_embedder_status", None, raising=False)
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
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


def test_ollama_resolves_without_api_key(monkeypatch):
    """Local Ollama embeddings do not require a cloud API key at construction."""
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    monkeypatch.setenv("REPOWISE_EMBEDDER", "ollama")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")

    embedder = _server._resolve_embedder()

    assert isinstance(embedder, OllamaEmbedder)
    assert _state._embedder_status == {
        "active": "ollama",
        "requested": "ollama",
        "degraded": False,
    }


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


def test_repo_dotenv_supplies_missing_embedder_key(monkeypatch, tmp_path):
    """The key persisted in the repo's .repowise/.env is used when env has none.

    `repowise mcp` loads that file for the repo it is pointed at, but workspace
    siblings and embedded run_mcp() callers never get it — without this fallback
    the server queries an openai-embedded index with mock vectors.
    """
    from repowise.core.providers.embedding.openai import OpenAIEmbedder

    repo_dir = tmp_path / "repo"
    (repo_dir / ".repowise").mkdir(parents=True)
    (repo_dir / ".repowise" / "config.yaml").write_text("embedder: openai\n", encoding="utf-8")
    (repo_dir / ".repowise" / ".env").write_text(
        "OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8"
    )
    monkeypatch.setattr(_state, "_repo_path", str(repo_dir))

    embedder = _server._resolve_embedder()

    assert isinstance(embedder, OpenAIEmbedder)
    assert _state._embedder_status == {
        "active": "openai",
        "requested": "openai",
        "degraded": False,
    }


def test_global_config_supplies_missing_embedder_key(monkeypatch, tmp_path):
    """`repowise serve` restores embedder_api_key from ~/.repowise/config.yaml —
    the MCP server must resolve the same credential from the same place."""
    from repowise.core.providers.embedding.openai import OpenAIEmbedder

    home = tmp_path / "home"
    (home / ".repowise").mkdir(parents=True)
    (home / ".repowise" / "config.yaml").write_text(
        "embedder: openai\nembedder_api_key: sk-from-global\n", encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")

    embedder = _server._resolve_embedder()

    assert isinstance(embedder, OpenAIEmbedder)
    assert _state._embedder_status["degraded"] is False


def test_global_config_key_not_used_for_a_different_embedder(monkeypatch, tmp_path):
    """A saved openai key must not be handed to gemini — that fails confusingly."""
    home = tmp_path / "home"
    (home / ".repowise").mkdir(parents=True)
    (home / ".repowise" / "config.yaml").write_text(
        "embedder: openai\nembedder_api_key: sk-from-global\n", encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("REPOWISE_EMBEDDER", "gemini")

    embedder = _server._resolve_embedder()

    assert isinstance(embedder, MockEmbedder)
    assert _state._embedder_status["degraded"] is True


def test_process_env_key_wins_over_persisted(monkeypatch, tmp_path):
    """An explicitly exported key stays authoritative over the persisted one."""
    from repowise.core.providers.embedding.openai import OpenAIEmbedder

    repo_dir = tmp_path / "repo"
    (repo_dir / ".repowise").mkdir(parents=True)
    (repo_dir / ".repowise" / ".env").write_text("OPENAI_API_KEY=sk-persisted\n", encoding="utf-8")
    monkeypatch.setattr(_state, "_repo_path", str(repo_dir))
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")

    embedder = _server._resolve_embedder()

    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder._api_key == "sk-exported"


def test_keyless_embedder_needs_no_persisted_lookup(monkeypatch):
    """Embedders outside the keyed map resolve without touching the config files."""
    assert _server._persisted_embedder_key("ollama") is None
    assert _server._persisted_embedder_key("mock") is None


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
    """A healthy embedder adds no prose, only the explicit not-degraded verdict.

    ``embedder_degraded: False`` is the whole point: the check runs on every
    call, so a key written only when degraded reads as a 100% degradation rate
    to anything that aggregates it.
    """
    monkeypatch.setattr(
        _state,
        "_embedder_status",
        {"active": "openai", "requested": "openai", "degraded": False},
    )
    meta = build_meta(timing_ms=1.0)
    assert meta["embedder_degraded"] is False
    assert "semantic_search" not in meta
    assert "embedder" not in meta
    assert "embedder_warning" not in meta


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"active": "openai", "requested": "openai", "degraded": False}, True),
        ({"active": "mock", "requested": "mock", "degraded": False}, False),
        ({"active": "mock", "requested": "openai", "degraded": True}, False),
        (None, None),
    ],
)
def test_semantic_search_state_is_three_valued(monkeypatch, status, expected):
    """Telemetry needs the third state named, not inferred.

    A signal that only ever reports ``False`` cannot be told apart, by anything
    aggregating it, from one this version does not report at all — which is what
    hid the keyless population, the larger one, behind ``embedder_degraded``.
    Absent means "never evaluated" and stays distinct from an explicit ``False``.
    """
    from repowise.server.mcp_server._meta import semantic_search_state

    monkeypatch.setattr(_state, "_embedder_status", status)
    assert semantic_search_state() is expected


def test_build_meta_marks_a_keyless_index_full_text_only(monkeypatch):
    """The keyless install: nothing broken, but retrieval really is FTS-only."""
    monkeypatch.setattr(
        _state,
        "_embedder_status",
        {"active": "mock", "requested": "mock", "degraded": False},
    )
    meta = build_meta(timing_ms=1.0)
    assert meta["embedder_degraded"] is False
    assert meta["semantic_search"] is False
    assert meta["embedder"] == "mock"


def test_build_meta_omits_degraded_when_embedder_unresolved(monkeypatch):
    """Nothing resolved → the check never ran, so neither value is honest."""
    monkeypatch.setattr(_state, "_embedder_status", None)
    meta = build_meta(timing_ms=1.0)
    assert "embedder_degraded" not in meta
    assert "semantic_search" not in meta
