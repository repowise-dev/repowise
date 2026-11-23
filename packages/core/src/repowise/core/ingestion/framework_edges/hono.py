"""Hono / Fastify / Koa route framework edges.

These frameworks all share a common router DSL: an ``app`` (or ``router``)
instance has methods ``.get``/``.post``/``.put``/``.delete``/``.patch``/
``.use``/``.route`` whose final argument is a handler identifier. When
the handler is an imported identifier, the static parser sees an
``imports`` edge but no usage edge to the handler's file — making the
handler's only-from-route caller invisible. This module emits a
``framework`` edge from the router-DSL call site to the handler's file.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_ts_var_to_file,
    read_text_from,
)

if TYPE_CHECKING:
    import networkx as nx


# Matches ``app.get('/path', handler)`` / ``router.post("/x", h)`` /
# ``app.use(middleware)`` / ``app.route('/', sub)`` where the last argument
# is a bare identifier. Multi-argument middlewares (`'/x', a, b, handler`)
# only capture the last identifier — good enough to reach the handler's
# file via the var map.
_ROUTE_METHODS = (
    "get", "post", "put", "delete", "patch", "options", "head", "all",
    "use", "route", "register", "mount", "addHook", "on",
)
_ROUTE_METHODS_ALT = "|".join(_ROUTE_METHODS)
_ROUTE_CALL_RE = re.compile(
    r"""\.\s*(?:""" + _ROUTE_METHODS_ALT + r""")\s*\(
        [^)]*?
        \b([A-Za-z_$][\w$]*)\s*
        \)""",
    re.VERBOSE,
)
_HONO_IMPORT_TOKENS = ("hono", "fastify", "@fastify/", "koa", "@koa/", "elysia")


def _has_router_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        for imp in parsed.imports:
            mp = imp.module_path.lower()
            if any(tok in mp for tok in _HONO_IMPORT_TOKENS):
                return True
    return False


class _RouterDslHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        for tok in ("hono", "fastify", "koa", "elysia"):
            if tok in dctx.stack_lower:
                return True
        return _has_router_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        count = 0
        for path, parsed in parsed_files.items():
            if parsed.file_info.language not in ("typescript", "javascript"):
                continue
            text = read_text_from(path, parsed, ctx.source_map)
            if not text:
                continue
            lowered = text.lower()
            if not any(tok in lowered for tok in ("hono", "fastify", "koa", "elysia")):
                continue
            for m in _ROUTE_CALL_RE.finditer(text):
                # Built on the first route call, not per file: the token test
                # above passes many files that register no route at all.
                target = _build_ts_var_to_file(parsed, path, ctx, path_set).get(m.group(1))
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
        return count


HANDLERS: list[FrameworkHandler] = [_RouterDslHandler()]
