"""Go HTTP provider dialect — gin/echo/chi ``r.GET("/path", ...)`` and
stdlib ``.HandleFunc("/path", ...)`` (which carries no method, recorded as
``*``)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import GO
from .dialect import METHODS_UPPER, build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# r.GET("/path", ...) or .HandleFunc("/path", ...)
_GO_ROUTE_RE = re.compile(
    rf"""\.({METHODS_UPPER}|Handle|HandleFunc)\s*\(\s*['"]([^'"]+)['"]""",
)


class GoDialect:
    name = "go"
    extensions = GO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _GO_ROUTE_RE.finditer(ctx.content):
            method_raw = m.group(1)
            # Handle/HandleFunc don't carry a method verb.
            method = "*" if method_raw in ("Handle", "HandleFunc") else method_raw.upper()
            c = build_provider_contract(ctx, method=method, path_raw=m.group(2), framework="go")
            if c is not None:
                out.append(c)
        return out
