"""Node router-DSL HTTP provider dialect — ``router.get('/path', ...)``.

Express, Hono, Fastify, Koa and Elysia all serve routes through this one call
shape, and only the variable's binding says which is which. The contract is
labelled from that binding rather than assumed to be Express. A Fastify file
(or a module a Fastify file registers) and a ``koa-router`` file are left to
their own dialects, because there a route's path depends on the plugin or
router it runs in.

Routers carry no in-file prefix; the mount lives in a separate
``app.use('/prefix', router)`` call, so the real path is recovered by stitching
the cross-file mount prefix (collected by the orchestrator) onto the route.

The route call and the bindings both come from ``ingestion.framework_routes``,
shared with the graph-edge builders that scan the same call's argument list.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    JS_DEFAULT_ROUTER_NAMES,
    express_routes,
    js_router_bindings,
)
from repowise.core.ingestion.framework_routes_koa import koa_router_file

from ..base import line_at
from ..calls import code_matches, outside_comments
from ..langs import JS_TS
from .dialect import build_provider_contract
from .fastify import fastify_module
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# app.use('/prefix', router) — a cross-file (or in-file) router mount.
_APP_USE_RE = re.compile(
    r"""\.use\s*\(\s*['"]([^'"]+)['"]\s*,\s*"""
    r"""([\w.]+?)(?:\.routes\s*\(\s*\)|(?![\w.]|\s*\())"""
)


class ExpressDialect:
    name = "express"
    extensions = JS_TS
    # A route's receiver is a default name or a router a constructor binds,
    # and a mount is a `.use(`; `app .get(` and `.use (` are allowed.
    requires = (
        "app.", "app ", "app\n", "app\t", "router.", "router ", "router\n", "router\t",
        "Router", "express", "Hono", "astify", "Koa", "koa", "Elysia",
        ".use(", ".use ", ".use\n", ".use\t",
    )  # fmt: skip

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """Find ``app.use('/prefix', router)`` mounts declared in the file."""
        out: dict[str, str] = {}
        for m in code_matches(ctx, _APP_USE_RE):
            out[m.group(2).split(".")[-1]] = m.group(1)
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if fastify_module(ctx) or koa_router_file(ctx.content):
            return []
        bindings = js_router_bindings(ctx.content)
        # `app`/`router` are routers whose constructor is in another file, so
        # they take the file's framework when it binds exactly one, and Express
        # otherwise — which is what they were always labelled.
        served = set(bindings.values())
        default = served.pop() if len(served) == 1 else "express"

        out: list[Contract] = []
        for route in outside_comments(ctx, express_routes(ctx.content)):
            # The registry also yields `use`/`all`, which the graph consumer
            # scans for handlers but which name no HTTP method here.
            if route.verb not in HTTP_METHODS or not route.path:
                continue
            if route.receiver in bindings:
                framework = bindings[route.receiver]
            elif route.receiver in JS_DEFAULT_ROUTER_NAMES:
                framework = default
            else:
                continue
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=compose_prefix(ctx.mounts.get(route.receiver, ""), route.path),
                framework=framework,
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out
