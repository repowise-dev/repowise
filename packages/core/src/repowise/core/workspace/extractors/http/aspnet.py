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

_CLASS_DECL_RE = re.compile(
    r"""(?:(?:public|private|protected|internal|abstract|sealed|static|partial)\s+)*class\s+\w+""",
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


def _scan_attribute_spans(content: str) -> list[tuple[int, int]]:
    """Scan `content` for all top-level attribute list `[...]` character spans.

    Respects string literals, verbatim strings, character literals, and comments.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        # Line comment
        if c == "/" and i + 1 < n and content[i + 1] == "/":
            nl = content.find("\n", i + 2)
            i = n if nl == -1 else nl + 1
            continue
        # Block comment
        if c == "/" and i + 1 < n and content[i + 1] == "*":
            end = content.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # Verbatim string @"..." or $@"..."" or @$""
        if (c == "@" and i + 1 < n and content[i + 1] == '"') or (
            c == "$" and i + 2 < n and content[i + 1] == "@" and content[i + 2] == '"'
        ) or (
            c == "@" and i + 2 < n and content[i + 1] == "$" and content[i + 2] == '"'
        ):
            i = content.find('"', i) + 1
            while i < n:
                if content[i] == '"':
                    if i + 1 < n and content[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        # Regular or interpolated string "..."
        if c == '"' or (c == "$" and i + 1 < n and content[i + 1] == '"'):
            i = content.find('"', i) + 1
            while i < n:
                if content[i] == "\\":
                    i += 2
                    continue
                if content[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        # Character literal '...'
        if c == "'":
            i += 1
            while i < n:
                if content[i] == "\\":
                    i += 2
                    continue
                if content[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        # Opening bracket
        if c == "[":
            start = i
            i += 1
            depth = 1
            while i < n and depth > 0:
                ch = content[i]
                if ch == "/" and i + 1 < n and content[i + 1] == "/":
                    nl = content.find("\n", i + 2)
                    i = n if nl == -1 else nl + 1
                    continue
                if ch == "/" and i + 1 < n and content[i + 1] == "*":
                    end = content.find("*/", i + 2)
                    i = n if end == -1 else end + 2
                    continue
                if (ch == "@" and i + 1 < n and content[i + 1] == '"') or (
                    ch == "$" and i + 2 < n and content[i + 1] == "@" and content[i + 2] == '"'
                ) or (
                    ch == "@" and i + 2 < n and content[i + 1] == "$" and content[i + 2] == '"'
                ):
                    i = content.find('"', i) + 1
                    while i < n:
                        if content[i] == '"':
                            if i + 1 < n and content[i + 1] == '"':
                                i += 2
                                continue
                            i += 1
                            break
                        i += 1
                    continue
                if ch == '"' or (ch == "$" and i + 1 < n and content[i + 1] == '"'):
                    i = content.find('"', i) + 1
                    while i < n:
                        if content[i] == "\\":
                            i += 2
                            continue
                        if content[i] == '"':
                            i += 1
                            break
                        i += 1
                    continue
                if ch == "'":
                    i += 1
                    while i < n:
                        if content[i] == "\\":
                            i += 2
                            continue
                        if content[i] == "'":
                            i += 1
                            break
                        i += 1
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        spans.append((start, i))
                        break
                i += 1
            continue
        i += 1
    return spans


def _is_whitespace_or_comments(text: str) -> bool:
    i = 0
    n = len(text)
    while i < n:
        if text[i] in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i):
            nl = text.find("\n", i + 2)
            i = n if nl == -1 else nl + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                return True
            i = end + 2
            continue
        return False
    return True


def _find_class_mappings(content: str, spans: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Extract (class_start_pos, route_prefix) for classes preceded by Route/RoutePrefix."""
    if not spans:
        return []
    blocks: list[tuple[int, int]] = []
    curr_start, curr_end = spans[0]
    for start, end in spans[1:]:
        if _is_whitespace_or_comments(content[curr_end:start]):
            curr_end = end
        else:
            blocks.append((curr_start, curr_end))
            curr_start, curr_end = start, end
    blocks.append((curr_start, curr_end))

    mappings: list[tuple[int, str]] = []
    for b_start, b_end in blocks:
        block_text = content[b_start:b_end]
        route_match = _ROUTE_PREFIX_RE.search(block_text)
        if not route_match:
            continue
        rest = content[b_end:]
        p = 0
        n = len(rest)
        while p < n:
            if rest[p] in " \t\r\n":
                p += 1
            elif rest.startswith("//", p):
                nl = rest.find("\n", p)
                p = n if nl == -1 else nl + 1
            elif rest.startswith("/*", p):
                end_c = rest.find("*/", p + 2)
                p = n if end_c == -1 else end_c + 2
            else:
                break
        after_block = rest[p:]
        m_cls = _CLASS_DECL_RE.match(after_block)
        if m_cls:
            cls_pos = b_end + p + m_cls.start()
            mappings.append((cls_pos, route_match.group(1).rstrip("/")))
    return mappings


def _find_attribute_block(
    content: str, pos: int, spans: list[tuple[int, int]] | None = None
) -> str | None:
    """Find the full text of contiguous attribute lists `[...]` enclosing `pos`."""
    if spans is None:
        spans = _scan_attribute_spans(content)
    target_idx = None
    for idx, (s, e) in enumerate(spans):
        if s <= pos < e:
            target_idx = idx
            break
    if target_idx is None:
        return None
    start_idx = target_idx
    while start_idx > 0 and _is_whitespace_or_comments(
        content[spans[start_idx - 1][1] : spans[start_idx][0]]
    ):
        start_idx -= 1
    end_idx = target_idx
    while end_idx + 1 < len(spans) and _is_whitespace_or_comments(
        content[spans[end_idx][1] : spans[end_idx + 1][0]]
    ):
        end_idx += 1
    return content[spans[start_idx][0] : spans[end_idx][1]]


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
        spans = _scan_attribute_spans(content)
        class_mappings = _find_class_mappings(content, spans)

        out: list[Contract] = []

        # Action method attribute routing
        for m in _ASPNET_VERB_ATTR_RE.finditer(content):
            block = _find_attribute_block(content, m.start(), spans)
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

