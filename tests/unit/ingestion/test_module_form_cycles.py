"""Python module-form imports and the static-only cycle tier.

``import b`` binds a half-built ``b`` without failing inside a cycle; only
reading ``b.X`` while the module loads can. ``from b import B`` fails whenever
the cycle is entered from the wrong side. A cycle made only of the first kind
stays a cycle, labelled static only, unlikely to fail at import.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.cohesion import is_static_only_cycle
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.test_import_load_kind import _builder, _info


def _module_form(source: str) -> dict[str, bool]:
    parsed = ASTParser().parse_file(_info("pkg/a.py"), source.encode("utf-8"))
    return {imp.module_path: imp.module_form for imp in parsed.imports}


def _static_only(builder) -> list[bool]:
    sub = builder.cycle_subgraph()
    return [
        is_static_only_cycle(d for _, _, d in sub.subgraph(scc).edges(data=True))
        for scc in builder.strongly_connected_components()
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import b\n", True),
        ("import b\ndef f():\n    return b.X\n", True),
        ("import b\nlam = lambda: b.X\n", True),
        ("import a.b as c\ndef f():\n    return c.X\n", True),
        ("import b\nclass A:\n    def m(self):\n        return b.X\n", True),
        # A keyword argument's name or another object's attribute is not ``b``.
        ("import b\nx = dict(b=1)\ny = x.b\n", True),
        ("import b\nX = b.X\n", False),
        ("import a.b\nY = a.b.c\n", False),
        ("import a.b as c\nY = c.X\n", False),
        ("import b\n@b.deco\ndef f():\n    pass\n", False),
        ("import b\ndef f(x=b.DEFAULT):\n    pass\n", False),
        ("import b\nclass A(b.Base):\n    pass\n", False),
        ("import b\nclass A:\n    X = b.X\n", False),
        ("import b\nY = f'{b.X}'\n", False),
        ("from b import B\n", False),
        ("from . import b\n", False),
    ],
)
def test_python_module_form_import(source: str, expected: bool) -> None:
    assert list(_module_form(source).values()) == [expected]


_A = "import pkg.b\ndef f():\n    return pkg.b.B\n"
_B = "import pkg.a\ndef g():\n    return pkg.a.A\n"


def test_a_cycle_of_module_form_imports_stays_a_cycle_labelled_static_only() -> None:
    builder = _builder({"pkg/a.py": _A, "pkg/b.py": _B})
    graph = builder.graph()
    assert graph["pkg/a.py"]["pkg/b.py"]["module_form"] is True
    assert graph["pkg/b.py"]["pkg/a.py"]["module_form"] is True
    assert builder.strongly_connected_components() == [frozenset({"pkg/a.py", "pkg/b.py"})]
    assert _static_only(builder) == [True]


@pytest.mark.parametrize(
    "b_source",
    [
        "from pkg.a import A\n",
        # ``import pkg.a`` and then ``pkg.a.A`` read while ``b`` loads.
        "import pkg.a\nX = pkg.a.A\n",
        # A load-time ``from`` import beside the module-form one.
        "import pkg.a\nfrom pkg.a import A\n",
    ],
)
def test_a_cycle_with_an_import_time_read_is_not_static_only(b_source: str) -> None:
    builder = _builder({"pkg/a.py": _A, "pkg/b.py": b_source})
    assert not builder.graph()["pkg/b.py"]["pkg/a.py"].get("module_form")
    assert builder.strongly_connected_components() == [frozenset({"pkg/a.py", "pkg/b.py"})]
    assert _static_only(builder) == [False]


def test_a_lazy_from_import_beside_a_module_form_one_keeps_the_edge_module_form() -> None:
    b_source = "import pkg.a\ndef g():\n    from pkg.a import A\n    return A\n"
    builder = _builder({"pkg/a.py": _A, "pkg/b.py": b_source})
    assert builder.graph()["pkg/b.py"]["pkg/a.py"]["module_form"] is True
    assert _static_only(builder) == [True]


@pytest.mark.parametrize(
    ("edges", "expected"),
    [
        ([{"module_form": True}, {"module_form": True}], True),
        ([{"module_form": True}, {}], False),
        # An edge no cycle runs through says nothing about the tier.
        ([{"module_form": True}, {"module_form": True}, {"type_only": True}], True),
        ([{"deferred": True}], False),
        ([], False),
    ],
)
def test_is_static_only_cycle(edges: list[dict], expected: bool) -> None:
    assert is_static_only_cycle(edges) is expected
