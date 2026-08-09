"""Smoke + characterization tests for the shared CLI helper modules.

These guard the extraction of provider/store/state helpers out of ``init_cmd`` /
``update_cmd``: they assert the command modules route through the single shared
implementation (no divergent copies) and pin the behavior of the pure helpers.
"""

from __future__ import annotations

import types
from typing import ClassVar

import pytest

from repowise.cli import _repo_session, _setup, providers, state_persistence
from repowise.cli.commands import init_cmd, update_cmd


def test_command_modules_import() -> None:
    """Both command modules import cleanly with the helpers in place."""
    assert isinstance(init_cmd, types.ModuleType)
    assert isinstance(update_cmd, types.ModuleType)


def test_helpers_are_single_source() -> None:
    """The private aliases on ``init_cmd`` point at the shared implementations.

    Sibling commands (update/reindex/search) import ``_resolve_embedder`` /
    ``_build_embedder`` from ``init_cmd``; these must remain the same objects as
    the canonical ``providers`` helpers so there's exactly one implementation.
    """
    assert init_cmd._resolve_embedder is providers.resolve_embedder
    assert init_cmd._build_embedder is providers.build_embedder


def test_resolve_embedder_explicit_flag_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert providers.resolve_embedder("openai") == "openai"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"GEMINI_API_KEY": "x"}, "gemini"),
        ({"GOOGLE_API_KEY": "x"}, "gemini"),
        ({"OPENAI_API_KEY": "x"}, "openai"),
        ({"OPENROUTER_API_KEY": "x"}, "openrouter"),
        ({"OLLAMA_BASE_URL": "http://localhost:11434"}, "mock"),
        ({"OLLAMA_EMBEDDING_MODEL": "embeddinggemma"}, "ollama"),
        ({"REPOWISE_EMBEDDER": "ollama"}, "ollama"),
        ({}, "mock"),
    ],
)
def test_resolve_embedder_env_detection(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_EMBEDDING_MODEL",
        "REPOWISE_EMBEDDER",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    assert providers.resolve_embedder(None) == expected


def test_build_embedder_falls_back_to_mock() -> None:
    """Unknown / unavailable backends degrade to the deterministic mock."""
    from repowise.core.providers.embedding.base import MockEmbedder

    assert isinstance(providers.build_embedder("definitely-not-a-backend"), MockEmbedder)
    assert isinstance(providers.build_embedder("mock"), MockEmbedder)


def test_build_embedder_fallback_carries_reason() -> None:
    """A silent mock fallback records why, so call sites can warn (R1)."""
    embedder = providers.build_embedder("definitely-not-a-backend")
    assert getattr(embedder, "fallback_reason", None)
    assert "definitely-not-a-backend" in embedder.fallback_reason


def test_build_embedder_mock_request_has_no_reason() -> None:
    """An explicit mock request is not a degradation (R1)."""
    embedder = providers.build_embedder("mock")
    assert getattr(embedder, "fallback_reason", None) is None


def test_build_embedder_bad_timeout_names_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """OLLAMA_EMBEDDING_TIMEOUT=abc degrades to mock with the var named (R2)."""
    from repowise.core.providers.embedding.base import MockEmbedder

    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "abc")
    embedder = providers.build_embedder("ollama")
    assert isinstance(embedder, MockEmbedder)
    assert "OLLAMA_EMBEDDING_TIMEOUT" in embedder.fallback_reason
    assert "abc" in embedder.fallback_reason


def test_build_embedder_bad_dims_names_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """OLLAMA_EMBEDDING_DIMS=abc degrades to mock with the var named (R2)."""
    from repowise.core.providers.embedding.base import MockEmbedder

    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_EMBEDDING_DIMS", "abc")
    embedder = providers.build_embedder("ollama")
    assert isinstance(embedder, MockEmbedder)
    assert "OLLAMA_EMBEDDING_DIMS" in embedder.fallback_reason
    assert "abc" in embedder.fallback_reason


def test_embedder_degraded_warning_none_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """No warning for explicit mock, success, or config that loads (R3)."""
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    assert providers.embedder_degraded_warning(providers.build_embedder("mock"), "mock") is None
    real = providers.build_embedder("ollama")
    assert isinstance(real, OllamaEmbedder)
    assert providers.embedder_degraded_warning(real, "ollama") is None


def test_embedder_degraded_warning_text_for_fallback() -> None:
    """A degraded request surfaces a Warning line naming the embedder (R3)."""
    embedder = providers.build_embedder("definitely-not-a-backend")
    warning = providers.embedder_degraded_warning(embedder, "definitely-not-a-backend")
    assert warning is not None
    assert "Warning" in warning
    assert "definitely-not-a-backend" in warning


def test_embedder_degraded_warning_config_load_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt config.yaml warns instead of silently unpinning (R1)."""
    # Some server-path tests write REPOWISE_EMBEDDER straight into os.environ;
    # the override would early-return past the config read under test.
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    def _boom(_path):
        raise ValueError("bad yaml")

    monkeypatch.setattr("repowise.cli.helpers.load_config", _boom)
    try:
        providers.resolve_embedder_for_repo("/nonexistent/repo")
        embedder = providers.build_embedder("mock")
        warning = providers.embedder_degraded_warning(embedder, "mock")
        assert warning is not None
        assert "config.yaml" in warning
        assert "bad yaml" in warning
    finally:
        providers._config_load_error = None


def test_build_embedder_supports_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    embedder = providers.build_embedder("ollama")
    assert isinstance(embedder, OllamaEmbedder)
    assert embedder._model == "qwen3-embedding:0.6b"


def test_build_embedder_ollama_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.embedding.ollama import OllamaEmbedder

    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "300")
    embedder = providers.build_embedder("ollama")
    assert isinstance(embedder, OllamaEmbedder)
    assert embedder._timeout == 300.0


def test_build_vector_store_returns_a_store(tmp_path) -> None:
    from repowise.core.providers.embedding.base import MockEmbedder

    store = providers.build_vector_store(tmp_path, MockEmbedder())
    assert store is not None
    # LanceDB (when installed) creates its dir under .repowise/lancedb.
    assert store is not None


class _FakeKG:
    nodes: ClassVar[list[dict[str, str]]] = [{"summary": "s"}, {"summary": ""}]
    layers: ClassVar[list[int]] = [1, 2, 3]
    tour: ClassVar[list[int]] = [1, 2]
    fingerprint = "abc123"

    def to_dict(self) -> dict:
        return {"nodes": self.nodes, "fingerprint": self.fingerprint}


def test_build_kg_state_shape() -> None:
    state = state_persistence.build_kg_state(_FakeKG())
    assert state == {
        "version": "1.0.0",
        "node_count": 2,
        "layer_count": 3,
        "tour_steps": 2,
        "has_summaries": True,
        "fingerprint": "abc123",
    }


def test_build_kg_state_missing_attrs() -> None:
    state = state_persistence.build_kg_state(object())
    assert state == {
        "version": "1.0.0",
        "node_count": 0,
        "layer_count": 0,
        "tour_steps": 0,
        "has_summaries": False,
        "fingerprint": "",
    }


def test_save_knowledge_graph_json_writes_file(tmp_path) -> None:
    import json

    state_persistence.save_knowledge_graph_json(tmp_path, _FakeKG())
    out = tmp_path / ".repowise" / "knowledge-graph.json"
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["fingerprint"] == "abc123"


def test_save_knowledge_graph_json_noop_without_to_dict(tmp_path) -> None:
    state_persistence.save_knowledge_graph_json(tmp_path, object())
    assert not (tmp_path / ".repowise" / "knowledge-graph.json").exists()


def test_setup_logging_silence_runs() -> None:
    import logging

    _setup.setup_logging_silence()
    assert logging.getLogger("httpx").level == logging.ERROR
    assert logging.getLogger("httpcore").level == logging.ERROR


def test_configure_cli_logging_quiet_by_default() -> None:
    import logging

    prior = {n: logging.getLogger(n).level for n in ("repowise.core", "repowise.server")}
    try:
        _setup.configure_cli_logging(verbose=False)
        assert logging.getLogger("repowise.core").level == logging.ERROR
        assert logging.getLogger("repowise.server").level == logging.ERROR
        # HTTP libs stay quiet regardless of verbosity.
        assert logging.getLogger("httpx").level == logging.ERROR
    finally:
        for name, level in prior.items():
            logging.getLogger(name).setLevel(level)


def test_configure_cli_logging_verbose_shows_repowise_debug() -> None:
    import logging

    prior = {n: logging.getLogger(n).level for n in ("repowise.core", "repowise.server")}
    try:
        _setup.configure_cli_logging(verbose=True)
        assert logging.getLogger("repowise.core").level == logging.DEBUG
        assert logging.getLogger("repowise.server").level == logging.DEBUG
        # HTTP libs are HTTP-level noise; kept at ERROR even in verbose mode.
        assert logging.getLogger("httpx").level == logging.ERROR
        assert logging.getLogger("httpcore").level == logging.ERROR
    finally:
        for name, level in prior.items():
            logging.getLogger(name).setLevel(level)
        # Restore the quiet default so verbose state doesn't leak to other tests.
        _setup.configure_cli_logging(verbose=False)


def test_repo_session_exposes_open_repo_db() -> None:
    assert callable(_repo_session.open_repo_db)
