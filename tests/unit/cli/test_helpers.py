"""Unit tests for repowise.cli.helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.helpers import (
    ensure_repowise_dir,
    get_db_url_for_repo,
    get_head_commit,
    get_repowise_dir,
    load_state,
    resolve_repo_path,
    run_async,
    save_state,
)

# ---------------------------------------------------------------------------
# run_async
# ---------------------------------------------------------------------------


class TestRunAsync:
    def test_returns_coroutine_result(self):
        async def _add(a, b):
            return a + b

        assert run_async(_add(3, 4)) == 7

    def test_raises_exception_from_coroutine(self):
        async def _fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_async(_fail())


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestResolveRepoPath:
    def test_none_defaults_to_cwd(self):
        result = resolve_repo_path(None)
        assert result == Path.cwd().resolve()

    def test_resolves_relative_path(self, tmp_path):
        import os

        old = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = resolve_repo_path(".")
            assert result == tmp_path.resolve()
        finally:
            os.chdir(old)

    def test_resolves_absolute_path(self, tmp_path):
        result = resolve_repo_path(str(tmp_path))
        assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# .repowise/ directory
# ---------------------------------------------------------------------------


class TestrepowiseDir:
    def test_get_repowise_dir(self, tmp_path):
        assert get_repowise_dir(tmp_path) == tmp_path / ".repowise"

    def test_ensure_repowise_dir_creates(self, tmp_path):
        d = ensure_repowise_dir(tmp_path)
        assert d.exists()
        assert d == tmp_path / ".repowise"

    def test_ensure_repowise_dir_idempotent(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        d = ensure_repowise_dir(tmp_path)
        assert d.exists()


# ---------------------------------------------------------------------------
# DB URL
# ---------------------------------------------------------------------------


class TestDbUrl:
    def test_sqlite_url(self, tmp_path):
        url = get_db_url_for_repo(tmp_path)
        assert url.startswith("sqlite+aiosqlite:///")
        assert "wiki.db" in url


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


class TestStateFile:
    def test_load_missing_returns_empty(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        assert load_state(tmp_path) == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        state = {"last_sync_commit": "abc123", "total_pages": 42}
        save_state(tmp_path, state)
        loaded = load_state(tmp_path)
        assert loaded == state

    def test_save_creates_repowise_dir(self, tmp_path):
        save_state(tmp_path, {"key": "value"})
        assert (tmp_path / ".repowise" / "state.json").exists()


# ---------------------------------------------------------------------------
# Git HEAD
# ---------------------------------------------------------------------------


class TestGetHeadCommit:
    def test_non_git_returns_none(self, tmp_path):
        assert get_head_commit(tmp_path) is None
