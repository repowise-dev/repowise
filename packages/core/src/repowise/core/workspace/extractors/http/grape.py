"""Grape (Ruby) HTTP provider dialect — ``class API < Grape::API``.

Recognises Grape API routes with ``prefix``, path-based ``version``,
nested ``resource`` / ``namespace`` / ``route_param`` scopes, and verb declarations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    r"(?:\s*(?:do|\{)|$)"
)
_DO_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?\s*$")


@dataclass
class _GrapeState:
    prefix: str = ""
    version: str = ""
    stack: list[str] = field(default_factory=list)
    out: list[Contract] = field(default_factory=list)


def _strip_inline_end(raw: str) -> tuple[str, bool]:
    clean = raw
    has_inline_end = False
    if "; end" in clean:
        clean = clean.split("; end")[0].strip()
        has_inline_end = True
    elif re.search(r"\s+end$", clean):
        clean = re.sub(r"\s+end$", "", clean).strip()
        has_inline_end = True
    return clean, has_inline_end


def _handle_config(line: str, state: _GrapeState) -> bool:
    m = _PREFIX_RE.search(line)
    if m:
        val = next(g for g in m.groups() if g is not None)
        state.prefix = "/" + val.strip("/")
        return True

    m = _VERSION_RE.search(line)
    if m:
        val = next((g for g in m.groups() if g is not None), "")
        if val:
            state.version = "/" + val.strip("/")
        return True

    return False


def _handle_scope(line: str, push_block: bool, state: _GrapeState) -> bool:
    m = _RESOURCE_RE.search(line)
    if m:
        res = next(g for g in m.groups() if g is not None).strip("/")
        if push_block:
            state.stack.append(res)
        return True

    m = _ROUTE_PARAM_RE.search(line)
    if m:
        p_name = next(g for g in m.groups() if g is not None).strip("/")
        if push_block:
            state.stack.append(":" + p_name)
        return True

    return False


def _handle_verb(
    line: str,
    line_no: int,
    push_block: bool,
    state: _GrapeState,
    ctx: ScanContext,
) -> bool:
    m = _VERB_RE.search(line)
    if not m:
        return False

    verb = m.group(1).upper()
    sub = next((g for g in m.groups()[1:] if g is not None), "")
    path_parts = [p for p in [state.prefix, state.version, *state.stack, sub] if p]
    full = "/" + "/".join(s.strip("/") for s in path_parts)

    c = build_provider_contract(
        ctx,
        method=verb,
        path_raw=full,
        framework="grape",
        line=line_no,
    )
    if c is not None:
        state.out.append(c)
    if push_block:
        state.stack.append("")
    return True


def _process_statement(
    line: str,
    line_no: int,
    push_block: bool,
    state: _GrapeState,
    ctx: ScanContext,
) -> bool:
    if _handle_config(line, state):
        return True

    if _handle_scope(line, push_block, state):
        return True

    return _handle_verb(line, line_no, push_block, state, ctx)


class GrapeDialect:
    name = "grape"
    extensions = RUBY
    requires = ("Grape::API",)

    def _step_line(
        self,
        raw: str,
        line_no: int,
        state: _GrapeState,
        ctx: ScanContext,
    ) -> None:
        if raw == "end" or raw.startswith("end "):
            if state.stack:
                state.stack.pop()
            return

        line, has_inline_end = _strip_inline_end(raw)
        if not line:
            if has_inline_end and state.stack:
                state.stack.pop()
            return

        had_do = bool(_DO_BLOCK_RE.search(line))
        push_block = had_do and not has_inline_end
        pop_enclosing = has_inline_end and not had_do

        handled = _process_statement(line, line_no, push_block, state, ctx)
        if not handled and push_block:
            state.stack.append("")

        if pop_enclosing and state.stack:
            state.stack.pop()

    def extract(self, ctx: ScanContext) -> list[Contract]:
        mask = ctx._mask()
        code = mask if mask is not None else ctx.content
        lines = code.splitlines()

        state = _GrapeState()

        for line_no, mask_line in enumerate(lines, 1):
            raw = mask_line.strip()
            if not raw or ctx.comment_only_line(line_no):
                continue
            self._step_line(raw, line_no, state, ctx)

        return state.out
