"""``repowise.core.git_repo.Repo`` releases git without forcing garbage collection."""

from __future__ import annotations

import gc

import git
import pytest

from repowise.core.git_repo import Repo


@pytest.fixture
def repo_path(tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    repo.index.add(["a.txt"])
    repo.index.commit("init")
    repo.close()
    return tmp_path


def test_close_and_del_never_collect(repo_path, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(gc, "collect", lambda *a, **k: calls.append(1) or 0)
    repo = Repo(repo_path)
    repo.git.version()
    repo.close()
    repo.__del__()
    assert calls == []


def test_close_stops_persistent_git_processes(repo_path) -> None:
    repo = Repo(repo_path)
    repo.git.get_object_header("HEAD")
    assert repo.git.cat_file_header is not None
    repo.close()
    assert repo.git.cat_file_header is None
