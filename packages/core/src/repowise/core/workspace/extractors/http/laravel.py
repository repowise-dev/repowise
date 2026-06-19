"""Laravel (PHP) HTTP provider dialect — ``Route::get('/path', ...)``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import PHP
from .dialect import METHODS, build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Route::get('/path', ...)
_LARAVEL_RE = re.compile(
    rf"""Route::({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


class LaravelDialect:
    name = "laravel"
    extensions = PHP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _LARAVEL_RE.finditer(ctx.content):
            c = build_provider_contract(
                ctx, method=m.group(1).upper(), path_raw=m.group(2), framework="laravel"
            )
            if c is not None:
                out.append(c)
        return out
