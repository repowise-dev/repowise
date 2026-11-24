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
    language = {"py": "python", "astro": "astro"}.get(path.rsplit(".", 1)[-1], "typescript")
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
from typing_extensions import TYPE_CHECKING as TE
if TE:
    from .aliased_ext import AE
if False:
    from .false_guarded import FG
else:
    from .false_else import FE
if 0:
    from .zero_guarded import ZG
if True:
    from .true_guarded import TG
if 1:
    from .one_guarded import OG
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
        (".aliased", (True, False)),
        (".aliased_ext", (True, False)),
        (".false_guarded", (True, False)),
        (".false_else", (False, False)),
        (".zero_guarded", (True, False)),
        (".true_guarded", (False, False)),
        (".one_guarded", (False, False)),
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


ASTRO_SOURCE = """\
---
import type { Props } from "./props";
const load = () => import("./lazy.js");
---
<p>hi</p>
"""


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("./props", (True, False)),  # import type, as in a .ts file
        ("./lazy.js", (False, True)),  # lazy loader in the frontmatter
    ],
)
def test_astro_frontmatter_import_load_kind(module: str, expected: tuple[bool, bool]) -> None:
    assert _kinds("src/pages/index.astro", ASTRO_SOURCE)[module] == expected


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


R, T, D, G = (False, False, False), (True, False, False), (False, True, False), (False, False, True)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (T, T, T), (T, D, D), (D, T, D), (D, D, D), (D, R, R), (R, D, R), (T, R, R), (R, T, R),
        (G, T, G), (D, G, G), (G, G, G), (G, R, R), (R, G, R),
    ],
)  # fmt: skip
def test_combine_load_kinds(first, second, expected) -> None:
    assert combine_load_kinds(first, second) == expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [(T, T, T), (T, D, D), (D, T, D), (D, R, R), (R, D, R), (G, D, G), (D, G, G), (G, R, R)],
)
def test_an_edge_folds_its_imports_load_kinds(first, second, expected) -> None:
    from repowise.core.ingestion.graph.builder import _merge_load_kind

    data: dict = {
        "type_only": first[0],
        **({"deferred": True} if first[1] else {}),
        **({"guarded": True} if first[2] else {}),
    }
    imp = Import(
        "x", "b", [], True, None, type_only=second[0], deferred=second[1], guarded=second[2]
    )
    _merge_load_kind(data, imp)
    assert (data["type_only"], bool(data.get("deferred")), bool(data.get("guarded"))) == expected
    assert data.get("deferred", True) is True  # stored only when true
    assert data.get("guarded", True) is True


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


GUARDED_SOURCE = """\
try:
    from .plain import P
except ImportError:
    P = None
try:
    from .tupled import T
except (ImportError, OSError) as exc:
    T = None
try:
    import pkg.missing
except builtins.ModuleNotFoundError:
    pass
try:
    from .broad import B
except Exception:
    B = None
try:
    from .bare import X
except:
    X = None
try:
    from .base import BE
except BaseException:
    BE = None
try:
    import json
except ImportError:
    from .handler import H
else:
    from .else_branch import E
finally:
    from .finally_branch import F
try:
    from .reraised import RR
except ImportError as exc:
    raise ImportError("install the extra") from exc
try:
    from .exited import EX
except ImportError:
    print("missing")
    sys.exit(1)
try:
    from .maybe_raised import MR
except ImportError:
    if STRICT:
        raise
try:
    from .split import SP
except ModuleNotFoundError:
    SP = None
except ImportError:
    raise
def f():
    try:
        from .lazy import L
    except ImportError:
        pass
"""


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (".plain", (False, False, True)),
        (".tupled", (False, False, True)),  # in a tuple, with `as`
        ("pkg.missing", (False, False, True)),  # builtins.ModuleNotFoundError
        (".broad", (False, False, False)),  # except Exception guards more than the import
        (".bare", (False, False, False)),
        (".base", (False, False, False)),
        (".handler", (False, False, False)),  # the fallback itself runs unguarded
        (".else_branch", (False, False, False)),
        (".finally_branch", (False, False, False)),
        (".reraised", (False, False, False)),  # the module does not survive
        (".exited", (False, False, False)),
        (".maybe_raised", (False, False, True)),  # survives unless STRICT
        # A cycle's `cannot import name` is an ImportError: the fatal handler runs.
        (".split", (False, False, False)),
        (".lazy", (False, True, False)),  # deferred wins
    ],
)
def test_python_import_guarded_by_except_import_error(
    module: str, expected: tuple[bool, bool, bool]
) -> None:
    parsed = ASTParser().parse_file(_info("pkg/a.py"), GUARDED_SOURCE.encode("utf-8"))
    kinds = {imp.module_path: (imp.type_only, imp.deferred, imp.guarded) for imp in parsed.imports}
    assert kinds[module] == expected


_B_IMPORTS_A = "from .a import A\nclass B: ...\n"


@pytest.mark.parametrize(
    "handler", ["ImportError", "ModuleNotFoundError", "(ImportError, OSError)", "ImportError as e"]
)
def test_an_import_guarded_by_except_import_error_closes_no_cycle(handler: str) -> None:
    builder = _builder(
        {
            "pkg/a.py": f"try:\n    from .b import B\nexcept {handler}:\n    B = None\nclass A: ...\n",
            "pkg/b.py": _B_IMPORTS_A,
        }
    )
    graph = builder.graph()
    assert graph["pkg/a.py"]["pkg/b.py"]["guarded"] is True  # kept for reachability
    assert not builder.cycle_subgraph().has_edge("pkg/a.py", "pkg/b.py")
    assert build_file_scc_index(graph) == {}
    assert _cycles(builder) == []


@pytest.mark.parametrize(
    "a_source",
    [
        "try:\n    from .b import B\nexcept Exception:\n    B = None\nclass A: ...\n",
        "try:\n    from .b import B\nexcept:\n    B = None\nclass A: ...\n",
        # One unguarded load-time import of the same target makes the edge runtime.
        "try:\n    from .b import B\nexcept ImportError:\n    B = None\n"
        "from .b import B2\nclass A: ...\n",
        "from .b import B\nclass A: ...\n",
    ],
)
def test_an_unguarded_import_still_closes_the_cycle(a_source: str) -> None:
    builder = _builder({"pkg/a.py": a_source, "pkg/b.py": _B_IMPORTS_A})
    assert not builder.graph()["pkg/a.py"]["pkg/b.py"].get("guarded")
    plans = _cycles(builder)
    assert len(plans) == 1
    assert set(plans[0].plan["cycle"]) == {"pkg/a.py", "pkg/b.py"}


def test_type_checking_guards_do_not_close_import_cycles() -> None:
    b_source = "from a import A\nclass B: ...\n"
    cases = {
        "if TYPE_CHECKING": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from b import B\nclass A: ...\n",
        "if False": "if False:\n    from b import B\nclass A: ...\n",
        "if 0": "if 0:\n    from b import B\nclass A: ...\n",
        "alias TC": "from typing import TYPE_CHECKING as TC\nif TC:\n    from b import B\nclass A: ...\n",
        "alias TE": "from typing_extensions import TYPE_CHECKING as TE\nif TE:\n    from b import B\nclass A: ...\n",
    }
    for name, a_source in cases.items():
        sccs = _builder({"a.py": a_source, "b.py": b_source}).strongly_connected_components()
        assert [sorted(s) for s in sccs if len(s) > 1] == [], f"cycle found for {name}"

