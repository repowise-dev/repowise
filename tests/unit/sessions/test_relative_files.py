"""Path normalization for the paths a transcript records.

The staged decision rows and the index disagreed about what a path is: the
miner wrote whatever the tool call was given, which is absolute, while the
index holds repo-relative POSIX. These pin the seam from both sides, because
a store written before the miner was fixed still has to read correctly.
"""

from __future__ import annotations

import os

from repowise.core.sessions.events import relative_files

ROOT = r"C:\Users\x\repo"
POSIX_ROOT = "/home/x/repo"


def test_an_absolute_path_under_the_root_becomes_relative_posix():
    assert relative_files([ROOT + r"\packages\core\a.py"], ROOT) == ["packages/core/a.py"]
    assert relative_files([POSIX_ROOT + "/packages/core/a.py"], POSIX_ROOT) == [
        "packages/core/a.py"
    ]


def test_an_absolute_path_outside_the_root_is_dropped():
    assert relative_files([r"C:\Users\x\other\a.py"], ROOT) == []
    assert relative_files(["/home/x/other/a.py"], POSIX_ROOT) == []


def test_a_windows_path_reads_as_a_path_on_any_interpreter():
    """Not as a relative name that happens to start with a drive letter."""
    assert relative_files([r"D:\elsewhere\a.py"], POSIX_ROOT) == []


def test_relativizing_twice_is_the_same_as_relativizing_once():
    once = relative_files([ROOT + r"\a.py", r"C:\Users\x\other\b.py"], ROOT)
    assert relative_files(once, ROOT) == once == ["a.py"]


def test_an_already_relative_path_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    """The defect this guards: resolving against the process cwd.

    ``os.path.relpath`` on a relative input resolves it against wherever the
    process happens to be, which is the repository root only by luck. From
    anywhere else every file escaped the root and was dropped, so a promoted
    decision silently lost its whole scope.
    """
    monkeypatch.chdir(tmp_path)
    assert relative_files(["packages/core/a.py"], ROOT) == ["packages/core/a.py"]
    assert relative_files(["packages/core/a.py"], os.getcwd()) == ["packages/core/a.py"]


def test_a_relative_path_that_escapes_the_root_is_still_dropped():
    assert relative_files(["../sibling/a.py", "..", "a/../../b.py"], ROOT) == []


def test_order_is_first_seen_and_duplicates_collapse():
    files = [ROOT + r"\b.py", "a.py", ROOT + "/b.py", "a.py"]
    assert relative_files(files, ROOT) == ["b.py", "a.py"]
