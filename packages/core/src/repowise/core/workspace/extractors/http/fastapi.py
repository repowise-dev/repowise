"""FastAPI / Python HTTP provider dialect — ``@app.get('/path')``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import PYTHON
from .dialect import METHODS, build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# @app.get('/path') or @router.post('/path')
_FASTAPI_RE = re.compile(
    rf"""@(?:app|router)\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


class FastApiDialect:
    name = "fastapi"
    extensions = PYTHON

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _FASTAPI_RE.finditer(ctx.content):
            c = build_provider_contract(
                ctx, method=m.group(1).upper(), path_raw=m.group(2), framework="fastapi"
            )
            if c is not None:
                out.append(c)
        return out
