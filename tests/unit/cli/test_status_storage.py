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
