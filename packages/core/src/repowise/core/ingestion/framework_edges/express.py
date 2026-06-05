"""Express / NestJS convention edges.

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
    _build_ts_var_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_EXPRESS_USE_RE = re.compile(r"\.\s*use\s*\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?(\w+)\s*[,)]")
_NEST_MODULE_RE = re.compile(r"@Module\s*\(\s*\{([^}]*)\}\s*\)", re.DOTALL)
_NEST_ARRAY_FIELD_RE = re.compile(r"\b(?:controllers|providers|imports|exports)\s*:\s*\[([^\]]*)\]")
_IDENT_RE = re.compile(r"\b([A-Z]\w*)\b")

_EXPRESS_RECEIVER_RE = re.compile(
    r"\b(\w+)\s*(?::[^=;\n]+)?=\s*(?:express\s*\(\s*\)|(?:express\s*\.\s*)?Router\s*\()"
)
_ROUTE_CALL_START_RE = re.compile(
    r"\b(\w+)\s*\.\s*(?:get|post|put|delete|patch|options|head|all|use)\s*\("
)
_STRING_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`")
_ARG_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _match_paren(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _add_reads_edge(graph: nx.DiGraph, source: str, target: str) -> bool:
    if source == target or not graph.has_node(source) or not graph.has_node(target):
        return False
    if graph.has_edge(source, target):
        return False
    graph.add_edge(source, target, edge_type="reads", imported_names=[])
    return True


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

    # ---- Express: app.use(routerVar) ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        text = read_text(parsed)
        if not text:
            continue

        var_to_file = _build_ts_var_to_file(parsed, path, ctx, path_set)

        if "express" in text or any(imp.module_path == "express" for imp in parsed.imports):
            for m in _EXPRESS_USE_RE.finditer(text):
                var_name = m.group(1)
                target = var_to_file.get(var_name)
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

            local_funcs = {
                sym.name: sym.id
                for sym in parsed.symbols
                if sym.kind in ("function", "method")
            }
            receivers = {m.group(1) for m in _EXPRESS_RECEIVER_RE.finditer(text)}
            if local_funcs and receivers:
                module_sym = f"{path}::__module__"
                for m in _ROUTE_CALL_START_RE.finditer(text):
                    if m.group(1) not in receivers:
                        continue
                    close = _match_paren(text, m.end() - 1)
                    if close == -1:
                        continue
                    arg_blob = _STRING_LITERAL_RE.sub("", text[m.end() : close])
                    for ident in _ARG_IDENT_RE.finditer(arg_blob):
                        sym_id = local_funcs.get(ident.group(0))
                        if sym_id and _add_reads_edge(graph, module_sym, sym_id):
                            count += 1

        # ---- NestJS: @Module({ controllers: [...], providers: [...], imports: [...] }) ----
        for mod_match in _NEST_MODULE_RE.finditer(text):
            body = mod_match.group(1)
            for arr_match in _NEST_ARRAY_FIELD_RE.finditer(body):
                for ident_match in _IDENT_RE.finditer(arr_match.group(1)):
                    cls = ident_match.group(1)
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
