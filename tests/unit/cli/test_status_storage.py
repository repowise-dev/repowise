"""Tests for index storage helpers used by ``repowise status``."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_path_mapping_valid_with_no_db_is_true(tmp_path: Path) -> None:
    """Nothing to diverge from: an unindexed dir is not a broken mapping."""
    assert status_cmd._path_mapping_valid(tmp_path) is True


def test_path_mapping_valid_false_when_repo_row_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #1748: .repowise/ exists but no Repository row matches the
    checkout's local_path, so the index/checkout identity has diverged."""
    # An empty .repowise/wiki.db with no Repository row (identity lost).
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "wiki.db").write_bytes(b"")

    monkeypatch.setenv("REPOWISE_DATA_DIR", str(tmp_path / ".repowise"))
    monkeypatch.setenv("REPOWISE_DB_PATH", str(tmp_path / ".repowise" / "wiki.db"))

    # No repo was ever upserted -> the mapping must read as invalid.
    assert status_cmd._path_mapping_valid(tmp_path) is False
