"""ASP.NET (C#) HTTP provider dialect.

Covers:

* attribute routing — ``[HttpGet("path")]`` or ``[HttpGet] [Route("path")]``
  stitched onto the class ``[RoutePrefix("api/users")]`` or ``[Route("api/users")]``
  prefix;
* parameterless attributes — ``[HttpPost]`` whose route is the class prefix;
* tilde prefix overrides — ``[Route("~/healthz")]`` ignoring class prefix;
* minimal APIs — ``app.MapGet("/users", ...)``, prefix-stitched onto the
  ``MapGroup`` chain the receiver is bound to rather than onto a class route.

The minimal-API shape is recognised by ``ingestion.framework_routes``, shared
with the graph-edge builder that reads the same call for its handler argument.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import TYPE_CHECKING

from repowise.core.analysis.security.masking import mask_comments_and_strings, mask_dialect
from repowise.core.ingestion.framework_routes import aspnet_groups, aspnet_routes

from ..base import line_at
from ..calls import code_matches, outside_comments
from ..langs import CSHARP
from .dialect import build_provider_contract, nearest_prefix
from .mounts import compose_prefix, group_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Read on the masked code just past a class's attribute block.
_CLASS_DECL_RE = re.compile(
    r"""\s*(?:(?:public|private|protected|internal|abstract|sealed|static|partial)\s+)*class\s+\w+""",
    re.IGNORECASE,
)

_ROUTE_PREFIX_RE = re.compile(
    r"""\b(?:RoutePrefix|Route)\s*\(\s*(?:template\s*=\s*)?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Action-level HTTP verb: [HttpGet], [HttpPost("path")], [HttpGet, Route("path")], etc.
# Anchored to attribute context: must immediately follow an opening bracket '[' or comma ','
_ASPNET_VERB_ATTR_RE = re.compile(
    r"""(?<=[\[,])\s*Http(Get|Post|Put|Delete|Patch)\b(?:\s*\(\s*(?:template\s*=\s*)?['"]([^'"]+)['"]\s*\))?""",
    re.IGNORECASE,
)

# Action-level [Route("path")] in an attribute list
_ASPNET_ACTION_ROUTE_RE = re.compile(
    r"""\bRoute\s*\(\s*(?:template\s*=\s*)?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


def _attribute_blocks(code: str) -> list[tuple[int, int]]:
    """The ``[...]`` runs of *code* (comments and strings blanked, offsets kept):
    outermost brackets, with lists separated only by whitespace merged, so
    ``[HttpGet]`` and ``[Route("x")]`` on the lines above an action are one block.
    """
    blocks: list[tuple[int, int]] = []
    depth = start = 0
    for i, ch in enumerate(code):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
            if depth:
                continue
            if blocks and not code[blocks[-1][1] : start].strip():
                blocks[-1] = (blocks[-1][0], i + 1)
            else:
                blocks.append((start, i + 1))
    return blocks


def _class_mappings(
    content: str, code: str, blocks: list[tuple[int, int]]
) -> list[tuple[int, str]]:
    """``(offset, prefix)`` for each class whose attribute block holds a
    ``[RoutePrefix]`` or ``[Route]``."""
    mappings: list[tuple[int, str]] = []
    for start, end in blocks:
        route = _ROUTE_PREFIX_RE.search(content, start, end)
        if route and _CLASS_DECL_RE.match(code, end):
            mappings.append((end, route.group(1).rstrip("/")))
    return mappings


def _block_at(content: str, blocks: list[tuple[int, int]], pos: int) -> str | None:
    """The text of the attribute block holding *pos*, None outside every block."""
    idx = bisect_right(blocks, (pos, len(content))) - 1
    if idx >= 0 and blocks[idx][0] <= pos < blocks[idx][1]:
        return content[blocks[idx][0] : blocks[idx][1]]
    return None


def _compose_aspnet_path(prefix: str, action_path: str) -> str:
    if action_path.startswith("~"):
        # Absolute route override in ASP.NET Web API / MVC
        return action_path.lstrip("~").lstrip("/")
    if prefix and action_path:
        return prefix.rstrip("/") + "/" + action_path.lstrip("/")
    if prefix:
        return prefix
    return action_path


class AspNetDialect:
    name = "aspnet"
    extensions = CSHARP
    # `[HttpGet(...)]` and `.MapGet(...)`, read without regard to case.
    requires = ("Http", "http", "HTTP", "MapGet", "MapPost", "MapPut", "MapDelete", "MapPatch")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        code = mask_comments_and_strings(
            content, strings=True, hash_comments=False, **mask_dialect(ctx.suffix)
        )
        blocks = _attribute_blocks(code)
        class_mappings = _class_mappings(content, code, blocks)

        out: list[Contract] = []

        # Action method attribute routing
        for m in code_matches(ctx, _ASPNET_VERB_ATTR_RE):
            block = _block_at(content, blocks, m.start())
            if block is None:
                continue
            method = m.group(1).upper()
            inline_path = m.group(2)
            if inline_path is not None:
                action_path = inline_path
            else:
                route_match = _ASPNET_ACTION_ROUTE_RE.search(block)
                action_path = route_match.group(1) if route_match else ""

            prefix = nearest_prefix(class_mappings, m.start())
            path_raw = _compose_aspnet_path(prefix, action_path)
            if not path_raw:
                continue

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
        for route in outside_comments(ctx, aspnet_routes(content)):
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

        return out
