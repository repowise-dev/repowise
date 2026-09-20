"""Unresolvable absolute Python imports become ``external:`` nodes.

Pins the tail of ``resolve_python_import``: an absolute import no repo file
defines registers an external node, a relative miss stays None, and a
resolvable import is untouched.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.models import Import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.python import (
    resolve_python_import,
    resolve_python_import_all,
)

PATHS = {"app.py", "pkg/__init__.py", "pkg/mod.py"}


def _ctx() -> ResolverContext:
    return ResolverContext(path_set=set(PATHS), stem_map={}, graph=nx.DiGraph())


def _imp(module_path: str, names: list[str]) -> Import:
    return Import(
        raw_statement="",
        module_path=module_path,
        imported_names=names,
        is_relative=False,
        resolved_file=None,
        bindings=[],
    )


def test_absolute_miss_registers_external_node() -> None:
    ctx = _ctx()
    assert resolve_python_import("requests", "app.py", ctx) == "external:requests"
    assert ctx.graph.nodes["external:requests"]["language"] == "external"


def test_dotted_miss_keeps_the_full_module_path() -> None:
    ctx = _ctx()
    resolved = resolve_python_import("requests.adapters", "app.py", ctx)
    assert resolved == "external:requests.adapters"
    assert "external:requests.adapters" in ctx.graph.nodes


def test_stdlib_miss_is_external_too() -> None:
    ctx = _ctx()
    assert resolve_python_import("subprocess", "app.py", ctx) == "external:subprocess"
    assert ctx.graph.nodes["external:subprocess"]["language"] == "external"


def test_relative_miss_stays_none_and_adds_nothing() -> None:
    ctx = _ctx()
    assert resolve_python_import(".missing", "app.py", ctx) is None
    assert list(ctx.graph.nodes) == []


def test_resolvable_absolute_import_is_untouched() -> None:
    ctx = _ctx()
    assert resolve_python_import("pkg.mod", "app.py", ctx) == "pkg/mod.py"
    assert list(ctx.graph.nodes) == []


def test_resolve_all_returns_the_external_key_without_probing() -> None:
    ctx = _ctx()
    targets = resolve_python_import_all(_imp("requests", ["adapters", "Session"]), "app.py", ctx)
    assert targets == ("external:requests",)


def test_stem_fallback_never_lands_on_a_non_python_file() -> None:
    ctx = ResolverContext(
        path_set={"app.py", "baselines/httpx.json", "vendor/httpx.py"},
        stem_map={"httpx": ["baselines/httpx.json", "vendor/httpx.py"]},
        graph=nx.DiGraph(),
    )
    assert resolve_python_import("httpx", "app.py", ctx) == "vendor/httpx.py"

    only_fixture = ResolverContext(
        path_set={"app.py", "baselines/httpx.json"},
        stem_map={"httpx": ["baselines/httpx.json"]},
        graph=nx.DiGraph(),
    )
    assert resolve_python_import("httpx", "app.py", only_fixture) == "external:httpx"
