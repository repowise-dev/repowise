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

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_MOUNT_KEY = "laravel-route-file:"
# The mount merge drops a key two files disagree on. This second key, always
# the same value, survives it, so a declared-but-dropped file reads as
# ambiguous (refused) rather than undeclared (served at the default).
_DECLARED_KEY = "laravel-route-file-declared:"
# Mount values are strings; a declared prefix this cannot read travels as this.
_UNREADABLE = "\0"


class LaravelDialect:
    name = "laravel"
    extensions = PHP

    def collect_mounts(self, content: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, prefix in laravel_route_file_prefixes(content).items():
            out[f"{_MOUNT_KEY}{name}"] = _UNREADABLE if prefix is None else prefix
            out[f"{_DECLARED_KEY}{name}"] = ""
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        name = ctx.rel_path.rpartition("/")[2]
        declared: dict[str, str | None] = {}
        if f"{_DECLARED_KEY}{name}" in ctx.mounts:
            value = ctx.mounts.get(f"{_MOUNT_KEY}{name}", _UNREADABLE)
            declared[name] = None if value == _UNREADABLE else value
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
