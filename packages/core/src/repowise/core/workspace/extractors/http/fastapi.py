"""FastAPI / Python HTTP provider dialect — ``@app.get('/path')``.

Resolves the real route path by stitching three segments where present:
a cross-file ``include_router(prefix=...)`` mount, the in-file
``APIRouter(prefix=...)`` binding, and the decorator path itself.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import PYTHON
from .dialect import METHODS, build_provider_contract
from .mounts import balanced_args, compose_prefix, router_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# @<var>.get('/path') / @<var>.post('/path'). The variable is captured so its
# router prefix can be resolved; only known routers (those bound to APIRouter /
# FastAPI in-file, plus the conventional ``app`` / ``router`` names) are kept, so
# an unrelated ``@cache.get(...)`` decorator is not mistaken for a route.
_FASTAPI_RE = re.compile(
    rf"""@(\w+)\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# include_router(...) — a cross-file mount of a router.
_INCLUDE_ROUTER_RE = re.compile(r"""include_router\s*\(""")
_FIRST_ARG_RE = re.compile(r"""\s*([\w.]+)""")
_PREFIX_KW_RE = re.compile(r"""prefix\s*=\s*['"]([^'"]+)['"]""")

_DEFAULT_ROUTER_NAMES = frozenset({"app", "router"})


class FastApiDialect:
    name = "fastapi"
    extensions = PYTHON

    def collect_mounts(self, content: str) -> dict[str, str]:
        """Find ``include_router(var, prefix=...)`` mounts declared in *content*.

        Keyed by the router variable's final name segment (``pkg.router`` ->
        ``router``); only mounts that carry an explicit ``prefix=`` are recorded.
        """
        out: dict[str, str] = {}
        for m in _INCLUDE_ROUTER_RE.finditer(content):
            args = balanced_args(content, m.end() - 1)  # m.end()-1 is the '('
            var_m = _FIRST_ARG_RE.match(args)
            pm = _PREFIX_KW_RE.search(args)
            if var_m and pm:
                out[var_m.group(1).split(".")[-1]] = pm.group(1)
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        prefixes = router_prefixes(ctx.content, "APIRouter|FastAPI")
        known = set(prefixes) | _DEFAULT_ROUTER_NAMES

        out: list[Contract] = []
        for m in _FASTAPI_RE.finditer(ctx.content):
            var, method, path_raw = m.group(1), m.group(2), m.group(3)
            if var not in known:
                continue
            # In-file APIRouter(prefix=...) then any cross-file mount of this var.
            path = compose_prefix(prefixes.get(var, ""), path_raw)
            path = compose_prefix(ctx.mounts.get(var, ""), path)
            c = build_provider_contract(
                ctx, method=method.upper(), path_raw=path, framework="fastapi"
            )
            if c is not None:
                out.append(c)
        return out
