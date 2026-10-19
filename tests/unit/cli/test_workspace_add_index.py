"""``workspace add``'s per-repo index step, with the pipeline and LLM stubbed.

Pins what ``_run_index_for_repo`` writes and prints on each path: docs off,
docs on, a provider that fails to resolve, a generation error, and an index
failure.
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
    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", lambda **_k: 5)
    _run(ws_root, cfg, generate_docs=True, save_key=False)

    state = load_state(ws_root / "added")
    assert state["total_pages"] == 5
    assert state["provider"] == "fake"
    assert state["model"] == "fake-1"
    assert resolve_docs_mode(state) == "llm"
    (args, kwargs), = calls["save_config"]
    assert args[1:3] == ("fake", "fake-1")
    assert kwargs["save_key"] is False
    out = capsys.readouterr().out
    assert "Generated 5 pages" in out
    assert "indexed without docs" not in out


def test_provider_failure_falls_back_to_index_only(ws, monkeypatch, capsys):
    ws_root, cfg, calls = ws

    def boom(*_a, **_k):
        raise RuntimeError("no key")

    monkeypatch.setattr("repowise.cli.helpers.resolve_provider", boom)
    _run(ws_root, cfg, generate_docs=True)

    assert resolve_docs_mode(load_state(ws_root / "added")) == "none"
    assert calls["save_config"] == []
    out = capsys.readouterr().out
    assert "Provider unavailable (no key)" in out
    assert "indexed without docs (provider failure: no key)" in out


def test_generation_error_keeps_the_provider_state(ws, monkeypatch, capsys):
    ws_root, cfg, calls = ws
    monkeypatch.setattr("repowise.cli.helpers.resolve_provider", lambda *a, **k: _provider())

    def boom(**_k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(_index, "_generate_docs_for_added_repo", boom)
    _run(ws_root, cfg, generate_docs=True)

    state = load_state(ws_root / "added")
    assert state["provider"] == "fake"
    assert state["total_pages"] == 0
    assert len(calls["save_config"]) == 1
    assert "Doc generation failed: rate limited" in capsys.readouterr().out


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
