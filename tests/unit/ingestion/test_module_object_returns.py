"""A Python function that returns an imported module object types its call as that module.

``def load(): import mod; return mod`` hands the module on, so ``load().f()``
and ``m = load(); m.f()`` call ``mod.f``. Two modules here declare
``backend_of``, so only the returned module can pick the right one.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_CATALOG = "pkg/catalog.py::backend_of"
_OTHER = "pkg/other.py::backend_of"

_LIBRARY = {
    "pkg/__init__.py": "",
    "pkg/catalog.py": "def backend_of(x):\n    return x\n",
    "pkg/other.py": "def backend_of(x):\n    return x\n",
    "app/__init__.py": "",
}


def _callees(
    tmp_path: Path, use: str, library: dict[str, str] = _LIBRARY
) -> dict[str, set[tuple[str, str]]]:
    """``{caller name: {(callee, origin)}}`` for the calls ``app/use.py`` makes into ``pkg``."""
    parser = ASTParser()
    builder = GraphBuilder()
    for rel, text in {**library, "app/use.py": use}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(path),
            language="python",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(parser.parse_file(info, text.encode()))
    graph = builder.build()
    out: dict[str, set[tuple[str, str]]] = {}
    for src, tgt, data in graph.edges(data=True):
        calls_in = data.get("edge_type") == "calls" and tgt.startswith("pkg/")
        if calls_in and src.startswith("app/use.py::"):
            out.setdefault(src.split("::")[-1], set()).add((tgt, data["resolution_origin"]))
    return out


_HEAD = "from pkg import catalog\nimport pkg.catalog as aliased\nfrom pkg import other\n\n\n"


@pytest.mark.parametrize(
    ("returner", "origin"),
    [
        ("def load():\n    return catalog\n", "from-imported module"),
        ("def load():\n    return aliased  # the alias\n", "import ... as"),
        ("def load():\n    from pkg import catalog as c\n    return c\n", "import in the body"),
        ("def load():\n    import pkg.catalog\n    return pkg.catalog\n", "dotted import"),
        (
            "def load(fresh=False):\n    if fresh:\n        import pkg.catalog as m\n        return m\n"
            "    return catalog\n",
            "every return the same module",
        ),
        (
            "def load():\n    with open(__file__): return catalog\n",
            "the only return follows a with header on its line",
        ),
        (
            "def load(\n  fresh=False,\n):\n    return catalog\n",
            "arguments indented less than the body",
        ),
    ],
)
def test_call_on_a_returned_module_reaches_its_function(tmp_path, returner, origin):
    use = (
        _HEAD
        + returner
        + "\n\ndef chained():\n    return load().backend_of(1)\n"
        + "\n\ndef held():\n    m = load()\n    return m.backend_of(2)\n"
    )
    callees = _callees(tmp_path, use)

    assert callees["chained"] == {(_CATALOG, "return_type_import")}, origin
    assert callees["held"] == {(_CATALOG, "receiver_typed_import")}, origin


@pytest.mark.parametrize(
    "returner",
    [
        # One return hands on a value, not the module.
        "def load(flag):\n    if flag:\n        return catalog\n    return None\n",
        # The body can fall through to None.
        "def load(flag):\n    if flag:\n        return catalog\n",
        "def load(flag):\n    if flag: return catalog\n",
        "def load(flag):\n    if flag: x = 1\n    else: return catalog\n",
        "def load(flag):\n    for _ in flag:\n        return catalog\n    print(flag)\n",
        # A return on its if header's line hands on another value.
        "def load(flag):\n    if flag: return object()\n    return catalog\n",
        "def load(flag):\n    if flag:\n        return catalog\n    else: return other\n",
        # The returns name two different modules.
        "def load(flag):\n    if flag:\n        return catalog\n    return other\n",
        # The name is a parameter, not the import.
        "def load(catalog):\n    return catalog\n",
        # The name is rebound in the body.
        "def load():\n    catalog = object()\n    return catalog\n",
        # A generator yields, it does not return the module.
        "def load():\n    yield catalog\n    return catalog\n",
        # A nested def's return is its own.
        "def load():\n    def inner():\n        return catalog\n    return inner\n",
        # A def or a class of the module's name shadows the import.
        "def catalog():\n    pass\n\n\ndef load(v):\n    return catalog\n",
        "class catalog:\n    pass\n\n\ndef load(v):\n    return catalog\n",
    ],
)
def test_a_function_that_may_not_return_the_module_types_nothing(tmp_path, returner):
    use = (
        _HEAD
        + returner
        + "\n\ndef chained(v):\n    return load(v).backend_of(1)\n"
        + "\n\ndef held(v):\n    m = load(v)\n    return m.backend_of(2)\n"
    )

    callees = _callees(tmp_path, use)

    for caller in ("chained", "held"):
        assert (_CATALOG, "return_type_import") not in callees.get(caller, set())
        assert (_CATALOG, "receiver_typed_import") not in callees.get(caller, set())


def test_a_symbol_named_like_its_module_is_a_value_not_the_module(tmp_path):
    """``from pkg.catalog import catalog`` binds the module's own ``catalog`` object."""
    library = {
        **_LIBRARY,
        "pkg/catalog.py": "def backend_of(x):\n    return x\n\ncatalog = object()\n",
    }
    use = (
        "from pkg.catalog import catalog\n\n\ndef load():\n    return catalog\n"
        "\n\ndef held():\n    m = load()\n    return m.backend_of(2)\n"
    )

    callees = _callees(tmp_path, use, library)

    assert (_CATALOG, "receiver_typed_import") not in callees.get("held", set())


def test_the_returned_module_wins_over_a_same_named_function_elsewhere(tmp_path):
    use = _HEAD + "def load():\n    return other\n\n\ndef held():\n    m = load()\n"
    use += "    return m.backend_of(2)\n"

    assert _callees(tmp_path, use)["held"] == {(_OTHER, "receiver_typed_import")}


def test_an_awaited_coroutine_or_a_method_returning_the_module_types_its_call(tmp_path):
    use = _HEAD + (
        "async def load():\n    return catalog\n\n\n"
        "async def held():\n    m = await load()\n    return m.backend_of(2)\n\n\n"
        "class Box:\n    def mod(self):\n        return catalog\n\n"
        "    def chained(self):\n        return self.mod().backend_of(3)\n"
    )

    callees = _callees(tmp_path, use)

    assert callees["held"] == {(_CATALOG, "receiver_typed_import")}
    assert (_CATALOG, "return_type_import") in callees["chained"]


def test_a_return_after_an_async_header_on_its_line_is_read(tmp_path):
    use = _HEAD + (
        "async def load(lock):\n    async with lock: return other\n    return catalog\n\n\n"
        "async def held(lock):\n    m = await load(lock)\n    return m.backend_of(2)\n"
    )

    callees = _callees(tmp_path, use)

    assert (_CATALOG, "receiver_typed_import") not in callees.get("held", set())
