"""Submodule fan-out for ``from pkg import submodule`` imports (#666).

The single-target resolver maps a package import to ``pkg/__init__.py`` only,
so submodules pulled in through their package never gain inbound edges and the
dead-code analyzer flags them unreachable. ``resolve_python_import_all`` probes
every imported name as a submodule file and fans the edge out.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.models import Import, NamedBinding
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.python import resolve_python_import_all


def _ctx(path_set: set[str]) -> ResolverContext:
    return ResolverContext(path_set=path_set, stem_map={}, graph=nx.DiGraph())


def _imp(
    module_path: str,
    names: list[str],
    *,
    relative: bool = False,
    bindings: list | None = None,
) -> Import:
    return Import(
        raw_statement="",
        module_path=module_path,
        imported_names=names,
        is_relative=relative,
        resolved_file=None,
        bindings=bindings or [],
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


def test_submodule_binding_points_at_submodule_file() -> None:
    """``from pkg import submodule`` binds ``submodule`` to the submodule file.

    The binding's source_file must be ``pkg/submodule.py``, not the package
    ``__init__.py``, so a later ``submodule.symbol()`` call resolves against
    the file that actually declares the symbol (#1193).
    """
    paths = {
        "sensors/__init__.py",
        "sensors/foo.py",
        "caller.py",
    }
    imp = _imp(
        "sensors",
        ["foo"],
        bindings=[NamedBinding(local_name="foo", exported_name=None, source_file=None)],
    )
    targets = resolve_python_import_all(imp, "caller.py", _ctx(paths))
    assert targets == ("sensors/__init__.py", "sensors/foo.py")
    # The binding for ``foo`` must point at the submodule file.
    assert len(imp.bindings) == 1
    assert imp.bindings[0].local_name == "foo"
    assert imp.bindings[0].source_file == "sensors/foo.py"


def test_non_submodule_binding_keeps_package_init() -> None:
    """A name that is not a submodule file keeps the package init as its source.

    ``from pkg import some_function`` (a symbol re-exported by the package)
    must not be repointed to a submodule file that does not exist.
    """
    paths = {
        "sensors/__init__.py",
        "sensors/foo.py",
        "caller.py",
    }
    imp = _imp(
        "sensors",
        ["some_function"],
        bindings=[
            NamedBinding(
                local_name="some_function", exported_name=None, source_file=None
            )
        ],
    )
    targets = resolve_python_import_all(imp, "caller.py", _ctx(paths))
    assert targets == ("sensors/__init__.py",)
    assert len(imp.bindings) == 1
    assert imp.bindings[0].local_name == "some_function"
    assert imp.bindings[0].source_file is None


def test_submodule_aliased_binding_points_at_submodule_file() -> None:
    """``from pkg import submodule as alias`` binds ``alias`` to the submodule file."""
    paths = {
        "sensors/__init__.py",
        "sensors/foo.py",
        "caller.py",
    }
    imp = _imp(
        "sensors",
        ["foo"],
        bindings=[NamedBinding(local_name="f", exported_name="foo", source_file=None)],
    )
    targets = resolve_python_import_all(imp, "caller.py", _ctx(paths))
    assert targets == ("sensors/__init__.py", "sensors/foo.py")
    assert len(imp.bindings) == 1
    assert imp.bindings[0].local_name == "f"
    assert imp.bindings[0].source_file == "sensors/foo.py"


def test_non_submodule_aliased_binding_keeps_package_init() -> None:
    """An aliased name that is not a submodule file keeps the package init as its source."""
    paths = {
        "sensors/__init__.py",
        "sensors/foo.py",
        "caller.py",
    }
    imp = _imp(
        "sensors",
        ["some_function"],
        bindings=[
            NamedBinding(
                local_name="sf", exported_name="some_function", source_file=None
            )
        ],
    )
    targets = resolve_python_import_all(imp, "caller.py", _ctx(paths))
    assert targets == ("sensors/__init__.py",)
    assert len(imp.bindings) == 1
    assert imp.bindings[0].local_name == "sf"
    assert imp.bindings[0].source_file is None
