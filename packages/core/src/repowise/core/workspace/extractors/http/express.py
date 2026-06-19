"""Express / Node.js HTTP provider dialect — ``router.get('/path', ...)``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JS_TS
from .dialect import METHODS, build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# router.get('/path', ...) or app.post('/path', ...). The negative lookbehind for
# ``@`` keeps FastAPI decorators (``@app.get``) from matching here.
_EXPRESS_RE = re.compile(
    rf"""(?<!@)(?:router|app)\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


class ExpressDialect:
    name = "express"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _EXPRESS_RE.finditer(ctx.content):
            c = build_provider_contract(
                ctx, method=m.group(1).upper(), path_raw=m.group(2), framework="express"
            )
            if c is not None:
                out.append(c)
        return out
