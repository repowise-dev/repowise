"""ASP.NET (C#) HTTP provider dialect.

Covers three shapes that can co-exist in one file:

* attribute routing — ``[HttpGet("path")]`` stitched onto the class
  ``[Route("api/users")]`` prefix;
* parameterless attributes — ``[HttpPost]`` whose route is the class prefix;
* minimal APIs — ``app.MapGet("/users", ...)``, prefix-stitched onto the
  ``MapGroup`` chain the receiver is bound to rather than onto a class route.

The minimal-API shape is recognised by ``ingestion.framework_routes``, shared
with the graph-edge builder that reads the same call for its handler argument.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import aspnet_groups, aspnet_routes

from ..base import line_at
from ..langs import CSHARP
from .dialect import build_provider_contract, nearest_prefix
from .mounts import compose_prefix, group_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# [HttpGet("path")], [HttpPost("path")], etc. The leading bracket may be on its
# own line, so we anchor on the attribute name.
_ASPNET_METHOD_RE = re.compile(
    r"""\[\s*Http(Get|Post|Put|Delete|Patch)\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Parameterless attribute: [HttpPost] / [HttpGet] — the route is the class
# prefix only.
_ASPNET_BARE_METHOD_RE = re.compile(
    r"""\[\s*Http(Get|Post|Put|Delete|Patch)\s*\]""",
    re.IGNORECASE,
)

# Class-level prefix: [Route("api/users")] above an [ApiController] class.
_ASPNET_CLASS_ROUTE_RE = re.compile(
    r"""\[\s*Route\s*\(\s*['"]([^'"]+)['"]""",
)


class AspNetDialect:
    name = "aspnet"
    extensions = CSHARP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        class_mappings: list[tuple[int, str]] = [
            (cm.start(), cm.group(1).rstrip("/")) for cm in _ASPNET_CLASS_ROUTE_RE.finditer(content)
        ]

        out: list[Contract] = []

        # Attribute routing — stitched onto the nearest class [Route(...)].
        for m in _ASPNET_METHOD_RE.finditer(content):
            method = m.group(1).upper()
            path_raw = m.group(2)
            prefix = nearest_prefix(class_mappings, m.start())
            if prefix:
                path_raw = prefix + "/" + path_raw.lstrip("/")
            c = build_provider_contract(
                ctx,
                method=method,
                path_raw=path_raw,
                framework="aspnet",
                line=line_at(content, m.start()),
            )
            if c is not None:
                out.append(c)

        # Minimal API — the prefix comes from the MapGroup chain its receiver
        # is bound to, not from a class [Route(...)].
        prefixes = group_prefixes(aspnet_groups(content))
        for route in aspnet_routes(content):
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=compose_prefix(prefixes.get(route.receiver or "", ""), route.path or ""),
                framework="aspnet-minimal",
                line=line_at(content, route.offset),
                handler=route.handler,
            )
            if c is not None:
                out.append(c)

        # Parameterless [HttpPost] — route is whichever class [Route("...")]
        # precedes it. Without a class route there is no useful path to record.
        if class_mappings:
            for m in _ASPNET_BARE_METHOD_RE.finditer(content):
                method = m.group(1).upper()
                prefix = nearest_prefix(class_mappings, m.start())
                if not prefix:
                    continue
                c = build_provider_contract(
                    ctx,
                    method=method,
                    path_raw=prefix,
                    framework="aspnet",
                    line=line_at(content, m.start()),
                )
                if c is not None:
                    out.append(c)

        return out
