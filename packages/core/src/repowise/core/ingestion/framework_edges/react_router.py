"""React Router v7 framework-mode convention edges.

In framework mode the React Router build loads ``root`` and ``routes`` from
the app directory (``app/`` unless ``react-router.config.*`` sets
``appDirectory``), and ``routes`` names each route module by a path relative
to that directory through the ``@react-router/dev/routes`` helpers
(``index("routes/home.tsx")``, ``route("about", "routes/about.tsx")``,
``layout("routes/shell.tsx", [...])``). Nothing imports any of them, so
without these edges all three read as unreachable and their exports unused.

The framework is recognised per app: a directory holding a
``react-router.config.*`` file, or a ``package.json`` depending on
``@react-router/dev``. Its ``root`` and ``routes`` hang off the
``framework:react-router`` anchor, and ``routes`` gets an edge to every module
its helpers name. The edges carry no names, so every export of those files
(``default``, ``loader``, ``meta``, ``Layout``, ``ErrorBoundary`` ...) counts
as reached by the runtime.

Ceilings: route modules found by ``flatRoutes()`` (``@react-router/fs-routes``)
and a route file named through a variable or a renamed helper import get no
edge.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from ..framework_facts import REACT_ROUTER
from ..languages.js_config_values import strip_comments
from ..resolvers import ResolverContext
from ..resolvers.ts_workspace import _parsed_package_jsons
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    anchor_edge,
    read_text_from,
)

if TYPE_CHECKING:
    import networkx as nx


_DEV_PACKAGE = "@react-router/dev"
_CONFIG_RE = re.compile(r"(?:^|/)react-router\.config\.(?:ts|mts|cts|js|mjs|cjs)$")
_APP_DIRECTORY_RE = re.compile(r"""\bappDirectory\s*:\s*(["'`])([^"'`]+)\1""")

_ROOT_EXTS = (".tsx", ".ts", ".jsx", ".js")
_ROUTES_EXTS = (".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs")
_MODULE_EXTS = (".tsx", ".ts", ".jsx", ".js", ".mts", ".mjs")

# The file argument of a route helper: first for ``index`` and ``layout``,
# second for ``route`` (after the URL path). ``prefix`` names no file.
_ROUTE_FILE_RE = re.compile(
    r"""(?<![\w.$])(?:(?:index|layout)\s*\(\s*|route\s*\(\s*(["'`])[^"'`]*\1\s*,\s*)"""
    r"""(["'`])(?P<file>[^"'`]+)\2"""
)


def _depends_on_dev_package(data: dict) -> bool:
    for key in ("dependencies", "devDependencies"):
        deps = data.get(key)
        if isinstance(deps, dict) and _DEV_PACKAGE in deps:
            return True
    return False


def _app_roots(ctx: ResolverContext, path_set: set[str]) -> dict[str, str | None]:
    """``{app root: its react-router.config path, or None}`` for every framework-mode app."""
    roots: dict[str, str | None] = {}
    for path in sorted(path_set):
        if _CONFIG_RE.search(path):
            roots.setdefault(posixpath.dirname(path), path)
    for pkg_dir, data in _parsed_package_jsons(ctx):
        if _depends_on_dev_package(data):
            roots.setdefault("" if pkg_dir in ("", ".") else pkg_dir, None)
    return roots


def _app_directory(
    root: str, config: str | None, parsed_files: dict[str, Any], ctx: ResolverContext
) -> str:
    """The app directory, repo-relative (``""`` for the repo root): ``appDirectory``
    from the config, else ``app``."""
    app_dir = "app"
    if config is not None and config in parsed_files:
        text = read_text_from(config, parsed_files[config], ctx.source_map)
        if m := _APP_DIRECTORY_RE.search(_strip_comments(text)):
            app_dir = m.group(2)
    app_dir = posixpath.normpath(posixpath.join(root, app_dir))
    return "" if app_dir == "." else app_dir


def _strip_comments(text: str) -> str:
    return strip_comments(text.encode("utf-8")).decode("utf-8", errors="replace")


def _first_present(stem: str, exts: tuple[str, ...], path_set: set[str]) -> str | None:
    return next((stem + ext for ext in exts if stem + ext in path_set), None)


def route_module_files(text: str) -> list[str]:
    """The file arguments of the route helpers in a ``routes`` module, in order."""
    return [m.group("file") for m in _ROUTE_FILE_RE.finditer(_strip_comments(text))]


def _resolve_module(app_dir: str, name: str, path_set: set[str]) -> str | None:
    rel = posixpath.normpath(posixpath.join(app_dir, name))
    if rel in path_set:
        return rel
    return _first_present(rel, _MODULE_EXTS, path_set)


def _add_react_router_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    count = 0
    for root, config in sorted(_app_roots(ctx, path_set).items()):
        app_dir = _app_directory(root, config, parsed_files, ctx)
        root_module = _first_present(posixpath.join(app_dir, "root"), _ROOT_EXTS, path_set)
        if root_module is not None:
            count += anchor_edge(graph, REACT_ROUTER, root_module)
        routes = _first_present(posixpath.join(app_dir, "routes"), _ROUTES_EXTS, path_set)
        if routes is None or routes not in parsed_files:
            continue
        count += anchor_edge(graph, REACT_ROUTER, routes)
        text = read_text_from(routes, parsed_files[routes], ctx.source_map)
        for name in route_module_files(text):
            target = _resolve_module(app_dir, name, path_set)
            if target is not None and _add_edge_if_new(graph, routes, target):
                count += 1
    return count


class _ReactRouterHandler:
    """Framework-mode apps: anchor ``root``/``routes`` and link the modules ``routes`` names."""

    def detect(self, dctx: DetectionContext) -> bool:
        # The gate is per app (a config file or the dev package), inside add_edges.
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_react_router_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_ReactRouterHandler()]
