"""A function a registering decorator stores is reached through the registry.

``@rule`` where ``rule`` appends its argument to a module list and returns it:
the decorated function is called by whoever walks the list, never by name.
A decorator that only wraps, or stores something other than the function, is
not a registration and keeps its targets flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.models import REGISTRATION_HINT
from repowise.core.ingestion.python_registrars import registering_functions

_REGISTRY = """\
import functools

_RULES = []
_BY_NAME = {}
_SEEN = set()


def rule(fn):
    _RULES.append(fn)
    return fn


def named(key):
    def deco(fn):
        _BY_NAME[key] = fn
        return fn
    return deco


def timed(fn):
    @functools.wraps(fn)
    def wrapper(*args):
        return fn(*args)
    return wrapper


def remember(fn):
    _SEEN.add(fn.__name__)
    return fn


def run(value):
    for check in _RULES:
        check(value)
    return [f(value) for f in _BY_NAME.values()]
"""

_SAME_FILE = """\
_HOOKS = []


def hook(fn):
    _HOOKS.append(fn)
    return fn


@hook
def _local_hook():
    return 1


def fire():
    return [h() for h in _HOOKS]
"""

_RULES = """\
from pkg.registry import named, remember, rule, timed


@rule
def _imported_rule(value):
    return value


@named("x")
def _factory_rule(value):
    return value


@timed
def _wrapped_only(value):
    return value


@remember
def _name_only(value):
    return value


def _undecorated(value):
    return value
"""

_ALIASED = """\
from pkg import registry
from pkg.registry import rule as r


@r
def _aliased_rule(value):
    return value


@registry.rule
def _dotted_rule(value):
    return value
"""

_SQL = """\
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


class json_text(FunctionElement):
    pass


@compiles(json_text)
def _json_text_sqlite(element, compiler, **kw):
    return "x"
"""


def _build(root: Path) -> tuple[nx.DiGraph, dict[str, bytes]]:
    traverser, parser, builder = FileTraverser(root), ASTParser(), GraphBuilder(repo_path=root)
    source_map = {}
    for fi in traverser.traverse():
        source_map[fi.path] = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source_map[fi.path]))
    return builder.build(), source_map


def _unused_internals(root: Path) -> set[str]:
    graph, source_map = _build(root)
    report = DeadCodeAnalyzer(graph, source_map=source_map).analyze({"min_confidence": 0.0})
    return {f.symbol_name for f in report.findings if f.kind is DeadCodeKind.UNUSED_INTERNAL}


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_registered_functions_are_used_and_wrapped_ones_are_not(tmp_path):
    root = _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/registry.py": _REGISTRY,
            "pkg/hooks.py": _SAME_FILE,
            "pkg/rules.py": _RULES,
            "pkg/aliased.py": _ALIASED,
        },
    )
    flagged = _unused_internals(root)
    used = {"_local_hook", "_imported_rule", "_factory_rule", "_aliased_rule", "_dotted_rule"}
    assert not flagged & used
    assert {"_wrapped_only", "_name_only", "_undecorated"} <= flagged


def test_sqlalchemy_compiles_registers_the_function(tmp_path):
    root = _write(tmp_path, {"pkg/__init__.py": "", "pkg/sql.py": _SQL})
    assert "_json_text_sqlite" not in _unused_internals(root)


def test_registrar_shapes():
    found = registering_functions(ast.parse(_REGISTRY))
    assert found == {"rule", "named"}
    # A stored argument that is not returned, or returned but never stored.
    assert not registering_functions(
        ast.parse("def a(fn):\n    L.append(fn)\n\ndef b(fn):\n    return fn\n")
    )


def test_a_store_into_local_or_conditional_state_is_not_a_registration():
    source = """\
_RULES = []


def local_only(fn):
    seen = []
    seen.append(fn)
    return fn


def shadowed(fn):
    _RULES = []
    _RULES.append(fn)
    return fn


def conditional(fn):
    if fn.__doc__:
        _RULES.append(fn)
    return fn
"""
    assert not registering_functions(ast.parse(source))


def test_the_edge_is_a_registration_reference_and_reaches_a_class(tmp_path):
    root = _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/hooks.py": _SAME_FILE + "\n\n@hook\nclass _Plugin:\n    pass\n",
            # ``ast`` rejects this file; its decorator must not break the pass.
            "pkg/broken.py": "from pkg.hooks import hook\n\n\n@hook\ndef _f(:\n",
        },
    )
    graph, _ = _build(root)
    for target in ("pkg/hooks.py::_local_hook", "pkg/hooks.py::_Plugin"):
        edge = graph.get_edge_data("pkg/hooks.py::hook", target)
        assert edge is not None, target
        assert edge["edge_type"] == "references"
        assert edge["hint_source"] == REGISTRATION_HINT
