"""The deleted-file prune must not read a nested index as a mass deletion.

Git-metadata rows are keyed relative to the git root. When the index covers a
subdirectory, asking only "is this path under the indexed directory?" made
every one of them look deleted, so the prune refused the run and reported a
degraded update on every update.
"""

from __future__ import annotations

from pathlib import Path

import git as gitpython
import pytest

from repowise.core.pipeline.persist import _FileLiveness


@pytest.fixture
def nested(tmp_path: Path) -> Path:
    r = gitpython.Repo.init(tmp_path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "svc" / "module").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / "svc" / "module" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "other" / "b.py").write_text("b = 1\n", encoding="utf-8")
    r.index.add(["svc/module/a.py", "other/b.py"])
    r.index.commit("init")
    r.close()
    return tmp_path


def test_a_root_relative_path_that_exists_is_live(nested: Path) -> None:
    liveness = _FileLiveness(nested / "svc" / "module", git_root_keyed=True)

    assert liveness.is_live("svc/module/a.py")
    assert liveness.is_live("other/b.py")


def test_a_module_relative_path_is_still_live(nested: Path) -> None:
    assert _FileLiveness(nested / "svc" / "module").is_live("a.py")


def test_a_genuinely_missing_path_is_still_dead(nested: Path) -> None:
    assert not _FileLiveness(nested / "svc" / "module").is_live("gone.py")
    git_keyed = _FileLiveness(nested / "svc" / "module", git_root_keyed=True)
    assert not git_keyed.is_live("svc/module/gone.py")


def test_an_index_relative_path_is_not_resolved_against_the_git_root(nested: Path) -> None:
    # A deleted module file must not look live because the repo root has a
    # file of the same name.
    (nested / "a.py").write_text("root = 1\n", encoding="utf-8")
    (nested / "svc" / "module" / "a.py").unlink()
    r = gitpython.Repo(nested)
    r.git.rm("--cached", "-q", "svc/module/a.py")
    r.close()

    assert not _FileLiveness(nested / "svc" / "module").is_live("a.py")
    git_keyed = _FileLiveness(nested / "svc" / "module", git_root_keyed=True)
    assert not git_keyed.is_live("svc/module/a.py")
    assert git_keyed.is_live("a.py")


def test_the_repo_root_behaves_as_before(nested: Path) -> None:
    for liveness in (_FileLiveness(nested), _FileLiveness(nested, git_root_keyed=True)):
        assert liveness.is_live("other/b.py")
        assert not liveness.is_live("nope.py")
