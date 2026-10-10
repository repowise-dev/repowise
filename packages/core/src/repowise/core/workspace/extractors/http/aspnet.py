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
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import aspnet_groups, aspnet_routes

from ..base import line_at
from ..langs import CSHARP
from .dialect import build_provider_contract, nearest_prefix
from .mounts import compose_prefix, group_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Class-level route prefix: [RoutePrefix("Orders")] or [Route("api/users")]
# placed before a class declaration.
_ASPNET_CLASS_ROUTE_RE = re.compile(
    r"""\[[^\]]*\b(?:RoutePrefix|Route)\s*\(\s*(?:template\s*=\s*)?['"]([^'"]+)['"]\s*\)[^\]]*\](?:\s*\[[^\]]+\])*\s*(?:(?:public|private|protected|internal|abstract|sealed|static|partial)\s+)*class\s+\w+""",
    re.IGNORECASE,
)

# Action-level HTTP verb: [HttpGet], [HttpPost("path")], [HttpGet, Route("path")], etc.
_ASPNET_VERB_ATTR_RE = re.compile(
    r"""\bHttp(Get|Post|Put|Delete|Patch)\b(?:\s*\(\s*(?:template\s*=\s*)?['"]([^'"]+)['"]\s*\))?""",
    re.IGNORECASE,
)

# Action-level [Route("path")] in an attribute list
_ASPNET_ACTION_ROUTE_RE = re.compile(
    r"""\bRoute\s*\(\s*(?:template\s*=\s*)?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


def _skip_whitespace_and_comments_backwards(content: str, pos: int) -> int:
    p = pos
    while p >= 0:
        if content[p] in " \t\r\n":
            p -= 1
        elif p >= 1 and content[p - 1 : p + 1] == "*/":
            comment_start = content.rfind("/*", 0, p - 1)
            if comment_start != -1:
                p = comment_start - 1
            else:
                break
        elif content[p] == "\n":
            p -= 1
        else:
            line_start = content.rfind("\n", 0, p)
            line_start = 0 if line_start == -1 else line_start + 1
            line = content[line_start : p + 1]
            comment_idx = line.find("//")
            if comment_idx != -1:
                p = line_start + comment_idx - 1
            else:
                break
    return p


def _skip_whitespace_and_comments_forward(content: str, pos: int) -> int:
    p = pos
    n = len(content)
    while p < n:
        if content[p] in " \t\r\n":
            p += 1
        elif content.startswith("//", p):
            nl = content.find("\n", p)
            p = n if nl == -1 else nl + 1
        elif content.startswith("/*", p):
            end_comment = content.find("*/", p + 2)
            p = n if end_comment == -1 else end_comment + 2
        else:
            break
    return p


def _find_attribute_block(content: str, pos: int) -> str | None:
    """Find the full text of contiguous attribute lists `[...]` enclosing `pos`."""
    curr_start = content.rfind("[", 0, pos)
    curr_end = content.find("]", pos)
    if curr_start == -1 or curr_end == -1:
        return None

    block_start = curr_start
    scan_pos = curr_start
    while scan_pos > 0:
        p = _skip_whitespace_and_comments_backwards(content, scan_pos - 1)
        if p >= 0 and content[p] == "]":
            prev_bracket_start = content.rfind("[", 0, p)
            if prev_bracket_start != -1:
                block_start = prev_bracket_start
                scan_pos = prev_bracket_start
                continue
        break

    block_end = curr_end + 1
    scan_pos = curr_end + 1
    while scan_pos < len(content):
        p = _skip_whitespace_and_comments_forward(content, scan_pos)
        if p < len(content) and content[p] == "[":
            next_bracket_end = content.find("]", p)
            if next_bracket_end != -1:
                block_end = next_bracket_end + 1
                scan_pos = next_bracket_end + 1
                continue
        break

    return content[block_start:block_end]


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

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        class_mappings: list[tuple[int, str]] = [
            (cm.start(), cm.group(1).rstrip("/")) for cm in _ASPNET_CLASS_ROUTE_RE.finditer(content)
        ]

        out: list[Contract] = []

        # Action method attribute routing
        for m in _ASPNET_VERB_ATTR_RE.finditer(content):
            block = _find_attribute_block(content, m.start())
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

        return out

