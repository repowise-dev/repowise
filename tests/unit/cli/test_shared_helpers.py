"""Smoke + characterization tests for the shared CLI helper modules.

These guard the extraction of provider/store/state helpers out of ``init_cmd`` /
``update_cmd``: they assert the command modules route through the single shared
implementation (no divergent copies) and pin the behavior of the pure helpers.
"""

from __future__ import annotations

import types

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


def test_build_vector_store_returns_a_store(tmp_path) -> None:
    from repowise.core.providers.embedding.base import MockEmbedder

    store = providers.build_vector_store(tmp_path, MockEmbedder())
    assert store is not None
    # LanceDB (when installed) creates its dir under .repowise/lancedb.
    assert store is not None


class _FakeKG:
    nodes = [{"summary": "s"}, {"summary": ""}]
    layers = [1, 2, 3]
    tour = [1, 2]
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


def test_repo_session_exposes_open_repo_db() -> None:
    assert callable(_repo_session.open_repo_db)
