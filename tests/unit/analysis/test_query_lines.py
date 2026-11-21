"""``query_lines``: a per-test map measured before the change's base still matches by line.

Real git repositories: the map's commit *M*, the change's base *B* and head *H*.
"""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.analysis.changed_lines import change_set
from repowise.core.analysis.test_collection import query_lines

TEN = "".join(f"v{i} = {i}\n" for i in range(1, 11))


def _git(cwd, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _commit(repo, files: dict[str, str | None], message: str) -> str:
    for path, text in files.items():
        if text is None:
            _git(repo, "rm", "-q", path)
        else:
            (repo / path).write_text(text, encoding="utf-8")
            _git(repo, "add", path)
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _edit(text: str, line: int, new: str) -> str:
    lines = text.splitlines(keepends=True)
    lines[line - 1] = new
    return "".join(lines)


def test_lines_move_back_through_a_pure_shift(repo) -> None:
    measured = _commit(repo, {"mod.py": TEN, "other.py": TEN}, "map")
    base_text = "a = 0\nb = 0\nc = 0\n" + TEN  # three lines on top: v5 is now line 8
    _commit(repo, {"mod.py": base_text}, "shift")
    _commit(
        repo,
        {"mod.py": _edit(base_text, 8, "v5 = 50\n"), "other.py": _edit(TEN, 2, "x\n")},
        "change",
    )
    change = change_set(str(repo), "HEAD~1..HEAD")
    assert query_lines(change, measured, repo) == {"mod.py": {5}, "other.py": {2}}
    # Without a checkout, nothing moves the lines: matched by file.
    assert query_lines(change, measured) == {"mod.py": None, "other.py": None}


def test_a_line_edited_since_the_map_is_matched_by_file(repo) -> None:
    measured = _commit(repo, {"mod.py": TEN, "other.py": TEN}, "map")
    base_text = _edit(TEN, 5, "v5 = 'rewritten'\n")
    _commit(repo, {"mod.py": base_text}, "rewrite v5")
    change_text = _edit(base_text, 5, "v5 = 'again'\n")
    _commit(repo, {"mod.py": change_text, "other.py": _edit(TEN, 9, "y\n")}, "change")
    change = change_set(str(repo), "HEAD~1..HEAD")
    # Only the file with the rewritten line falls back; the other still matches by line.
    assert query_lines(change, measured, repo) == {"mod.py": None, "other.py": {9}}


def test_a_line_beside_code_removed_since_the_map_is_matched_by_file(repo) -> None:
    measured = _commit(repo, {"mod.py": TEN}, "map")
    base_text = "".join(TEN.splitlines(keepends=True)[:4] + TEN.splitlines(keepends=True)[6:])
    _commit(repo, {"mod.py": base_text}, "drop v5 and v6")
    _commit(repo, {"mod.py": _edit(base_text, 5, "v7 = 70\n")}, "change")  # v7 sat beside them
    change = change_set(str(repo), "HEAD~1..HEAD")
    assert query_lines(change, measured, repo) == {"mod.py": None}


def test_a_map_off_the_base_history_is_matched_by_file(repo) -> None:
    _commit(repo, {"mod.py": TEN}, "root")
    _git(repo, "checkout", "-qb", "side")
    measured = _commit(repo, {"side.py": "s = 1\n"}, "side only")
    _git(repo, "checkout", "-q", "-")
    _commit(repo, {"mod.py": _edit(TEN, 3, "v3 = 30\n")}, "change")
    change = change_set(str(repo), "HEAD~1..HEAD")
    assert query_lines(change, measured, repo) == {"mod.py": None}
    assert query_lines(change, "0" * 40, repo) == {"mod.py": None}  # not in the clone


def test_a_file_renamed_since_the_map_is_matched_by_file(repo) -> None:
    measured = _commit(repo, {"old.py": TEN}, "map")
    _git(repo, "mv", "old.py", "new.py")
    _git(repo, "commit", "-qm", "rename")
    _commit(repo, {"new.py": _edit(TEN, 4, "v4 = 40\n")}, "change")
    change = change_set(str(repo), "HEAD~1..HEAD")
    assert query_lines(change, measured, repo) == {"new.py": None}


def test_a_map_at_either_end_needs_no_git(repo) -> None:
    base = _commit(repo, {"mod.py": TEN}, "base")
    head = _commit(repo, {"mod.py": "top = 0\n" + _edit(TEN, 3, "v3 = 30\n")}, "change")
    change = change_set(str(repo), "HEAD~1..HEAD")
    assert query_lines(change, base) == {"mod.py": {1, 3}}
    assert query_lines(change, head) == {"mod.py": {1, 4}}
