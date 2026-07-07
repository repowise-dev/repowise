"""Tests for the serve discovery lockfile helpers."""

from __future__ import annotations

import json
import os

from repowise.cli.commands.serve_cmd import (
    _remove_serve_lock,
    _serve_lock_path,
    _write_serve_lock,
)


class TestServeLockPath:
    def test_no_index_dir_returns_none(self, tmp_path):
        assert _serve_lock_path(tmp_path) is None

    def test_repo_index_dir(self, tmp_path):
        (tmp_path / ".repowise").mkdir()
        path = _serve_lock_path(tmp_path)
        assert path == tmp_path / ".repowise" / "serve.lock.json"

    def test_workspace_dir(self, tmp_path):
        (tmp_path / ".repowise-workspace").mkdir()
        path = _serve_lock_path(tmp_path)
        assert path == tmp_path / ".repowise-workspace" / "serve.lock.json"

    def test_repo_dir_preferred_over_workspace(self, tmp_path):
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise-workspace").mkdir()
        path = _serve_lock_path(tmp_path)
        assert path is not None
        assert path.parent.name == ".repowise"


class TestServeLockLifecycle:
    def test_write_then_remove(self, tmp_path):
        (tmp_path / ".repowise").mkdir()
        lock = _serve_lock_path(tmp_path)
        assert lock is not None

        _write_serve_lock(lock, host="127.0.0.1", port=7337, ui_port=3000)
        data = json.loads(lock.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["port"] == 7337
        assert data["host"] == "127.0.0.1"
        assert data["url"] == "http://127.0.0.1:7337"
        assert data["ui_port"] == 3000
        assert data["server_version"]
        assert data["started_at"]

        _remove_serve_lock(lock)
        assert not lock.exists()

    def test_write_without_ui(self, tmp_path):
        (tmp_path / ".repowise").mkdir()
        lock = _serve_lock_path(tmp_path)
        _write_serve_lock(lock, host="127.0.0.1", port=7440, ui_port=None)
        data = json.loads(lock.read_text(encoding="utf-8"))
        assert data["ui_port"] is None

    def test_remove_leaves_foreign_lock(self, tmp_path):
        (tmp_path / ".repowise").mkdir()
        lock = _serve_lock_path(tmp_path)
        lock.write_text(json.dumps({"pid": os.getpid() + 99999, "port": 7337}))
        _remove_serve_lock(lock)
        assert lock.exists()

    def test_remove_missing_lock_is_noop(self, tmp_path):
        (tmp_path / ".repowise").mkdir()
        lock = _serve_lock_path(tmp_path)
        _remove_serve_lock(lock)  # must not raise
