"""A Python import inside a function binds its names for that function only.

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


def _calls(tmp_path: Path, app: str) -> set[tuple[str, str]]:
    for rel, text in {**_PKG, "app.py": app}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    parser = ASTParser()
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return {
        (u, v)
        for u, v, data in builder.graph().edges(data=True)
        if data.get("edge_type") == "calls"
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
