"""Tests for index storage helpers used by ``repowise status``."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands import status_cmd


def test_index_storage_bytes_sums_repowise_files(tmp_path: Path) -> None:
    repowise = tmp_path / ".repowise"
    repowise.mkdir()
    (repowise / "wiki.db").write_bytes(b"x" * 100)
    nested = repowise / "lancedb" / "pages"
    nested.mkdir(parents=True)
    (nested / "chunk.lance").write_bytes(b"y" * 50)

    assert status_cmd._index_storage_bytes(repowise) == 150


def test_index_storage_bytes_missing_dir() -> None:
    assert status_cmd._index_storage_bytes(Path("/no/such/repowise/dir")) == 0


def test_health_line_for_a_repo_with_no_scored_file(monkeypatch) -> None:
    """Every file is in a language health has no dialect for: say so, never crash."""
    from repowise.cli.commands import status_cmd

    monkeypatch.setattr(
        status_cmd,
        "_query_health",
        lambda _path: {"average_health": None, "unanalysed_file_count": 2992},
    )
    line = status_cmd._query_health_line(Path("."))
    assert line is not None and "not analysed" in line and "2992" in line
