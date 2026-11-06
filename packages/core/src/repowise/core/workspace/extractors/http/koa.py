"""Koa router HTTP provider dialect: ``@koa/router`` / ``koa-router`` routes with
their prefixes.

A file that imports the router package is read here and not by the Express
dialect, because a Koa route's path depends on its router: the router's own
``prefix``, the router that nests it in the same file
(``api.use('/v1', child.routes())``), and the module that nests the whole file
(``api.use(require('./users'))``). Recognition is in
``ingestion.framework_routes_koa``; ``collect_mounts`` records module mounts,
keyed ``koa:<module>``, and ``extract`` composes the three.

A prefix that is not a string this can resolve leaves the router's routes
unread rather than placed at a guessed path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    JS_DEFAULT_ROUTER_NAMES,
    express_routes,
)
from repowise.core.ingestion.framework_routes_js import js_imports, module_key, resolve_module
from repowise.core.ingestion.framework_routes_koa import koa_router_file, koa_routers, koa_uses

from ..base import line_at
from ..calls import file_strings, outside_comments
from ..langs import JS_TS
from .dialect import build_provider_contract
from .mounts import (
    add_mounts,
    compose_prefix,
    declare_module_mount,
    module_prefix,
    prefix_text,
)

if TYPE_CHECKING:
    from repowise.core.ingestion.framework_routes_koa import KoaUse
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_NAMESPACE = "koa"
# Routers nest a few levels deep in one file; a longer chain is a cycle.
_MAX_DEPTH = 16
# koa-router's `del` is its name for DELETE.
_VERB_ALIASES = {"DEL": "DELETE"}


class _File:
    """One Koa file's routers and where each is served within the file."""

    def __init__(self, ctx: ScanContext) -> None:
        strings = file_strings(ctx)
        self.own = {
            var: None if expr is None else prefix_text(strings, expr)
            for var, expr in koa_routers(ctx.content).items()
        }
        self.uses: list[KoaUse] = list(outside_comments(ctx, koa_uses(ctx.content)))
        self._paths = {id(use): prefix_text(strings, use.path) for use in self.uses}

    def use_path(self, use: KoaUse) -> str | None:
        return self._paths[id(use)]

    def prefix_of(self, router: str, depth: int = 0) -> str | None:
        """Where *router* is served within this file; None when unreadable.

        A router mounted through a wrapper (``mount('/api', r.routes())``) or
        under parents that place it differently is unreadable.
        """
        own = self.own.get(router, "")
        if depth > _MAX_DEPTH or own is None or any(router in u.wrapped for u in self.uses):
            return None
        placed: set[str | None] = set()
        for use in self.uses:
            if router not in use.routers or use.receiver == router:
                continue
            outer, path = self.prefix_of(use.receiver, depth + 1), self.use_path(use)
            placed.add(None if outer is None or path is None else compose_prefix(outer, path))
        if not placed:
            return own
        if len(placed) > 1 or None in placed:
            return None
        return compose_prefix(placed.pop() or "", own)


class KoaDialect:
    name = "koa"
    extensions = JS_TS
    requires = ("koa-router", "@koa/router")

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """Each module this file nests under one of its routers."""
        if not koa_router_file(ctx.content):
            return {}
        f = _File(ctx)
        imports = js_imports(ctx.content)
        here = module_key(ctx.rel_path)
        out: dict[str, str] = {}
        for use in f.uses:
            for router in use.routers:
                spec = _module_spec(router, imports)
                module = resolve_module(ctx.rel_path, spec) if spec else None
                if module is None or module == here:
                    continue
                outer, path = f.prefix_of(use.receiver), f.use_path(use)
                prefix = None if outer is None or path is None else compose_prefix(outer, path)
                add_mounts(out, declare_module_mount(_NAMESPACE, module, prefix, here))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if not koa_router_file(ctx.content):
            return []
        _mounted, module = module_prefix(ctx.mounts, _NAMESPACE, module_key(ctx.rel_path))
        if module is None:
            return []
        f = _File(ctx)
        out: list[Contract] = []
        for route in outside_comments(ctx, express_routes(ctx.content)):
            verb = _VERB_ALIASES.get(route.verb, route.verb)
            receiver = route.receiver or ""
            if verb not in HTTP_METHODS or not route.path:
                continue
            if receiver not in f.own and receiver not in JS_DEFAULT_ROUTER_NAMES:
                continue
            within = f.prefix_of(receiver)
            if within is None:
                continue
            c = build_provider_contract(
                ctx,
                method=verb,
                path_raw=compose_prefix(compose_prefix(module, within), route.path),
                framework="koa",
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out


def _module_spec(expr: str, imports: dict[str, tuple[str, str]]) -> str | None:
    """The module spec a router argument names: ``require('./x')`` or an import."""
    if expr.startswith("require"):
        start, end = expr.find("("), expr.rfind(")")
        inner = expr[start + 1 : end].strip()
        return inner[1:-1] if inner[:1] in "'\"" and inner[-1:] == inner[:1] else None
    found = imports.get(expr)
    return found[0] if found else None
