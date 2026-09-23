"""Laravel (PHP) HTTP provider dialect — ``Route::get('/path', ...)``.

The route call is recognised by ``ingestion.framework_routes``, shared with the
graph-edge builder that reads the same call for its controller class. Where a
route file is served (``routes/api.php`` under ``/api``, or whatever
``bootstrap/app.php`` or a route provider declares) is collected repo-wide
through the orchestrator's mount pass, under keys no router variable can take.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    laravel_route_file_prefixes,
    laravel_route_prefix,
    laravel_routes,
)

from ..base import line_at
from ..langs import PHP
from .dialect import build_provider_contract
from .mounts import declare_mount, declared_mount

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_MOUNT_KEY = "laravel-route-file:"


class LaravelDialect:
    name = "laravel"
    extensions = PHP

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, prefix in laravel_route_file_prefixes(ctx.content).items():
            out.update(declare_mount(f"{_MOUNT_KEY}{name}", prefix))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        name = ctx.rel_path.rpartition("/")[2]
        declared: dict[str, str | None] = {}
        found, value = declared_mount(ctx.mounts, f"{_MOUNT_KEY}{name}")
        if found:
            declared[name] = value
        prefix = laravel_route_prefix(ctx.rel_path, declared)
        if prefix is None:
            return []  # served under a prefix this cannot read
        out: list[Contract] = []
        for route in laravel_routes(ctx.content, prefix):
            # `any` names no single verb, and a computed path no one route.
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
