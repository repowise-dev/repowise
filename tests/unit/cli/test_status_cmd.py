"""Tests for status_cmd edge cases."""
from __future__ import annotations

from pathlib import Path

from repowise.cli.commands.status_cmd import _query_health_line


def test_query_health_line_returns_none_when_average_health_is_none(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    repowise_dir = repo / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "wiki.db").touch()

    fake_data = {
        "average_health": None,
        "hotspot_health": None,
        "worst_performer_path": "foo.py",
        "worst_performer_score": None,
        "file_count": 1,
    }

    monkeypatch.setattr(
        "repowise.cli.commands.status_cmd.run_async",
        lambda fn: fake_data,
    )
    result = _query_health_line(repo)
    assert result is None
