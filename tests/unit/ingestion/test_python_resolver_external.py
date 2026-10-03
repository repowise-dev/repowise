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


@pytest.fixture
def py_nested_import_root() -> ResolverContext:
    """microdot's layout: a vendored package beside a loose module it imports."""
    return _layout_ctx(
        {
            "libs/circuitpython/adafruit_ticks.py",
            "libs/circuitpython/asyncio/__init__.py",
            "libs/circuitpython/asyncio/core.py",
            "libs/circuitpython/asyncio/types.py",
            "libs/micropython/adafruit_ticks.py",
            "libs/circuitpython/tools/run.py",
            "libs/circuitpython/tools/helper.py",
            "src/microdot/__init__.py",
            "src/microdot/helpers.py",
            "vendor/helpers.py",
        }
    )


def test_package_member_resolves_a_bare_name_under_its_own_root(py_nested_import_root) -> None:
    """``asyncio/core.py`` is imported from ``libs/circuitpython``, so its siblings are too."""
    ctx = py_nested_import_root
    for importer in (
        "libs/circuitpython/asyncio/core.py",
        "libs/circuitpython/asyncio/__init__.py",
    ):
        assert resolve_python_import("adafruit_ticks", importer, ctx) == (
            "libs/circuitpython/adafruit_ticks.py"
        )
    # A stdlib name stays the stdlib, even beside ``asyncio/types.py``.
    assert resolve_python_import("types", "libs/circuitpython/asyncio/core.py", ctx) == (
        "external:types"
    )


def test_a_script_sibling_in_a_nested_non_package_dir_resolves(py_nested_import_root) -> None:
    ctx = py_nested_import_root
    assert resolve_python_import("helper", "libs/circuitpython/tools/run.py", ctx) == (
        "libs/circuitpython/tools/helper.py"
    )


def test_own_root_never_matches_a_same_stem_module_elsewhere(py_nested_import_root) -> None:
    ctx = py_nested_import_root
    # ``src/microdot`` is imported from ``src``, which has no ``adafruit_ticks.py``.
    assert resolve_python_import("adafruit_ticks", "src/microdot/helpers.py", ctx) == (
        "external:adafruit_ticks"
    )
    # ``src`` has no ``helpers.py``; ``vendor/helpers.py`` and the package's own are other roots.
    assert resolve_python_import("helpers", "src/microdot/__init__.py", ctx) == "external:helpers"
    assert resolve_python_import("helper", "libs/circuitpython/asyncio/core.py", ctx) == (
        "external:helper"
    )


def test_own_root_wins_over_a_same_dotted_name_in_another_root() -> None:
    """Two distributions each with a ``tests`` package: each imports its own."""
    ctx = _layout_ctx(
        {
            "libs/a/tests/__init__.py",
            "libs/a/tests/stubs.py",
            "libs/b/tests/__init__.py",
            "libs/b/tests/stubs.py",
            "libs/b/tests/test_x.py",
        }
    )
    assert resolve_python_import("tests.stubs", "libs/b/tests/test_x.py", ctx) == (
        "libs/b/tests/stubs.py"
    )


# A name imported from outside the repository is that module's, so a call to
# it must not fall through to a same-named repo symbol.


def _call_edges(tmp_path, sources: dict[str, str]) -> set[tuple[str, str]]:
    from datetime import datetime

    from repowise.core.ingestion.graph import GraphBuilder
    from repowise.core.ingestion.models import FileInfo
    from repowise.core.ingestion.parser import ASTParser

    parser = ASTParser()
    builder = GraphBuilder(tmp_path)
    for rel, text in sources.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(abs_path),
            language="python",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(parser.parse_file(info, text.encode("utf-8")))
    graph = builder.build()
    return {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"}


_SHADOWED = {
    "pkg/__init__.py": "",
    "pkg/utils.py": "def unquote(s):\n    return s\n\n\ndef other():\n    return 1\n",
    "pkg/types.py": "class Path:\n    pass\n",
}


def test_an_imported_stdlib_name_does_not_bind_a_same_named_repo_symbol(tmp_path) -> None:
    edges = _call_edges(
        tmp_path,
        {
            **_SHADOWED,
            "pkg/urls.py": (
                "from urllib.parse import unquote\nfrom pkg.utils import other\n\n\n"
                "def parse(s):\n    other()\n    return unquote(s)\n"
            ),
            "pkg/paths.py": "from pathlib import Path\n\n\ndef mk(s):\n    return Path(s)\n",
        },
    )
    assert ("pkg/urls.py::parse", "pkg/utils.py::other") in edges
    assert not {e for e in edges if e[1] in ("pkg/utils.py::unquote", "pkg/types.py::Path")}


def test_an_unimported_name_still_reaches_the_repo_symbol(tmp_path) -> None:
    edges = _call_edges(
        tmp_path,
        {**_SHADOWED, "pkg/paths.py": "def mk(s):\n    return Path(s)\n"},
    )
    assert ("pkg/paths.py::mk", "pkg/types.py::Path") in edges
