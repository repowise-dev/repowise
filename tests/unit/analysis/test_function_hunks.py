"""Which changed files the per-test map answers alone: every hunk inside a function body."""

from __future__ import annotations

import subprocess
from difflib import SequenceMatcher

import pytest

from repowise.core.analysis.function_hunks import (
    _decorator_seeds,
    function_scoped_lines,
    touched_functions,
)

OLD = '''\
from __future__ import annotations

LIMIT = 3


def helper(x: int = 2) -> int:
    """Doc."""
    y = x + 1
    return y


class Box:
    size = 1

    def grow(self) -> int:
        return self.size + LIMIT
'''


def _swap(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new)


def _hunks(old: str, new: str) -> list[tuple[int, int, int, int]]:
    """The ``--unified=0`` hunks git reports, as ``(old_start, old_count, new_start, new_count)``."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            # git names the line before a pure insertion or deletion.
            out.append((i1 + (i2 > i1), i2 - i1, j1 + (j2 > j1), j2 - j1))
    return out


def _touched(old: str, new: str, side: str = "old", path: str = "src/m.py"):
    return touched_functions(path, _hunks(old, new), side, old, new)


def test_a_change_inside_a_function_body_names_that_function() -> None:
    found = _touched(OLD, _swap(OLD, "    y = x + 1\n", "    y = x + 2\n"))
    assert found is not None
    assert [b.chain for b in found.bodies] == [("helper",)]
    # The whole body is looked up, from its first statement to its last line.
    assert found.lines == {7, 8, 9}
    assert found.symbol_ids() == {"src/m.py::helper"}


def test_a_method_body_change_names_the_method() -> None:
    new = _swap(OLD, "return self.size + LIMIT", "return self.size * LIMIT")
    found = _touched(OLD, new, "new")
    assert found is not None
    assert found.symbol_ids() == {"src/m.py::Box::grow"}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("LIMIT = 3\n", "LIMIT = 4\n"),  # a module constant
        ("def helper(x: int = 2)", "def helper(x: int = 5)"),  # a default value
        ("    size = 1\n", "    size = 2\n"),  # a class body
    ],
)
def test_a_module_level_change_keeps_the_graph(old, new) -> None:
    assert _touched(OLD, _swap(OLD, old, new)) is None


def test_a_line_moved_out_of_a_function_is_module_level() -> None:
    assert _touched(OLD, _swap(OLD, "    return y\n", "    return y\nprint(LIMIT)\n")) is None


def _with_added(added: str, base: str = OLD) -> str:
    new = _swap(base, "    y = x + 1\n", "    y = x + 2\n")
    return _swap(new, "\n\nclass Box:", f"\n\n{added}\n\nclass Box:")


def test_a_new_inert_helper_beside_a_body_change_still_qualifies() -> None:
    found = _touched(OLD, _with_added("def _twice(v: int) -> int:\n    return v * 2\n"))
    assert found is not None
    assert [b.chain for b in found.bodies] == [("helper",)]


@pytest.mark.parametrize(
    "added",
    [
        "@cache\ndef _twice(v):\n    return v\n",  # a decorator runs at import
        "def _twice(v=compute()):\n    return v\n",  # so does a default value
        "def helper2(v=LIMIT):\n    return v\n",  # a default that is not a literal
        "import repowise\n",  # a repo import can fail or run code
        "_PAT = build()\n",  # a constant computed by a call
        "LIMIT = 4\n",  # a name the old file already binds
        "def len(v):\n    return v\n",  # shadows a builtin
    ],
)
def test_an_added_statement_that_may_run_at_import_keeps_the_graph(added) -> None:
    assert _touched(OLD, _with_added(added)) is None


@pytest.mark.parametrize(
    "added",
    [
        "import json\n",
        "from contextlib import suppress\n",
        "_NAMES = frozenset({'a', 'b'})\n",
        "_PAT = re.compile(r'[a-z]+', re.I)\n",
    ],
)
def test_an_added_inert_statement_qualifies(added) -> None:
    old = _swap(OLD, "LIMIT = 3\n", "import re\n\nLIMIT = 3\n")
    assert _touched(old, _with_added(added, old)) is not None


def test_a_pattern_that_does_not_compile_is_not_inert() -> None:
    old = _swap(OLD, "LIMIT = 3\n", "import re\n\nLIMIT = 3\n")
    assert _touched(old, _with_added("_PAT = re.compile('(')\n", old)) is None


def test_a_file_that_only_adds_functions_keeps_the_graph() -> None:
    new = _swap(OLD, "\n\nclass Box:", "\n\ndef _twice(v):\n    return v * 2\n\n\nclass Box:")
    assert _touched(OLD, new) is None


def test_a_dunder_method_also_names_its_class() -> None:
    old = "class A:\n    def __init__(self):\n        self.x = 1\n"
    found = _touched(old, old.replace("1", "2"), "new", "m.py")
    assert found is not None
    assert found.symbol_ids() == {"m.py::A::__init__", "m.py::A"}


async def test_no_measured_side_means_no_file_qualifies() -> None:
    """A map measured at neither end of the change is matched by file, never trusted."""
    assert await function_scoped_lines(None, "r", ".", object(), None, {"a.py": {1}}) == {}


def test_memos_and_fixtures_shared_across_files_seed_the_symbol_below(tmp_path) -> None:
    (tmp_path / "m.py").write_text(
        "@cache  # once per process\n"
        "def memo():\n    pass\n\n"
        '@pytest.fixture(scope="session")\n'
        "def shared():\n    pass\n\n"
        '@pytest.fixture(scope="module")\n'
        "def per_file():\n    pass\n\n"
        "@pytest.fixture\n"
        "def per_test():\n    pass\n\n"
        "@register\n"
        "def plugin():\n    pass\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "m.py"], cwd=tmp_path, check=True)
    by_file = {
        "m.py": [(2, "m.py::memo"), (6, "m.py::shared"), (10, "m.py::per_file"),
                 (14, "m.py::per_test"), (18, "m.py::plugin")],
    }  # fmt: skip
    by_name = {"register": {"r.py::register"}}
    assert _decorator_seeds(tmp_path, by_file, by_name) == {
        "m.py::memo",
        "m.py::shared",
        "r.py::register",
    }


@pytest.mark.parametrize(
    "added",
    [
        "from os import *\n",  # rebinds any name the module's functions read
        "def __getattr__(name):\n    return name\n",  # changes every attribute lookup
        "def twice(v):\n    return v\n",  # public, so ``from m import *`` exports it
    ],
)
def test_an_added_name_an_importer_can_see_keeps_the_graph(added) -> None:
    assert _touched(OLD, _with_added(added)) is None


def test_a_public_function_is_inert_when_all_limits_the_exports() -> None:
    old = _swap(OLD, "LIMIT = 3\n", "__all__ = ['helper']\n\nLIMIT = 3\n")
    assert _touched(old, _with_added("def twice(v):\n    return v\n", old)) is not None


def test_a_blank_or_hash_line_inside_a_module_string_is_a_change() -> None:
    old = _swap(OLD, "LIMIT = 3\n", 'PROMPT = """\n# Heading\n\nbody\n"""\nLIMIT = 3\n')
    new = _swap(old, "# Heading\n", "# Title\n")
    new = _swap(new, "    y = x + 1\n", "    y = x + 2\n")
    assert _touched(old, new) is None


def test_a_form_feed_does_not_shift_line_numbers() -> None:
    old = _swap(OLD, "LIMIT = 3\n", "LIMIT = 3  # \x0c page\n")
    found = _touched(old, _swap(old, "    y = x + 1\n", "    y = x + 2\n"))
    assert found is not None
    assert found.lines == {7, 8, 9}


def test_an_insertion_between_blank_lines_still_names_its_function() -> None:
    old = "def f():\n    a = 1\n\n\n    b = 2\n\n\ndef g():\n    return 1\n"
    new = "def f():\n    a = 1\n\n    c = 3\n\n    b = 2\n\n\ndef g():\n    return 2\n"
    # Line 3 of the old side is blank and so is line 4: f holds both.
    hunks = [(3, 0, 4, 2), (9, 1, 10, 1)]
    found = touched_functions("m.py", hunks, "old", old, new)
    assert found is not None
    assert {b.chain for b in found.bodies} == {("f",), ("g",)}


def test_a_hunk_no_function_holds_on_the_measured_side_keeps_the_graph() -> None:
    old = "def f():\n    a = 1\n\n\ndef g():\n    return 1\n"
    new = "def f():\n    a = 1\n\n    c = 3\n\ndef g():\n    return 2\n"
    # The new line sits in f, but its old neighbours (lines 3 and 4) are past f's end.
    assert touched_functions("m.py", [(3, 0, 4, 1), (6, 1, 7, 1)], "old", old, new) is None


def test_the_run_once_cache_sees_uncommitted_python_edits(tmp_path) -> None:
    from repowise.core.analysis.function_hunks import _tree_stamp

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    git("init", "-q")
    git("add", "m.py")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    clean = _tree_stamp(tmp_path)
    (tmp_path / "m.py").write_text("@register\ndef f():\n    pass\n", encoding="utf-8")
    edited = _tree_stamp(tmp_path)
    (tmp_path / "n.py").write_text("main()\n", encoding="utf-8")
    assert len({clean, edited, _tree_stamp(tmp_path)}) == 3
    assert None not in {clean, edited}
