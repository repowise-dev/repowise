"""Express / NestJS convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..framework_routes import express_routes, js_router_bindings, match_paren
from ..resolvers import ResolverContext
from .base import (
    JS_STRING_LITERAL_RE,
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    _build_ts_var_to_file,
    add_symbol_edge,
    read_text_from,
)

if TYPE_CHECKING:
    import networkx as nx


_EXPRESS_USE_RE = re.compile(r"\.\s*use\s*\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?(\w+)\s*[,)]")
_NEST_MODULE_RE = re.compile(r"@Module\s*\(\s*\{([^}]*)\}\s*\)", re.DOTALL)
_NEST_ARRAY_FIELD_RE = re.compile(r"\b(?:controllers|providers|imports|exports)\s*:\s*\[([^\]]*)\]")
_IDENT_RE = re.compile(r"\b([A-Z]\w*)\b")

_ARG_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _has_express_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if mp == "express" or mp.startswith("@nestjs/"):
                return True
    return False


def _add_express_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("typescript", "javascript"))
    source_map = ctx.source_map

    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        text = read_text_from(path, parsed, source_map)
        if not text:
            continue
        # The import map is built only once a pattern names an identifier to
        # look up: most TS files mention neither Express nor ``@Module``.
        if "express" in text or any(imp.module_path == "express" for imp in parsed.imports):
            count += _add_express_use_edges(graph, path, parsed, text, ctx, path_set)
            count += _add_express_route_edges(graph, path, parsed, text)
        count += _add_nest_module_edges(graph, path, parsed, text, ctx, path_set, class_to_file)

    return count


def _add_express_use_edges(
    graph: nx.DiGraph,
    path: str,
    parsed: Any,
    text: str,
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """``app.use(routerVar)``: link the file to the module that exports the router."""
    count = 0
    for m in _EXPRESS_USE_RE.finditer(text):
        target = _build_ts_var_to_file(parsed, path, ctx, path_set).get(m.group(1))
        if target and target in path_set and _add_edge_if_new(graph, path, target):
            count += 1
    return count


def _add_express_route_edges(graph: nx.DiGraph, path: str, parsed: Any, text: str) -> int:
    """``router.get('/x', handler)``: bind the module to the local handler functions."""
    local_funcs = {
        sym.name: sym.id for sym in parsed.symbols if sym.kind in ("function", "method")
    }
    receivers = {var for var, fw in js_router_bindings(text).items() if fw == "express"}
    if not (local_funcs and receivers):
        return 0
    count = 0
    module_sym = f"{path}::__module__"
    for route in express_routes(text):
        if route.receiver not in receivers:
            continue
        close = match_paren(text, route.paren_offset)
        if close == -1:
            continue
        arg_blob = JS_STRING_LITERAL_RE.sub("", text[route.paren_offset + 1 : close])
        for ident in _ARG_IDENT_RE.finditer(arg_blob):
            sym_id = local_funcs.get(ident.group(0))
            if sym_id and add_symbol_edge(graph, module_sym, sym_id):
                count += 1
    return count


def _add_nest_module_edges(
    graph: nx.DiGraph,
    path: str,
    parsed: Any,
    text: str,
    ctx: ResolverContext,
    path_set: set[str],
    class_to_file: dict[str, str],
) -> int:
    """NestJS ``@Module({ controllers: [...], providers: [...], imports: [...] })``."""
    count = 0
    for mod_match in _NEST_MODULE_RE.finditer(text):
        for arr_match in _NEST_ARRAY_FIELD_RE.finditer(mod_match.group(1)):
            for ident_match in _IDENT_RE.finditer(arr_match.group(1)):
                cls = ident_match.group(1)
                var_to_file = _build_ts_var_to_file(parsed, path, ctx, path_set)
                target = var_to_file.get(cls) or class_to_file.get(cls)
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
    return count


class _ExpressHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        express_in_stack = any(
            token in dctx.stack_lower for token in ("express", "nestjs", "nest", "nest.js")
        )
        return express_in_stack or _has_express_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_express_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_ExpressHandler()]
