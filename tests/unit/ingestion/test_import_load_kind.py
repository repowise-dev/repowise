"""Imports that never run at module load do not close an import cycle.

Python ``if TYPE_CHECKING:`` imports and TS ``typeof import()`` are type-only;
an import inside a function body is deferred. Both stay in the graph for
reachability and leave cycle detection, so a deliberate lazy-import pair is
not a ``break_cycle`` plan while a real runtime cycle still is.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    build_file_scc_index,
    detect_refactorings,
)
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, combine_load_kinds
from repowise.core.ingestion.parser import ASTParser


def _info(path: str) -> FileInfo:
    language = "python" if path.endswith(".py") else "typescript"
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _kinds(path: str, source: str) -> dict[str, tuple[bool, bool]]:
    parsed = ASTParser().parse_file(_info(path), source.encode("utf-8"))
    return {imp.module_path: (imp.type_only, imp.deferred) for imp in parsed.imports}


def _builder(files: dict[str, str]) -> GraphBuilder:
    parser = ASTParser()
    builder = GraphBuilder()
    for path, source in files.items():
        builder.add_file(parser.parse_file(_info(path), source.encode("utf-8")))
    builder.build()
    return builder


def _cycles(builder: GraphBuilder) -> list:
    graph = builder.graph()
    out = []
    for path in sorted(n for n, d in graph.nodes(data=True) if d.get("node_type") == "file"):
        ctx = RefactoringContext(
            file_path=path,
            language="python",
            nloc=50,
            graph=graph,
            file_scc=build_file_scc_index(graph).get(path),
        )
        out += [s for s in detect_refactorings(ctx) if s.refactoring_type == "break_cycle"]
    return out


PY_SOURCE = """\
from typing import TYPE_CHECKING
import typing
if TYPE_CHECKING:
    from .context import Ctx
else:
    from .other import Other
if typing.TYPE_CHECKING:
    from .types import T
def handler():
    from .command import claim
    if TYPE_CHECKING:
        from .hinted import H
class Box:
    from .attrs import field
    def method(self):
        from .lazy import thing
if not TYPE_CHECKING:
    from .negated import N
if not typing.TYPE_CHECKING:
    from .negated_attr import NA
if sys.version_info > (3, 11):
    pass
elif TYPE_CHECKING:
    from .elif_branch import E
from typing import TYPE_CHECKING as TC
if TC:
    from .aliased import A
"""


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (".context", (True, False)),  # if TYPE_CHECKING:
        (".other", (False, False)),  # its else branch runs
        (".types", (True, False)),  # typing.TYPE_CHECKING
        (".command", (False, True)),  # function-local
        (".hinted", (True, False)),  # type-only wins inside a function
        (".attrs", (False, False)),  # a class body runs at load
        (".lazy", (False, True)),  # method body
        (".negated", (False, False)),  # `if not TYPE_CHECKING:` runs
        (".negated_attr", (False, False)),
        (".elif_branch", (True, False)),
        # Ceiling: an alias is not recognised, so the import stays runtime.
        (".aliased", (False, False)),
    ],
)
def test_python_import_load_kind(module: str, expected: tuple[bool, bool]) -> None:
    assert _kinds("pkg/a.py", PY_SOURCE)[module] == expected


TS_SOURCE = """\
export type Probe = typeof import("./probe.js").probe;
const load = makeLoader(() => import("./runtime.js"));
async function f() { const r = require("./req.js"); }
const top = await import("./top.js");
"""


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("./probe.js", (True, False)),  # typeof import(): a type position
        ("./runtime.js", (False, True)),  # lazy loader
        ("./req.js", (False, True)),  # require in a function
        ("./top.js", (False, False)),  # top-level await runs at load
    ],
)
def test_ts_import_load_kind(module: str, expected: tuple[bool, bool]) -> None:
    assert _kinds("src/a.ts", TS_SOURCE)[module] == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # A lazy copy first must not hide the load-time twin: runtime wins.
        ("def f():\n    from .b import x\n\nfrom .b import x\n", (False, False)),
        ("from .b import x\n\ndef f():\n    from .b import x\n", (False, False)),
        ("if TYPE_CHECKING:\n    from .b import x\ndef f():\n    from .b import x\n", (False, True)),
    ],
)
def test_a_repeated_statement_is_one_import_with_the_earliest_kind(
    source: str, expected: tuple[bool, bool]
) -> None:
    parsed = ASTParser().parse_file(_info("pkg/a.py"), source.encode("utf-8"))
    same = [imp for imp in parsed.imports if imp.module_path == ".b"]
    assert len(same) == 1  # non-graph consumers see no duplicate
    assert (same[0].type_only, same[0].deferred) == expected


R, T, D = (False, False), (True, False), (False, True)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [(T, T, T), (T, D, D), (D, T, D), (D, D, D), (D, R, R), (R, D, R), (T, R, R), (R, T, R)],
)
def test_combine_load_kinds(first, second, expected) -> None:
    assert combine_load_kinds(first, second) == expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [(T, T, T), (T, D, D), (D, T, D), (D, R, R), (R, D, R)],
)
def test_an_edge_folds_its_imports_load_kinds(first, second, expected) -> None:
    from repowise.core.ingestion.graph.builder import _merge_load_kind

    data: dict = {"type_only": first[0], **({"deferred": True} if first[1] else {})}
    imp = Import("x", "b", [], True, None, type_only=second[0], deferred=second[1])
    _merge_load_kind(data, imp)
    assert (data["type_only"], bool(data.get("deferred"))) == expected
    assert data.get("deferred", True) is True  # stored only when true


def test_type_checking_and_lazy_import_pair_is_no_cycle() -> None:
    """The resolvers/context.py <-> ruby_rails.py shape: one side imports under
    TYPE_CHECKING, the other inside a function."""
    builder = _builder(
        {
            "pkg/context.py": "def resolve():\n    from .rails import go\n    return go()\n",
            "pkg/rails.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n    from .context import Ctx\n"
                "def go():\n    return 1\n"
            ),
        }
    )
    graph = builder.graph()
    # The edges stay for reachability, marked.
    assert graph["pkg/context.py"]["pkg/rails.py"]["deferred"] is True
    assert graph["pkg/rails.py"]["pkg/context.py"]["type_only"] is True
    cycle_view = builder.cycle_subgraph()
    assert not cycle_view.has_edge("pkg/context.py", "pkg/rails.py")
    assert not cycle_view.has_edge("pkg/rails.py", "pkg/context.py")
    assert build_file_scc_index(graph) == {}
    assert _cycles(builder) == []


def test_a_runtime_cycle_is_still_a_plan() -> None:
    builder = _builder(
        {
            "pkg/a.py": "from .b import y\nx = 1\n",
            "pkg/b.py": "from .a import x\ny = 2\n",
        }
    )
    plans = _cycles(builder)
    assert len(plans) == 1
    assert set(plans[0].plan["cycle"]) == {"pkg/a.py", "pkg/b.py"}


def test_one_load_time_import_makes_the_edge_runtime() -> None:
    builder = _builder(
        {
            "pkg/a.py": "def f():\n    from .b import y\nfrom .b import z\n",
            "pkg/b.py": "def g():\n    from .a import f\ny = z = 1\n",
        }
    )
    graph = builder.graph()
    assert not graph["pkg/a.py"]["pkg/b.py"].get("deferred")
    assert graph["pkg/b.py"]["pkg/a.py"]["deferred"] is True
    assert build_file_scc_index(graph) == {}
