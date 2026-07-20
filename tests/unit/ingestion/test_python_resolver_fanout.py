"""Submodule fan-out for ``from pkg import submodule`` imports (#666).

The single-target resolver maps a package import to ``pkg/__init__.py`` only,
so submodules pulled in through their package never gain inbound edges and the
dead-code analyzer flags them unreachable. ``resolve_python_import_all`` probes
every imported name as a submodule file and fans the edge out.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.models import Import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.python import resolve_python_import_all


def _ctx(path_set: set[str]) -> ResolverContext:
    return ResolverContext(path_set=path_set, stem_map={}, graph=nx.DiGraph())


def _imp(module_path: str, names: list[str], *, relative: bool = False) -> Import:
    return Import(
        raw_statement="",
        module_path=module_path,
        imported_names=names,
        is_relative=relative,
        resolved_file=None,
    )


PATHS = {
    "app.py",
    "routers/__init__.py",
    "routers/workspace.py",
    "routers/git/__init__.py",
}


def test_package_import_fans_out_to_submodules() -> None:
    imp = _imp("routers", ["workspace", "git", "overview"])
    targets = resolve_python_import_all(imp, "app.py", _ctx(PATHS))
    assert targets == (
        "routers/__init__.py",
        "routers/workspace.py",
        "routers/git/__init__.py",
    )


def test_non_submodule_names_resolve_to_package_only() -> None:
    imp = _imp("routers", ["some_function"])
    targets = resolve_python_import_all(imp, "app.py", _ctx(PATHS))
    assert targets == ("routers/__init__.py",)


def test_star_import_does_not_probe() -> None:
    imp = _imp("routers", ["*"])
    targets = resolve_python_import_all(imp, "app.py", _ctx(PATHS))
    assert targets == ("routers/__init__.py",)


def test_direct_module_import_unchanged() -> None:
    # ``import routers.workspace`` resolves straight to the submodule; no
    # package fan-out applies because the base is not an ``__init__.py``.
    imp = _imp("routers.workspace", ["workspace"])
    targets = resolve_python_import_all(imp, "app.py", _ctx(PATHS))
    assert targets == ("routers/workspace.py",)


def test_relative_package_import_fans_out() -> None:
    paths = {
        "pkg/__init__.py",
        "pkg/api.py",
        "pkg/sub/__init__.py",
        "pkg/sub/handlers.py",
    }
    # ``from .sub import handlers`` inside pkg/api.py
    imp = _imp(".sub", ["handlers"], relative=True)
    targets = resolve_python_import_all(imp, "pkg/api.py", _ctx(paths))
    assert targets == ("pkg/sub/__init__.py", "pkg/sub/handlers.py")


def test_unresolvable_import_returns_empty() -> None:
    imp = _imp("nonexistent", ["thing"])
    assert resolve_python_import_all(imp, "app.py", _ctx(PATHS)) == ()


def test_source_root_nested_package_fans_out() -> None:
    # src-layout: the module index maps the dotted path across the source
    # root, and fan-out must follow it for absolute imports.
    paths = {
        "src/myapp/__init__.py",
        "src/myapp/server.py",
        "src/myapp/routers/__init__.py",
        "src/myapp/routers/users.py",
    }
    imp = _imp("myapp.routers", ["users"])
    targets = resolve_python_import_all(imp, "src/myapp/server.py", _ctx(paths))
    assert targets == ("src/myapp/routers/__init__.py", "src/myapp/routers/users.py")
