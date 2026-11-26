"""Which tests a module-level change to a measured Python file needs (``module_hunks``)."""

from __future__ import annotations

import asyncio
from difflib import SequenceMatcher

import pytest

from repowise.core.analysis.module_hunks import (
    Importer,
    ModuleChange,
    Readers,
    module_change,
    readers,
    users_of,
)

OLD = '''\
"""Settings."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

LIMIT = 3
NAMES = ("a", "b")


def helper(x: int = 2) -> int:
    return x + LIMIT


class Box:
    size = 1
'''


def _hunks(old: str, new: str) -> list[tuple[int, int, int, int]]:
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append((i1 + (i2 > i1), i2 - i1, j1 + (j2 > j1), j2 - j1))
    return out


def _change(new: str, *, back: bool = False):
    return module_change(OLD, new, _hunks(OLD, new), lambda: back)


def _swap(old: str, new: str) -> str:
    assert old in OLD
    return OLD.replace(old, new)


@pytest.mark.parametrize(
    ("old", "new", "names"),
    [
        ("LIMIT = 3", "LIMIT = 4", {"LIMIT"}),
        ("def helper(x: int = 2)", "def helper(x: int = 5)", {"helper"}),
        ("    size = 1", "    size = 2", {"Box"}),
        ("import os\n", "import os\nimport sys\n", {"sys"}),
        ("    from pathlib import Path\n", "    from pathlib import Path, PurePath\n", set()),
        ('"""Settings."""', '"""Settings, read once."""', set()),
    ],
)
def test_a_binding_change_names_what_it_binds(old: str, new: str, names: set[str]) -> None:
    assert _change(_swap(old, new)) == ModuleChange(frozenset(names), frozenset())


def test_a_removed_definition_is_named_removed() -> None:
    change = _change(_swap('NAMES = ("a", "b")\n', ""))
    assert change == ModuleChange(frozenset({"NAMES"}), frozenset({"NAMES"}))


@pytest.mark.parametrize(
    ("old", "new", "why"),
    [
        ("LIMIT = 3", "LIMIT = 3\nos.environ.setdefault('X', '1')", "runs at import"),
        ("LIMIT = 3", "LIMIT = 3\nos.environ['X'] = '1'", "stores into"),
        ("LIMIT = 3", "LIMIT = 3\nif os.name == 'nt':\n    LIMIT = 4", "runs at import"),
        ("def helper", "@register('x')\ndef helper", "may register"),
        ("class Box:", "class Box(metaclass=type):", "metaclass"),
        ("if TYPE_CHECKING:\n", "if TYPE_CHECKING:\n    print(1)\n", "TYPE_CHECKING"),
    ],
)
def test_a_statement_doing_more_than_binding_keeps_the_graph(old: str, new: str, why: str) -> None:
    out = _change(_swap(old, new))
    assert isinstance(out, str) and why in out


def test_a_pure_decorator_is_a_binding() -> None:
    new = _swap(
        "class Box:\n    size = 1", "@dataclass(frozen=True)\nclass Box:\n    size: int = 1"
    )
    assert _change(new) == ModuleChange(frozenset({"Box"}), frozenset())


def test_an_import_leading_back_keeps_the_graph() -> None:
    new = _swap("import os\n", "import os\nfrom app import user\n")
    assert _change(new, back=False) == ModuleChange(frozenset({"user"}), frozenset())
    assert "leads back" in _change(new, back=True)


def test_a_file_that_does_not_parse_keeps_the_graph() -> None:
    assert isinstance(module_change(OLD, "def (", [(1, 1, 1, 1)], lambda: False), str)


# -- following the names ----------------------------------------------------------

USER = """\
from app.settings import LIMIT, NAMES

DOUBLE = LIMIT * 2


def use():
    return LIMIT
"""


def _readers(change: ModuleChange, graph: dict, sources: dict, **kw):
    async def importers(module: str):
        return [Importer(p, frozenset(n)) for p, n in graph.get(module, ())]

    return asyncio.run(
        readers(
            "app/settings.py",
            change,
            importers,
            sources.get,
            new_text=kw.pop("new_text", OLD),
            is_test=lambda p: p.startswith("tests/test_"),
            is_support=lambda p: p.startswith("tests/") and not p.startswith("tests/test_"),
            **kw,
        )
    )


GRAPH = {
    "app/settings.py": [
        ("app/user.py", {"LIMIT", "NAMES"}),
        ("app/other.py", {"NAMES"}),
        ("tests/test_settings.py", {"LIMIT"}),
        ("tests/test_names.py", {"NAMES"}),
    ],
    "app/user.py": [("app/api.py", {"DOUBLE"}), ("tests/test_user.py", {"use"})],
    "app/api.py": [("tests/test_api.py", {"handler"})],
}
SOURCES = {
    "app/user.py": USER,
    "app/other.py": "from app.settings import NAMES\n",
    "app/api.py": "from app.user import DOUBLE\n\ndef handler():\n    return DOUBLE\n",
}
LIMIT_ONLY = ModuleChange(frozenset({"LIMIT"}), frozenset())


def test_names_flow_through_importers_and_module_level_derived_names() -> None:
    out = _readers(LIMIT_ONLY, GRAPH, SOURCES)
    assert isinstance(out, Readers)
    # user.py reads LIMIT; DOUBLE derives from it at import, and api.py reads DOUBLE.
    assert out.modules == {"app/settings.py", "app/user.py", "app/api.py"}
    # Test files importing a changed or derived name; test_names takes only NAMES.
    assert out.tests == {"tests/test_settings.py"}


def test_a_test_importing_only_unchanged_names_is_no_reader() -> None:
    users = {"app/settings.py": {"tests/test_a.py"}, "app/user.py": {"tests/test_user.py"}}
    out = _readers(LIMIT_ONLY, GRAPH, SOURCES)
    assert users_of(out, users) == {
        "tests/test_a.py",
        "tests/test_user.py",
        "tests/test_settings.py",
    }


@pytest.mark.parametrize(
    ("graph", "sources", "why"),
    [
        ({"app/settings.py": [("tests/conftest.py", {"LIMIT"})]}, {}, "conftest"),
        ({"app/settings.py": [("app/x.py", {"*"})]}, {}, "star-imports"),
        (
            {"app/settings.py": [("app/x.py", {"LIMIT"})]},
            {"app/x.py": "from app.settings import LIMIT\nregister(LIMIT)\n"},
            "reads a changed name at import",
        ),
        (
            {"app/settings.py": [("app/x.py", {"LIMIT"})]},
            {
                "app/x.py": "from functools import cache\nfrom app.settings import LIMIT\n\n"
                "@cache\ndef get():\n    return LIMIT\n"
            },
            "runs once",
        ),
    ],
)
def test_a_reader_the_map_cannot_follow_keeps_the_graph(graph, sources, why) -> None:
    out = _readers(LIMIT_ONLY, graph, sources)
    assert isinstance(out, str) and why in out


def test_a_removed_name_still_imported_keeps_the_graph() -> None:
    gone = ModuleChange(frozenset({"NAMES"}), frozenset({"NAMES"}))
    assert "removes" in _readers(gone, GRAPH, SOURCES)


def test_a_once_per_process_function_reading_a_changed_name_keeps_the_graph() -> None:
    out = _readers(LIMIT_ONLY, GRAPH, SOURCES, once={"app/settings.py::helper"})
    assert isinstance(out, str) and "helper" in out


def test_a_test_helper_passes_the_change_to_every_test_importing_it() -> None:
    graph = {
        "app/settings.py": [("tests/helpers.py", {"LIMIT"})],
        "tests/helpers.py": [("tests/test_h.py", {"make"}), ("tests/test_i.py", set())],
    }
    out = _readers(LIMIT_ONLY, graph, {"tests/helpers.py": "from app.settings import LIMIT\n"})
    assert isinstance(out, Readers)
    assert out.tests == {"tests/test_h.py", "tests/test_i.py"}
    assert out.modules == {"app/settings.py"}


@pytest.mark.parametrize(
    "new",
    [
        "LIMIT = compute()",
        "LIMIT = (n := 4)",
        "LIMIT: int = registry.add(4)",
    ],
)
def test_a_binding_whose_value_runs_code_keeps_the_graph(new: str) -> None:
    out = _change(_swap("LIMIT = 3", new))
    assert isinstance(out, str) and "runs at import" in out


def test_a_binding_from_a_pure_builder_is_a_binding() -> None:
    new = _swap("LIMIT = 3", "LIMIT = frozenset({3})")
    assert _change(new) == ModuleChange(frozenset({"LIMIT"}), frozenset())


def test_a_module_imported_by_name_from_its_package_is_imported_whole() -> None:
    """``from app import settings`` then ``settings.LIMIT``: the edge names the module."""
    graph = {
        "app/settings.py": [("tests/test_direct.py", {"settings"})],
        "app/__init__.py": [("tests/test_barrel.py", {"settings"}), ("tests/test_x.py", {"x"})],
    }
    out = _readers(LIMIT_ONLY, graph, {})
    assert isinstance(out, Readers)
    assert out.tests == {"tests/test_direct.py", "tests/test_barrel.py"}
