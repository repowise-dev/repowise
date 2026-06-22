"""Express / Node.js HTTP provider dialect — ``router.get('/path', ...)``.

Express routers carry no in-file prefix; the mount lives in a separate
``app.use('/prefix', router)`` call, so the real path is recovered by stitching
the cross-file mount prefix (collected by the orchestrator) onto the route.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JS_TS
from .dialect import METHODS, build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# router.get('/path', ...) / app.post('/path', ...). The receiver variable is
# captured so its mount prefix can be resolved; the negative lookbehind for ``@``
# keeps decorator frameworks (FastAPI ``@app.get``, NestJS ``@Get``) out.
_EXPRESS_RE = re.compile(
    rf"""(?<!@)(\w+)\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Variables bound to an Express app / router: ``r = express.Router()``,
# ``app = express()``, ``router = require('express').Router()``.
_ROUTER_BIND_RE = re.compile(r"""(\w+)\s*=\s*(?:[\w.]*\.Router\s*\(|express\s*\(|Router\s*\()""")

# app.use('/prefix', router) — a cross-file (or in-file) router mount.
_APP_USE_RE = re.compile(r"""\.use\s*\(\s*['"]([^'"]+)['"]\s*,\s*([\w.]+)""")

_DEFAULT_ROUTER_NAMES = frozenset({"app", "router"})


class ExpressDialect:
    name = "express"
    extensions = JS_TS

    def collect_mounts(self, content: str) -> dict[str, str]:
        """Find ``app.use('/prefix', router)`` mounts declared in *content*."""
        out: dict[str, str] = {}
        for m in _APP_USE_RE.finditer(content):
            out[m.group(2).split(".")[-1]] = m.group(1)
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        known = {m.group(1) for m in _ROUTER_BIND_RE.finditer(ctx.content)}
        known |= _DEFAULT_ROUTER_NAMES

        out: list[Contract] = []
        for m in _EXPRESS_RE.finditer(ctx.content):
            var, method, path_raw = m.group(1), m.group(2), m.group(3)
            if var not in known:
                continue
            path = compose_prefix(ctx.mounts.get(var, ""), path_raw)
            c = build_provider_contract(
                ctx, method=method.upper(), path_raw=path, framework="express"
            )
            if c is not None:
                out.append(c)
        return out
