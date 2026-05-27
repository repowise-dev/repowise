"""Go web/RPC framework convention edges.

Covers the route/handler wiring that carries no import edge:

* **Router frameworks** — Gin / Echo / Chi ``r.GET("/path", handler)``.
* **stdlib ``net/http``** — ``http.HandleFunc("/path", handler)`` and
  ``mux.Handle("/path", handler)`` (same ``.HandleFunc`` / ``.Handle``
  selector shape as the router frameworks, so one regex serves both —
  the only difference is which import gates emission).
* **gRPC** — ``pb.RegisterXxxServer(s, &impl{})`` connects the registration
  site to the file defining the server implementation type.

Split out of ``framework_edges.py`` (PR 3.5); net/http + gRPC added in the
Go-parity work (Phase 4).
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
# stdlib net/http is gated separately: its handler-registration calls share
# the ``.HandleFunc`` / ``.Handle`` selector shape with the router frameworks.
_GO_HTTP_PKG = "net/http"

_GO_ROUTE_CALL_RE = re.compile(
    r"\.\s*(?:GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|HandleFunc|Handle|Any)"
    r"\s*\(\s*[\"'][^\"']*[\"']\s*,\s*([\w.]+)"
)

# gRPC generated registration: ``pb.RegisterGreeterServer(s, &greeter{})`` or
# ``RegisterGreeterServer(srv, impl)``. Capture the implementation argument
# (the second positional arg); we resolve its leading type identifier.
_GO_GRPC_REGISTER_RE = re.compile(
    r"\bRegister\w+Server\s*\(\s*[\w.]+\s*,\s*&?\s*([A-Za-z_][\w.]*)"
)


def _imports_any(parsed: Any, *prefixes: str) -> bool:
    return any(
        imp.module_path.startswith(pkg) for pkg in prefixes for imp in parsed.imports
    )


def _has_go_web_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "go":
            continue
        if _imports_any(parsed, *_GO_ROUTER_PKG_PATTERNS, _GO_HTTP_PKG):
            return True
    return False


def _add_go_route_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    """Emit edges for router-framework + net/http handler registrations.

    Only emit for files that themselves both reference a route call and
    import a router or ``net/http`` — conservative, mirrors the original
    router behaviour while admitting stdlib HTTP.
    """
    count = 0
    func_to_files = _build_function_to_file(parsed_files, ("go",))
    class_to_file = _build_class_to_file(parsed_files, ("go",))

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "go":
            continue
        if not _imports_any(parsed, *_GO_ROUTER_PKG_PATTERNS, _GO_HTTP_PKG):
            continue
        text = read_text(parsed)
        if not text:
            continue

        for m in _GO_ROUTE_CALL_RE.finditer(text):
            handler = m.group(1)
            targets = _resolve_go_handler(handler, parsed, func_to_files, class_to_file)
            for target in targets:
                if target != path and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

    return count


def _add_go_grpc_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    """Connect ``RegisterXxxServer(s, &impl{})`` sites to the impl-type file."""
    count = 0
    func_to_files = _build_function_to_file(parsed_files, ("go",))
    class_to_file = _build_class_to_file(parsed_files, ("go",))

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "go":
            continue
        text = read_text(parsed)
        if not text or "Server(" not in text:
            continue
        for m in _GO_GRPC_REGISTER_RE.finditer(text):
            impl = m.group(1)
            targets = _resolve_go_handler(impl, parsed, func_to_files, class_to_file)
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
    """Resolve `pkg.Func` / `recv.Method` / `Func` / `Type` to file paths."""
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
    # Bare identifier: a function/method name, or a type (gRPC ``&impl{}``).
    funcs = func_to_files.get(handler)
    if funcs:
        return list(funcs)
    type_file = class_to_file.get(handler)
    return [type_file] if type_file else []


class _GoWebHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        go_web_in_stack = any(
            token in dctx.stack_lower for token in ("gin", "echo", "chi", "grpc")
        )
        return go_web_in_stack or _has_go_web_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        count = _add_go_route_edges(graph, parsed_files, path_set)
        count += _add_go_grpc_edges(graph, parsed_files, path_set)
        return count


HANDLERS: list[FrameworkHandler] = [_GoWebHandler()]
