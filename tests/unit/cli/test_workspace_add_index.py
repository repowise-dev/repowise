"""``workspace add``'s per-repo index step, with the pipeline and LLM stubbed.

Pins what ``_run_index_for_repo`` writes and prints on each path: docs off,
built from code, written by a model, a provider that fails to resolve, a
generation error, and an index failure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from repowise.cli.commands.workspace_cmd import _index
from repowise.cli.helpers import load_state
from repowise.core.docs_mode import resolve_docs_mode
from repowise.core.workspace.config import WorkspaceConfig


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("primary", "added"):
        (tmp_path / name / ".git").mkdir(parents=True)
    (tmp_path / ".repowise-workspace.yaml").write_text(
        yaml.dump(
            {
                "version": 1,
                "default_repo": "primary",
                "repos": [
                    {"path": "primary", "alias": "primary", "is_primary": True},
                    {"path": "added", "alias": "added"},
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: dict[str, list] = {"save_config": [], "hooks": [], "warn": []}

    async def fake_index(repo_path, **_kwargs):
        return SimpleNamespace(file_count=3, symbol_count=1234)

    async def fake_hooks(_cfg, _root, aliases):
        calls["hooks"].append(aliases)

    monkeypatch.setattr("repowise.core.pipeline.full_index.index_repo_full", fake_index)
    monkeypatch.setattr("repowise.core.workspace.update.run_cross_repo_hooks", fake_hooks)
    monkeypatch.setattr("repowise.cli.helpers.get_head_commit", lambda _p: "abc123")
    monkeypatch.setattr(
        "repowise.cli.helpers.save_config", lambda *a, **k: calls["save_config"].append((a, k))
    )
    monkeypatch.setattr(_index, "warn", lambda msg: calls["warn"].append(msg))
    return tmp_path, WorkspaceConfig.load(tmp_path), calls


def _provider():
    return SimpleNamespace(provider_name="fake", model_name="fake-1")


def _run(ws_root, cfg, **kwargs):
    _index._run_index_for_repo(ws_root / "added", "added", ws_root, cfg, **kwargs)


def test_docs_off_writes_state_and_stamps_the_entry(ws, capsys):
    ws_root, cfg, calls = ws
    _run(ws_root, cfg, docs_skip_reason="no provider configured")

    state = load_state(ws_root / "added")
    assert state["last_sync_commit"] == "abc123"
    assert state["total_pages"] == 0
    assert resolve_docs_mode(state) == "none"
    assert "provider" not in state
    assert calls["save_config"] == []
    assert calls["hooks"] == [["added"]]
    reloaded = WorkspaceConfig.load(ws_root).get_repo("added")
    assert reloaded.last_commit_at_index == "abc123"
    assert reloaded.indexed_at
    out = capsys.readouterr().out
    assert "3 files, 1,234 symbols" in out
    assert "indexed without docs (no provider configured)" in out


def test_docs_on_records_the_provider(ws, monkeypatch, capsys):
    ws_root, cfg, calls = ws
    monkeypatch.setattr("repowise.cli.helpers.resolve_provider", lambda *a, **k: _provider())
    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", lambda **_k: [object()] * 5)
    _run(ws_root, cfg, docs_mode="llm", save_key=False)

    state = load_state(ws_root / "added")
    assert state["total_pages"] == 5
    assert state["provider"] == "fake"
    assert state["model"] == "fake-1"
    assert resolve_docs_mode(state) == "llm"
    # The scope names the docs model; add mines no decisions, so lanes stay pending.
    from repowise.core.decision_lanes import PENDING_DECISION_LANES_KEY, lanes_needing_model
    from repowise.core.index_scope import resolve_index_scope

    scope_provider = resolve_index_scope(state)["provider"]
    assert (scope_provider["name"], scope_provider["model"]) == ("fake", "fake-1")
    assert lanes_needing_model(ws_root / "added")
    assert state[PENDING_DECISION_LANES_KEY] == list(lanes_needing_model(ws_root / "added"))
    (args, kwargs), = calls["save_config"]
    assert args[1:3] == ("fake", "fake-1")
    assert kwargs["save_key"] is False
    out = capsys.readouterr().out
    assert "Documentation: 5 pages, written by fake-1" in out
    assert "indexed without docs" not in out


def test_provider_failure_falls_back_to_built_from_code(ws, monkeypatch, capsys):
    ws_root, cfg, calls = ws

    def boom(*_a, **_k):
        raise RuntimeError("no key")

    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return [object()] * 4

    monkeypatch.setattr("repowise.cli.helpers.resolve_provider", boom)
    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", fake_generate)
    _run(ws_root, cfg, docs_mode="llm")

    assert seen["provider"] is None
    state = load_state(ws_root / "added")
    assert resolve_docs_mode(state) == "deterministic"
    assert "provider" not in state
    assert calls["save_config"] == []
    out = capsys.readouterr().out
    assert "Provider unavailable (no key); building documentation from code" in out
    assert "Documentation: 4 pages, built from code" in out


def test_default_builds_from_code_without_a_model(ws, monkeypatch, capsys):
    ws_root, cfg, calls = ws

    def no_model(*_a, **_k):
        raise AssertionError("built from code must not resolve a provider")

    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return [object()] * 7

    monkeypatch.setattr("repowise.cli.helpers.resolve_provider", no_model)
    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", fake_generate)
    _run(ws_root, cfg, docs_mode="deterministic")

    assert seen["provider"] is None
    state = load_state(ws_root / "added")
    assert state["total_pages"] == 7
    assert resolve_docs_mode(state) == "deterministic"
    assert calls["save_config"] == []
    out = capsys.readouterr().out
    assert "Documentation: 7 pages, built from code" in out
    assert "indexed without docs" not in out


def test_generation_error_keeps_the_provider_state(ws, monkeypatch, capsys):
    ws_root, cfg, calls = ws
    monkeypatch.setattr("repowise.cli.helpers.resolve_provider", lambda *a, **k: _provider())

    def boom(**_k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", boom)
    _run(ws_root, cfg, docs_mode="llm")

    state = load_state(ws_root / "added")
    assert state["provider"] == "fake"
    assert state["total_pages"] == 0
    assert len(calls["save_config"]) == 1
    assert "Documentation failed: rate limited" in capsys.readouterr().out


def test_index_failure_stops_before_any_state(ws, monkeypatch):
    ws_root, cfg, calls = ws

    async def boom(*_a, **_k):
        raise RuntimeError("parse crash")

    monkeypatch.setattr("repowise.core.pipeline.full_index.index_repo_full", boom)
    _run(ws_root, cfg)

    assert calls["warn"] == ["Indexing failed for 'added': parse crash"]
    assert "last_sync_commit" not in load_state(ws_root / "added")
    assert calls["hooks"] == []
    assert WorkspaceConfig.load(ws_root).get_repo("added").last_commit_at_index is None


def test_state_matches_what_workspace_init_writes(ws, monkeypatch):
    """The added repo carries init's stamps and scope, so the first update has nothing to redo."""
    from repowise.cli.helpers import config_fingerprint
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
    from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION
    from repowise.core.index_scope import resolve_index_scope

    ws_root, cfg, _calls = ws
    coverage = SimpleNamespace(to_dict=lambda: {"mode": "full", "commits": 12})

    async def fake_index(repo_path, **_kwargs):
        return SimpleNamespace(
            file_count=3,
            symbol_count=1234,
            git_summary=SimpleNamespace(history_coverage=coverage),
            health_report=object(),
        )

    monkeypatch.setattr("repowise.core.pipeline.full_index.index_repo_full", fake_index)
    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", lambda **_k: [object()] * 2)
    _run(ws_root, cfg, docs_mode="deterministic")

    repo = ws_root / "added"
    state = load_state(repo)
    assert state["config_fingerprint"] == config_fingerprint(repo)
    assert state["config_dependency_fingerprints"]
    assert state["renderer_fingerprint"]
    assert state["health_analyzer_version"] == HEALTH_ANALYZER_VERSION
    assert state["security_scanner_version"] == SECURITY_SCANNER_VERSION
    assert state["git_history_coverage"] == {"mode": "full", "commits": 12}
    scope = resolve_index_scope(state)
    assert scope["git_tier"] == "full"
    assert scope["content_provenance"] == "template"
    assert scope["search"]["full_text"] == "available"
