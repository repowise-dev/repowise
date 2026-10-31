"""Slim (PHP) HTTP provider dialect — ``$app->get(...)`` and ``$app->group(...)``.

Recognises route registrations and group blocks in Slim framework applications.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import line_at
from ..langs import PHP
from ..strings import match_paren
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_SLIM_GROUP_RE = re.compile(r"\$[A-Za-z_]\w*\s*->\s*group\s*\(")
_SLIM_ROUTE_RE = re.compile(
    r"\$[A-Za-z_]\w*\s*->\s*(?P<verb>get|post|put|patch|delete|options)\s*\("
)
_PATH_LITERAL_RE = re.compile(r"[\"']([^\"']+)[\"']")


def _collect_groups(content: str) -> list[tuple[int, int, str]]:
    groups: list[tuple[int, int, str]] = []
    for m in _SLIM_GROUP_RE.finditer(content):
        open_paren = m.end() - 1
        close_paren = match_paren(content, open_paren)
        if close_paren < 0:
            continue
        args_text = content[open_paren + 1 : close_paren]
        pm = _PATH_LITERAL_RE.search(args_text)
        if pm:
            groups.append((open_paren, close_paren, pm.group(1)))
    return groups


def _resolve_route_path(
    content: str,
    m: re.Match[str],
    groups: list[tuple[int, int, str]],
) -> str | None:
    open_paren = m.end() - 1
    close_paren = match_paren(content, open_paren)
    if close_paren < 0:
        return None
    args_text = content[open_paren + 1 : close_paren]
    pm = _PATH_LITERAL_RE.search(args_text)
    if not pm:
        return None
    route_path = pm.group(1)

    enclosing = [g for g in groups if g[0] < m.start() < g[1]]
    full_path = ""
    for g in enclosing:
        full_path = compose_prefix(full_path, g[2])
    return compose_prefix(full_path, route_path)


class SlimDialect:
    name = "slim"
    extensions = PHP
    requires = ("Slim\\",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        groups = _collect_groups(content)
        out: list[Contract] = []

        for m in _SLIM_ROUTE_RE.finditer(content):
            full_path = _resolve_route_path(content, m, groups)
            if full_path is None:
                continue
            verb = m.group("verb").upper()
            c = build_provider_contract(
                ctx,
                method=verb,
                path_raw=full_path,
                framework="slim",
                line=line_at(content, m.start()),
            )
            if c is not None:
                out.append(c)

        return out
