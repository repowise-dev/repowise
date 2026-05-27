"""Gin / Echo / Chi (Go) router convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    _build_function_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_GO_ROUTER_PKG_PATTERNS = (
    "github.com/gin-gonic/gin",
    "github.com/labstack/echo",
    "github.com/go-chi/chi",
)
_GO_ROUTE_CALL_RE = re.compile(
    r"\.\s*(?:GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|HandleFunc|Handle|Any)"
    r"\s*\(\s*[\"'][^\"']*[\"']\s*,\s*([\w.]+)"
)


def _has_go_router_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "go":
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if any(mp.startswith(pkg) for pkg in _GO_ROUTER_PKG_PATTERNS):
                return True
    return False


def _add_go_router_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0
    func_to_files = _build_function_to_file(parsed_files, ("go",))
    class_to_file = _build_class_to_file(parsed_files, ("go",))

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "go":
            continue
        text = read_text(parsed)
        if not text:
            continue
        if not any(
            imp.module_path.startswith(pkg)
            for pkg in _GO_ROUTER_PKG_PATTERNS
            for imp in parsed.imports
        ):
            # Allow if some other go file in the repo imports a router (router
            # setup may be split across files); be conservative and only emit
            # edges for files that themselves reference router calls.
            pass

        for m in _GO_ROUTE_CALL_RE.finditer(text):
            handler = m.group(1)
            targets = _resolve_go_handler(handler, parsed, func_to_files, class_to_file)
            for target in targets:
                if target != path and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

    return count


def _resolve_go_handler(
    handler: str,
    parsed: Any,
    func_to_files: dict[str, list[str]],
    class_to_file: dict[str, str],
) -> list[str]:
    """Resolve `pkg.Func` / `recv.Method` / `Func` to candidate file paths."""
    if "." in handler:
        prefix, name = handler.rsplit(".", 1)
        # First: was prefix imported as a package?
        for imp in parsed.imports:
            short = imp.module_path.rsplit("/", 1)[-1]
            if short == prefix and imp.resolved_file:
                # Find file declaring `name` whose path starts with the resolved package dir
                pkg_dir = "/".join(imp.resolved_file.split("/")[:-1])
                results = [
                    p
                    for p in func_to_files.get(name, [])
                    if p.startswith(pkg_dir + "/") or p == imp.resolved_file
                ]
                if results:
                    return results
        # Second: receiver-method — try the receiver's type file
        type_file = class_to_file.get(prefix.title())
        if type_file:
            return [type_file]
        # Third: fall back to any file declaring the bare name
        return list(func_to_files.get(name, []))
    return list(func_to_files.get(handler, []))


class _GoRouterHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        go_router_in_stack = any(token in dctx.stack_lower for token in ("gin", "echo", "chi"))
        return go_router_in_stack or _has_go_router_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_go_router_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_GoRouterHandler()]
