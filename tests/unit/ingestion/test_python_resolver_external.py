"""Unresolvable absolute Python imports become ``external:`` nodes.

Pins the tail of ``resolve_python_import``: an absolute import no repo file
defines registers an external node, a relative miss stays None, and a
resolvable import is untouched. The layout fixtures pin which absolute imports
a repo file may claim: the full dotted path under an import root, a script's
own directory, and nothing else (no stem guesses, no self-imports).
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.ingestion.graph import build_stem_map
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


def test_stdlib_import_never_stem_matches_a_nested_file() -> None:
    path = "pkg/specs/json.py"
    ctx = ResolverContext(path_set={*PATHS, path}, stem_map={"json": [path]}, graph=nx.DiGraph())
    assert resolve_python_import("json", "app.py", ctx) == "external:json"


def test_removed_stdlib_names_are_still_stdlib() -> None:
    """``distutils`` left the stdlib in 3.12 but old setup.py files import it."""
    path = "pkg/tools/distutils.py"
    ctx = ResolverContext(
        path_set={*PATHS, path}, stem_map={"distutils": [path]}, graph=nx.DiGraph()
    )
    assert resolve_python_import("distutils", "app.py", ctx) == "external:distutils"


def test_a_script_sibling_shadows_the_stdlib_module() -> None:
    """A script's own directory comes first on sys.path, so its sibling wins."""
    paths = {"tools/deploy.py", "tools/secrets.py"}
    ctx = ResolverContext(
        path_set=paths, stem_map={"secrets": ["tools/secrets.py"]}, graph=nx.DiGraph()
    )
    assert resolve_python_import("secrets", "tools/deploy.py", ctx) == "tools/secrets.py"


def test_a_bare_name_never_stem_matches_a_nested_file() -> None:
    """``import shared`` from the repo root is not ``pkg/helpers/shared.py``."""
    path = "pkg/helpers/shared.py"
    ctx = ResolverContext(path_set={*PATHS, path}, stem_map={"shared": [path]}, graph=nx.DiGraph())
    assert resolve_python_import("shared", "app.py", ctx) == "external:shared"


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


def test_a_non_python_file_is_never_a_target() -> None:
    ctx = ResolverContext(
        path_set={"app.py", "baselines/httpx.json", "vendor/httpx.py"},
        stem_map={"httpx": ["baselines/httpx.json", "vendor/httpx.py"]},
        graph=nx.DiGraph(),
    )
    assert resolve_python_import("httpx", "app.py", ctx) == "external:httpx"
    # From its own directory the sibling is what a script run there loads.
    assert resolve_python_import("httpx", "vendor/run.py", ctx) == "vendor/httpx.py"


# --- Layout fixtures: each is a repo's Python file set. -----------------------


def _layout_ctx(paths: set[str]) -> ResolverContext:
    return ResolverContext(path_set=paths, stem_map=build_stem_map(paths), graph=nx.DiGraph())


@pytest.fixture
def py_shadow_thirdparty() -> ResolverContext:
    """A package whose helper modules share third-party and stdlib names."""
    return _layout_ctx(
        {
            "pkg/__init__.py",
            "pkg/client.py",
            "pkg/types.py",
            "pkg/utils/__init__.py",
            "pkg/utils/pydantic.py",
            "pkg/utils/json.py",
            "tests/apps/wsgi.py",
        }
    )


@pytest.fixture
def py_self_stdlib() -> ResolverContext:
    """Package modules named after the stdlib module they wrap."""
    return _layout_ctx({"pkg/__init__.py", "pkg/logging.py", "pkg/uuid.py"})


@pytest.fixture
def py_script_sibling() -> ResolverContext:
    """A scripts directory that is not a package, next to a real package."""
    return _layout_ctx(
        {
            "scripts/release.py",
            "scripts/helpers.py",
            "scripts/secrets.py",
            "scripts/tools/__init__.py",
            "scripts/tools/git.py",
            "pkg/__init__.py",
            "pkg/helpers.py",
            "pkg/core.py",
        }
    )


@pytest.fixture
def py_namespace_multi_root() -> ResolverContext:
    """One PEP 420 namespace split across two distribution roots."""
    return _layout_ctx(
        {
            "libs/alpha/acme/alpha/__init__.py",
            "libs/alpha/acme/alpha/core.py",
            "libs/beta/acme/beta/__init__.py",
            "libs/beta/acme/beta/client.py",
            "libs/beta/tests/acme/beta/client.py",
        }
    )


@pytest.fixture
def py_src_monorepo() -> ResolverContext:
    """Several ``packages/*/src`` distributions importing each other."""
    return _layout_ctx(
        {
            "packages/core/src/corelib/__init__.py",
            "packages/core/src/corelib/models.py",
            "packages/core/src/corelib/io.py",
            "packages/cli/src/clitool/__init__.py",
            "packages/cli/src/clitool/main.py",
            "packages/cli/tests/test_main.py",
        }
    )


def test_third_party_names_do_not_bind_to_local_modules(py_shadow_thirdparty) -> None:
    ctx = py_shadow_thirdparty
    assert resolve_python_import("pydantic", "pkg/client.py", ctx) == "external:pydantic"
    # A dotted third-party path needs its whole suffix in the repo, not its tail.
    assert resolve_python_import("werkzeug.wsgi", "pkg/client.py", ctx) == "external:werkzeug.wsgi"
    assert (
        resolve_python_import("thirdparty.types", "pkg/client.py", ctx)
        == "external:thirdparty.types"
    )
    # The package's own modules still resolve by their full dotted name.
    assert resolve_python_import("pkg.utils.pydantic", "pkg/client.py", ctx) == (
        "pkg/utils/pydantic.py"
    )


def test_stdlib_names_inside_a_package_stay_stdlib(py_shadow_thirdparty) -> None:
    """Absolute imports: ``import types`` in ``pkg/`` never means ``pkg/types.py``."""
    ctx = py_shadow_thirdparty
    assert resolve_python_import("types", "pkg/client.py", ctx) == "external:types"
    assert resolve_python_import("json", "pkg/utils/pydantic.py", ctx) == "external:json"


def test_a_module_never_imports_itself(py_self_stdlib) -> None:
    ctx = py_self_stdlib
    assert resolve_python_import("logging", "pkg/logging.py", ctx) == "external:logging"
    assert resolve_python_import("uuid", "pkg/uuid.py", ctx) == "external:uuid"
    # Naming itself is no edge, and no external package either.
    assert resolve_python_import("pkg.logging", "pkg/logging.py", ctx) is None
    assert "external:pkg.logging" not in ctx.graph.nodes


def test_script_directory_comes_first_on_sys_path(py_script_sibling) -> None:
    ctx = py_script_sibling
    assert resolve_python_import("helpers", "scripts/release.py", ctx) == "scripts/helpers.py"
    assert resolve_python_import("secrets", "scripts/release.py", ctx) == "scripts/secrets.py"
    assert resolve_python_import("tools.git", "scripts/release.py", ctx) == "scripts/tools/git.py"
    # The script itself is never its own import.
    assert resolve_python_import("release", "scripts/release.py", ctx) is None


def test_sibling_rule_skips_package_members(py_script_sibling) -> None:
    """A package member's directory is not on sys.path, so no sibling lookup."""
    ctx = py_script_sibling
    assert resolve_python_import("helpers", "pkg/core.py", ctx) == "external:helpers"
    assert resolve_python_import("pkg.helpers", "pkg/core.py", ctx) == "pkg/helpers.py"


def test_namespace_package_resolves_across_roots(py_namespace_multi_root) -> None:
    ctx = py_namespace_multi_root
    importer = "libs/alpha/acme/alpha/core.py"
    assert resolve_python_import("acme.beta.client", importer, ctx) == (
        "libs/beta/acme/beta/client.py"
    )
    assert resolve_python_import("acme.beta", importer, ctx) == "libs/beta/acme/beta/__init__.py"
    # A tail that is not the whole dotted path is not a match.
    assert resolve_python_import("other.beta.client", importer, ctx) == (
        "external:other.beta.client"
    )


def test_src_monorepo_resolves_by_dotted_name(py_src_monorepo) -> None:
    ctx = py_src_monorepo
    main = "packages/cli/src/clitool/main.py"
    assert resolve_python_import("corelib.models", main, ctx) == (
        "packages/core/src/corelib/models.py"
    )
    assert resolve_python_import("corelib", main, ctx) == "packages/core/src/corelib/__init__.py"
    # ``io`` is the stdlib even though ``corelib/io.py`` exists.
    assert resolve_python_import("io", main, ctx) == "external:io"
    assert resolve_python_import("clitool.main", "packages/cli/tests/test_main.py", ctx) == main
