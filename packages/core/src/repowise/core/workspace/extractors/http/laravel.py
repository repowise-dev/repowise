"""Laravel (PHP) HTTP provider dialect — ``Route::get('/path', ...)``.

The route call is recognised by ``ingestion.framework_routes``, shared with the
graph-edge builder that reads the same call for its controller class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import HTTP_METHODS, laravel_routes

from ..base import line_at
from ..langs import PHP
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


class LaravelDialect:
    name = "laravel"
    extensions = PHP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for route in laravel_routes(ctx.content):
            # `resource`/`apiResource` stand for a set of routes and `any`/`match`
            # for an unnamed verb, so neither is one method+path contract.
            if route.verb not in HTTP_METHODS or not route.path:
                continue
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=route.path,
                framework="laravel",
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out
