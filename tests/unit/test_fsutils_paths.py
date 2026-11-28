"""Edge cases of the shared repo-relative path helpers in ``core.fsutils``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repowise.core.fsutils import comparable_path, repo_relative, strip_dir_prefix


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("a", "b.py"), "a/b.py"),
        ((), "."),
        (("x", "..", "y"), "x/../y"),  # lexical: no ".." folding
    ],
)
def test_repo_relative_lexical(tmp_path: Path, parts: tuple[str, ...], expected: str) -> None:
    path = tmp_path.joinpath(*parts)
    assert repo_relative(path, tmp_path) == expected
    assert repo_relative(str(path), str(tmp_path)) == expected


def test_repo_relative_outside_root_is_none(tmp_path: Path) -> None:
    root = tmp_path / "a"
    assert repo_relative(tmp_path / "b" / "c.py", root) is None
    assert repo_relative(tmp_path / "ab" / "c.py", root) is None  # sibling sharing a prefix
    assert repo_relative(tmp_path / "a" / ".." / "b", root) == "../b"  # lexical, so it stays inside


def test_repo_relative_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "y").mkdir()
    assert repo_relative(tmp_path / "x" / ".." / "y", tmp_path, resolve=True) == "y"
    assert repo_relative(tmp_path / "a" / ".." / ".." / "b", tmp_path, resolve=True) is None
    monkeypatch.chdir(tmp_path)
    assert repo_relative("y/f.py", tmp_path, resolve=True) == "y/f.py"  # relative reads against cwd
    assert repo_relative(tmp_path, tmp_path, resolve=True) == "."


def test_repo_relative_resolve_follows_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    link = tmp_path / "alias"
    try:
        os.symlink(root, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert repo_relative(link / "src", root) is None
    assert repo_relative(link / "src", root, resolve=True) == "src"


@pytest.mark.parametrize(
    ("path", "base", "expected"),
    [
        ("a/b/c.py", "a/b", "c.py"),
        ("a/bc/d.py", "a/b", "a/bc/d.py"),
        ("a/b", "a/b", "a/b"),
        ("a/b/", "a/b", ""),
        ("x.py", "", "x.py"),
        ("/a/b/c", "/a", "b/c"),
    ],
)
def test_strip_dir_prefix(path: str, base: str, expected: str) -> None:
    assert strip_dir_prefix(path, base) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a\\b.py", "a/b.py"),
        ("././a.py", "a.py"),
        (".github/ci.yml", ".github/ci.yml"),
        ("../a.py", "../a.py"),
        ("/abs/a.py", "/abs/a.py"),
        ("C:\\repo\\a.py", "C:/repo/a.py"),
        ("a//b/", "a//b/"),
        ("", ""),
        (None, ""),
    ],
)
def test_comparable_path(path: str | None, expected: str) -> None:
    assert comparable_path(path) == expected
