"""A Python import inside a function binds its names for that function only.

Elsewhere in the file the one such import is still read, as a guess: the
``import_elsewhere`` origin at 0.50, never for a parameter, and not at all where
two function-local imports bind the name to different things.

``import pkg.mod`` binds ``pkg``: the module is reached as ``pkg.mod``, and the
bare ``mod`` stays free for a parameter or local.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_PKG = {
    "pkg/__init__.py": "",
    "pkg/util.py": "def work():\n    pass\n",
    "pkg/mod.py": "def work():\n    pass\n",
}


def _edges(
    tmp_path: Path, app: str, kind: str = "calls", files: dict[str, str] | None = None
) -> dict[tuple[str, str], dict]:
    for rel, text in {**_PKG, **(files or {}), "app.py": app}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    parser = ASTParser()
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return {
        (u, v): data
        for u, v, data in builder.graph().edges(data=True)
        if data.get("edge_type") == kind
    }


def _calls(tmp_path: Path, app: str) -> set[tuple[str, str]]:
    """The call edges an import binds outright; guesses left out."""
    return {
        pair
        for pair, data in _edges(tmp_path, app).items()
        if data.get("resolution_origin") != "import_elsewhere"
    }


def _guesses(tmp_path: Path, app: str) -> dict[tuple[str, str], float]:
    return {
        pair: data["confidence"]
        for pair, data in _edges(tmp_path, app).items()
        if data.get("resolution_origin") == "import_elsewhere"
    }


@pytest.mark.parametrize(
    "statement", ["from pkg import util as mod", "import pkg.util as mod"]
)
def test_function_import_does_not_bind_a_parameter_elsewhere(tmp_path: Path, statement: str) -> None:
    app = (
        f"def first():\n    {statement}\n    mod.work()\n\n"
        "def second(mod):\n    mod.work()\n"
    )
    assert _calls(tmp_path, app) == {("app.py::first", "pkg/util.py::work")}


def test_function_import_does_not_bind_a_bare_name_elsewhere(tmp_path: Path) -> None:
    app = "def first():\n    from pkg.util import work\n    work()\n\ndef second():\n    work()\n"
    assert _calls(tmp_path, app) == {("app.py::first", "pkg/util.py::work")}


def test_module_star_import_still_reaches_a_name_another_function_imports(
    tmp_path: Path,
) -> None:
    app = (
        "from pkg.util import *\n\n"
        "def first():\n    from pkg.mod import work\n    work()\n\n"
        "def second():\n    work()\n"
    )
    assert _calls(tmp_path, app) == {
        ("app.py::first", "pkg/mod.py::work"),
        ("app.py::second", "pkg/util.py::work"),
    }


def test_same_import_in_two_functions_binds_in_both(tmp_path: Path) -> None:
    body = "    from pkg import util\n    util.work()\n"
    app = f"def first():\n{body}\ndef second():\n{body}\ndef third(util):\n    util.work()\n"
    assert _calls(tmp_path, app) == {
        ("app.py::first", "pkg/util.py::work"),
        ("app.py::second", "pkg/util.py::work"),
    }


def test_function_import_reaches_a_nested_function(tmp_path: Path) -> None:
    app = (
        "class A:\n"
        "    def m(self):\n"
        "        import pkg.util as u\n"
        "        def inner():\n"
        "            u.work()\n"
        "        return inner\n"
        "    def n(self, u):\n"
        "        u.work()\n"
    )
    assert _calls(tmp_path, app) == {("app.py::A::m", "pkg/util.py::work")}


def test_module_level_copy_binds_file_wide(tmp_path: Path) -> None:
    app = (
        "def first():\n    import pkg.util as u\n    u.work()\n\n"
        "import pkg.util as u\n\n"
        "def second():\n    u.work()\n"
    )
    assert _calls(tmp_path, app) == {
        ("app.py::first", "pkg/util.py::work"),
        ("app.py::second", "pkg/util.py::work"),
    }


def test_dotted_import_binds_the_dotted_path_not_the_last_name(tmp_path: Path) -> None:
    app = "import pkg.mod\n\ndef f():\n    pkg.mod.work()\n\ndef g(mod):\n    mod.work()\n"
    assert _calls(tmp_path, app) == {("app.py::f", "pkg/mod.py::work")}


def test_another_functions_import_is_read_as_a_guess_for_a_local(tmp_path: Path) -> None:
    app = (
        "def first():\n    from pkg import util\n    util.work()\n\n"
        "def second():\n    util = make()\n    util.work()\n"
    )
    assert _calls(tmp_path, app) == {("app.py::first", "pkg/util.py::work")}
    assert _guesses(tmp_path, app) == {("app.py::second", "pkg/util.py::work"): 0.5}


def test_another_functions_import_is_read_as_a_guess_for_a_bare_call(tmp_path: Path) -> None:
    app = "def first():\n    from pkg.util import work\n    work()\n\ndef second():\n    work()\n"
    assert _guesses(tmp_path, app) == {("app.py::second", "pkg/util.py::work"): 0.5}


def test_no_guess_for_a_parameter(tmp_path: Path) -> None:
    app = (
        "def first():\n    from pkg import util\n    util.work()\n\n"
        "def second(util):\n    util.work()\n"
    )
    assert _guesses(tmp_path, app) == {}


def test_no_guess_where_two_function_imports_disagree(tmp_path: Path) -> None:
    app = (
        "def first():\n    from pkg import util as m\n    m.work()\n\n"
        "def second():\n    from pkg import mod as m\n    m.work()\n\n"
        "def third():\n    m = make()\n    m.work()\n"
    )
    assert _calls(tmp_path, app) == {
        ("app.py::first", "pkg/util.py::work"),
        ("app.py::second", "pkg/mod.py::work"),
    }
    assert _guesses(tmp_path, app) == {}


def test_heritage_reads_another_functions_import_as_a_guess(tmp_path: Path) -> None:
    app = (
        "def first():\n    from pkg.util import Base\n    return Base\n\n"
        "class Child(Base):\n    pass\n"
    )
    extends = _edges(
        tmp_path, app, "extends", {"pkg/util.py": "class Base:\n    pass\n"}
    )
    assert {pair: data["confidence"] for pair, data in extends.items()} == {
        ("app.py::Child", "pkg/util.py::Base"): 0.5
    }


def test_no_guess_for_a_bare_call_to_a_parameter(tmp_path: Path) -> None:
    app = (
        "def first():\n    from pkg.util import work\n    work()\n\n"
        "def second(work):\n    work()\n"
    )
    assert _guesses(tmp_path, app) == {}
