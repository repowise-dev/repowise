"""Dashboard stale rows say what git knows: commits since, and moved files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from repowise.server.mcp_server.tool_why.dashboard import _stale_row_currency


def _decision(files: list[str], evidence: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_commits_json=json.dumps(evidence or []),
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )


def test_stale_row_reports_commits_since(tmp_path, monkeypatch):
    calls = {}

    def fake_commits_since(root, *, since_commit=None, since_date=None, nodes=(), timeout=5.0):
        calls["since_commit"] = since_commit
        calls["nodes"] = list(nodes)
        return 3

    monkeypatch.setattr(
        "repowise.server.mcp_server.tool_why.dashboard.commits_since", fake_commits_since
    )
    (tmp_path / "a.py").write_text("x = 1\n")
    extra = _stale_row_currency(tmp_path, _decision(["a.py"], ["abc123"]), ["a.py"])
    assert extra["commits_since"] == 3
    assert calls["since_commit"] == "abc123"
    assert "moved_files" not in extra


def test_stale_row_reports_moved_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "repowise.server.mcp_server.tool_why.dashboard.commits_since",
        lambda *a, **k: 1,
    )
    extra = _stale_row_currency(tmp_path, _decision(["gone.py"]), ["gone.py"])
    assert extra["moved_files"] == ["gone.py"]


def test_stale_row_omits_commits_when_git_cannot_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "repowise.server.mcp_server.tool_why.dashboard.commits_since",
        lambda *a, **k: None,
    )
    (tmp_path / "a.py").write_text("x = 1\n")
    extra = _stale_row_currency(tmp_path, _decision(["a.py"]), ["a.py"])
    assert "commits_since" not in extra
