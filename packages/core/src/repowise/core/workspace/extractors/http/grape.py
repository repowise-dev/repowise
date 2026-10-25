"""Grape (Ruby) HTTP provider dialect — ``class API < Grape::API``.

Recognises Grape API routes with ``prefix``, path-based ``version``,
nested ``resource`` / ``namespace`` / ``route_param`` scopes, and verb declarations.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import RUBY
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_PREFIX_RE = re.compile(r"\bprefix\s+(?::(\w+)|\"([^\"]+)\"|'([^']+)')")
_VERSION_RE = re.compile(
    r"\bversion\s+(?:(?::(\w+)|\"([^\"]+)\"|'([^']+)'|\[[^\]]+\]))[^\n]*?using:\s*:path"
)
_RESOURCE_RE = re.compile(
    r"\b(?:resource|resources|namespace|group|segment)\s+(?::(\w+)|\"([^\"]+)\"|'([^']+)')"
)
_ROUTE_PARAM_RE = re.compile(r"\broute_param\s+(?::(\w+)|\"([^\"]+)\"|'([^']+)')")
_VERB_RE = re.compile(
    r"\b(get|post|put|patch|delete|options|head)"
    r"(?:\s+(?:(?::(\w+)|\"([^\"]+)\"|'([^']+)'|(?:\(\s*[\"']([^\"']+)[\"']\s*\)))))?"
    r"\s*(?:do|\{)"
)
_DO_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?\s*$")


class GrapeDialect:
    name = "grape"
    extensions = RUBY
    requires = ("Grape::API",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        mask = ctx._mask()
        code = mask if mask is not None else ctx.content
        lines = code.splitlines()

        prefix = ""
        version = ""
        stack: list[str] = []
        out: list[Contract] = []

        for line_no, mask_line in enumerate(lines, 1):
            line = mask_line.strip()
            if not line or ctx.comment_only_line(line_no):
                continue

            if (
                line == "end"
                or line.startswith("end ")
                or line.endswith(" end")
                or "; end" in line
            ):
                if stack:
                    stack.pop()
                continue

            has_do = bool(_DO_BLOCK_RE.search(line))

            # prefix :api
            m = _PREFIX_RE.search(line)
            if m:
                val = next(g for g in m.groups() if g is not None)
                prefix = "/" + val.strip("/")
                continue

            # version 'v1', using: :path
            m = _VERSION_RE.search(line)
            if m:
                val = next((g for g in m.groups() if g is not None), "")
                if val:
                    version = "/" + val.strip("/")
                continue

            # resource / namespace / group / segment :orders do
            m = _RESOURCE_RE.search(line)
            if m:
                res = next(g for g in m.groups() if g is not None).strip("/")
                if has_do:
                    stack.append(res)
                continue

            # route_param :id do
            m = _ROUTE_PARAM_RE.search(line)
            if m:
                p_name = next(g for g in m.groups() if g is not None).strip("/")
                if has_do:
                    stack.append(":" + p_name)
                continue

            # get do / get :status do / post '/new' do
            m = _VERB_RE.search(line)
            if m:
                verb = m.group(1).upper()
                sub = next((g for g in m.groups()[1:] if g is not None), "")
                path_parts = [p for p in [prefix, version, *stack, sub] if p]
                full = "/" + "/".join(s.strip("/") for s in path_parts)
                c = build_provider_contract(
                    ctx,
                    method=verb,
                    path_raw=full,
                    framework="grape",
                    line=line_no,
                )
                if c is not None:
                    out.append(c)
                if has_do:
                    stack.append("")  # block for route body
                continue

            if has_do:
                stack.append("")

        return out
