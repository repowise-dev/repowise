"""Go HTTP provider dialect — gin/echo/chi ``r.GET("/path", ...)`` and
stdlib ``.HandleFunc("/path", ...)`` (which carries no method, recorded as
``*``).

Route groups (``api := r.Group("/api"); v1 := api.Group("/v1")``) are resolved
transitively so a handler on ``v1`` records its full ``/api/v1/...`` path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import GroupMatch

from ..base import line_at
from ..langs import GO
from .dialect import METHODS_UPPER, build_provider_contract
from .mounts import compose_prefix, group_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# group := parent.Group("/segment") — a nested router group.
_GROUP_RE = re.compile(r"""(\w+)\s*:?=\s*(\w+)\.Group\s*\(\s*['"]([^'"]+)['"]""")

# r.GET("/path", ...) or .HandleFunc("/path", ...). The receiver variable is
# captured (when it is a bare identifier) so its group prefix can be resolved; an
# empty capture (e.g. an inline-chained ``Group("/x").GET(...)``) falls back to no
# prefix, preserving the pre-group-resolution behaviour.
_GO_ROUTE_RE = re.compile(
    rf"""(\w*)\.({METHODS_UPPER}|Handle|HandleFunc)\s*\(\s*['"]([^'"]+)['"]""",
)


def _groups(content: str) -> list[GroupMatch]:
    return [
        GroupMatch(var=m.group(1), parent=m.group(2), prefix=m.group(3))
        for m in _GROUP_RE.finditer(content)
    ]


class GoDialect:
    name = "go"
    extensions = GO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        prefixes = group_prefixes(_groups(ctx.content))
        out: list[Contract] = []
        for m in _GO_ROUTE_RE.finditer(ctx.content):
            var, method_raw, path_raw = m.group(1), m.group(2), m.group(3)
            # Handle/HandleFunc don't carry a method verb.
            method = "*" if method_raw in ("Handle", "HandleFunc") else method_raw.upper()
            path = compose_prefix(prefixes.get(var, ""), path_raw)
            c = build_provider_contract(
                ctx,
                method=method,
                path_raw=path,
                framework="go",
                line=line_at(ctx.content, m.start()),
            )
            if c is not None:
                out.append(c)
        return out
