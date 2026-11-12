"""One resolved symbol's served body: whole, capped, or outlined."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._budget.contracts import DEFAULT_RESPONSE_CHARS
from repowise.server.mcp_server._helpers import (
    clean_signature,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import completeness_line as _completeness_line
from repowise.server.mcp_server._meta import symbol_hint as _symbol_hint
from repowise.server.mcp_server._references import (
    source_reference,
    symbol_identity,
)
from repowise.server.mcp_server._symbol_facts import symbol_card_facts
from repowise.server.mcp_server._verify import (
    check_symbol_bounds,
    heal_symbol_row,
)

from .text import _MAX_SOURCE_LINES, _elapsed_ms, _number_lines, _read_file_text, _slice_text

# A container is served as its header plus each member's signature, lines and
# summary: the shape of a class is what a lookup of it is for, and a member's
# body is one get_symbol call away. Its body is served whole only when the
# outline would be no smaller.
_CONTAINER_KINDS = frozenset({"class", "interface", "struct", "enum", "trait", "impl", "module"})
# Header lines (decorators, declaration, docstring head) served above members.
_OUTLINE_HEADER_LINES = 12
_OUTLINE_SUMMARY_CHARS = 160
# Serialized chars of member rows listed in full; the members past it are named
# with their first line only, up to _OUTLINE_NAMES_CHARS, and the rest counted
# in members_total.
_OUTLINE_MEMBER_CHARS = DEFAULT_RESPONSE_CHARS // 4
_OUTLINE_NAMES_CHARS = DEFAULT_RESPONSE_CHARS // 12
# Member kinds a row leaves implicit.
_CALLABLE_MEMBER_KINDS = frozenset({"method", "function"})


class _Outline(NamedTuple):
    """A container's member rows, the line its first member starts on, and the total."""

    members: list[dict[str, Any]]
    more: list[str]
    first_line: int
    total: int


async def _container_outline(
    session_factory: Any, repo_id: str, row: WikiSymbol, text: str, start: int, end: int
) -> _Outline | None:
    """Direct members of the container *row* spanning ``start``-``end``, as outline rows.

    Rows are listed in full up to ``_OUTLINE_MEMBER_CHARS``, then by name and
    first line up to ``_OUTLINE_NAMES_CHARS``; None when the index holds no
    members to outline. Each member is verified against the live file, parsing
    it at most once for the whole set.

    Members are matched by parent name inside the container's own lines, so
    Rust impl blocks and Go receiver methods declared outside a struct do not
    outline it.
    """
    async with get_session(session_factory) as session:
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == row.file_path,
                WikiSymbol.parent_name == row.name,
                WikiSymbol.id != row.id,
            )
        )
        rows = list(res.scalars().all())
    _parsed = functools.cache(lambda: _pkg.parse_live_symbols(row, text))
    container_id = symbol_identity(row.symbol_id)
    members: list[dict[str, Any]] = []
    for member in rows:
        check = check_symbol_bounds(member, text, _parsed)
        # A same-named container elsewhere in the file shares the parent name;
        # an unverified member is judged by its indexed range instead.
        if check.approximate:
            if not start <= member.start_line <= member.end_line <= end:
                continue
        elif not start <= check.start_line <= end:
            continue
        members.append(_member_row(member, check, container_id))
    if not members:
        return None
    members.sort(key=lambda m: m["_start"])
    # Overloads share one id, and get_symbol on it already serves every one.
    unique: dict[str, dict[str, Any]] = {}
    for entry in members:
        unique.setdefault(entry["_id"], entry)
    members = list(unique.values())
    first = min((m["_start"] for m in members if "bounds" not in m), default=end + 1)
    listed: list[dict[str, Any]] = []
    more: list[str] = []
    remaining = _OUTLINE_MEMBER_CHARS
    names_left = _OUTLINE_NAMES_CHARS
    for entry in members:
        start_line = entry.pop("_start")
        entry.pop("_id")
        if not more:
            remaining -= len(json.dumps(entry)) + 2
            if remaining >= 0:
                listed.append(entry)
                continue
        named = f"{entry['name']} {start_line}"
        names_left -= len(named) + 4
        if names_left < 0:
            break
        more.append(named)
    return _Outline(listed, more, first, len(members))


def _member_row(member: WikiSymbol, check: Any, container_id: str) -> dict[str, Any]:
    """One outline row: name, signature and lines, with only the fields that say something.

    The id is left out when it is the container's id plus the name, as the
    note says; the kind when it is a method.
    """
    member_id = symbol_identity(member.symbol_id)
    entry: dict[str, Any] = {"name": member.name}
    if member_id != f"{container_id}::{member.name}":
        entry["symbol_id"] = member_id
    if member.kind not in _CALLABLE_MEMBER_KINDS:
        entry["kind"] = member.kind
    entry["signature"] = clean_signature(member.signature)
    entry["lines"] = f"{check.start_line}-{check.end_line}"
    summary = (member.docstring or "").strip().splitlines()
    if summary:
        entry["summary"] = summary[0][:_OUTLINE_SUMMARY_CHARS]
    if check.approximate:
        entry["bounds"] = "approximate"
    entry["_start"] = check.start_line
    entry["_id"] = member_id
    return entry


def _unreadable_source_response(row: Any, symbol_id: str, repository: Any, t0: float) -> dict:
    from repowise.server.mcp_server._savings.interaction import note_error_reason

    note_error_reason("index_behind")
    return {
        "symbol_id": symbol_id,
        "file": row.file_path,
        "name": row.name,
        "kind": row.kind,
        "signature": row.signature,
        "error": (
            "Symbol metadata exists but source file could not be read. "
            "The file may have been moved or deleted since indexing."
        ),
        "_meta": _build_meta(
            timing_ms=_elapsed_ms(t0),
            repository=repository,
        ),
    }


class _ServedBody(NamedTuple):
    """The numbered lines served for one symbol, and the outline if it was outlined."""

    numbered: str
    start: int
    end: int
    truncated: bool
    outline: _Outline | None


def _header_slice(text: str, check: Any, first_member: int, context_lines: int) -> tuple[str, int, int]:
    """The container's numbered header above its first member, and its line span.

    ``context_lines`` widens it upward only; below it are the members.
    """
    header_end = min(first_member - 1, check.start_line + _OUTLINE_HEADER_LINES - 1, check.end_line)
    source, start, _end, _total = _slice_text(
        text,
        max(1, check.start_line - context_lines),
        max(check.start_line, header_end),
        0,
    )
    source = source.rstrip()
    end = start + len(source.splitlines()) - 1 if source else start
    return _number_lines(source, start), start, end


def _outline_size(outline: _Outline, header: str) -> int:
    return len(header) + len(json.dumps(outline.members)) + len(json.dumps(outline.more))


async def _outline_container(
    ctx: Any, repository: Any, row: Any, text: str, check: Any
) -> _Outline | None:
    """Members outline of a verified container, when it is smaller than the body."""
    if row.kind not in _CONTAINER_KINDS or not check.verified:
        return None
    outline = await _container_outline(
        ctx.session_factory, repository.id, row, text, check.start_line, check.end_line
    )
    if outline is None:
        return None
    # Both sized without context_lines, so they never tip a class either way.
    bare, bare_start, _e, _t = _slice_text(text, check.start_line, check.end_line, 0)
    header, _s, _e = _header_slice(text, check, outline.first_line, 0)
    if _outline_size(outline, header) >= len(_number_lines(bare, bare_start)):
        return None
    return outline


async def _served_body(
    ctx: Any, repository: Any, row: Any, text: str, check: Any, context_lines: int
) -> _ServedBody:
    """The body slice to serve: the whole body, a capped head, or a header plus outline."""
    outline = await _outline_container(ctx, repository, row, text, check)
    if outline is not None:
        header, start, end = _header_slice(text, check, outline.first_line, context_lines)
        return _ServedBody(header, start, end, False, outline)

    source, start, end, _total = _slice_text(text, check.start_line, check.end_line, context_lines)
    truncated = (end - start + 1) >= _MAX_SOURCE_LINES and (
        check.end_line - check.start_line + 1 + 2 * context_lines
    ) > _MAX_SOURCE_LINES
    return _ServedBody(_number_lines(source, start), start, end, truncated, None)


def _note_outline(response: dict, outline: _Outline, row: Any, check: Any) -> None:
    """Mark an outlined body and say how to fetch its members or the whole body."""
    members = outline.members
    # An outline is not the body, so it never claims ``_meta.complete``.
    response["outlined"] = True
    response["members"] = members
    shown = len(members) + len(outline.more)
    if outline.more:
        response["more_members"] = ", ".join(outline.more)
    container_id = symbol_identity(row.symbol_id)
    response["note"] = (
        f"Body ({check.start_line}-{check.end_line}) outlined: header plus "
        f"{len(members)} member signatures"
        + (f" and {len(outline.more)} more by name and first line" if outline.more else "")
        + f". Fetch one member with get_symbol('{container_id}::<name>') (or its "
        f"symbol_id where listed), or Read {row.file_path} lines "
        f"{check.start_line}-{check.end_line} for the whole body."
    )
    if outline.total > shown:
        response["members_total"] = outline.total
        last = outline.more[-1].rsplit(" ", 1)[-1] if outline.more else (
            members[-1]["lines"].split("-")[-1] if members else str(check.start_line)
        )
        response["note"] += (
            f" {outline.total - shown} more members are not listed;"
            f" they start after line {last}."
        )


def _note_continuation(
    response: dict, body: _ServedBody, row: Any, check: Any, alias: Any
) -> None:
    """Hand back the exact range read that fetches the rest of a capped body."""
    continuation_reference = source_reference(
        alias,
        row.file_path,
        lines=[body.end + 1, check.end_line],
        verification_basis="live",
        source_kind="source",
    )
    response["continuation"] = continuation_reference["id"]
    response["continuation_reference"] = continuation_reference
    response["note"] = (
        f"Symbol body ({check.start_line}-{check.end_line}) exceeds the "
        f"{_MAX_SOURCE_LINES}-line serve cap; served {body.start}-{body.end}. Fetch "
        f"the remainder in one call: get_symbol({response['continuation']!r})."
    )


def _symbol_body_response(
    row: Any,
    check: Any,
    body: _ServedBody,
    repository: Any,
    alias: Any,
    t0: float,
    card_facts: dict[str, Any] | None = None,
) -> dict:
    """The served body with its fact row, test links, outline, continuation and bounds notes."""
    response = {
        "symbol_id": symbol_identity(row.symbol_id),
        "file": row.file_path,
        "name": row.name,
        "kind": row.kind,
        "qualified_name": row.qualified_name,
        "signature": row.signature,
        **(card_facts or {}),
        "start_line": body.start,
        "end_line": body.end,
        "source": body.numbered,
        "truncated": body.truncated,
        "verified": check.verified,
        "_meta": _build_meta(
            timing_ms=_elapsed_ms(t0),
            hint=_symbol_hint(row.symbol_id, check.end_line, check.start_line),
            repository=repository,
            # Served bytes are live-verified; only this file's drift matters.
            targets=[row.file_path],
        ),
    }
    if (body.start, body.end) != (check.start_line, check.end_line):
        # The whole symbol's bounds, when the served slice is not all of it.
        response["symbol_start_line"] = check.start_line
        response["symbol_end_line"] = check.end_line
    if body.outline is not None:
        _note_outline(response, body.outline, row, check)
    elif not body.truncated and check.verified:
        # The whole body was served against live bytes, so nothing is left to
        # fetch for this symbol.
        response["_meta"]["complete"] = _completeness_line(bodies=1)
    if body.truncated and not check.approximate and body.end < check.end_line:
        _note_continuation(response, body, row, check, alias)
    if check.approximate:
        response["bounds"] = "approximate"
        response["note"] = (
            "The live file no longer contains this symbol at its indexed "
            "location and it could not be re-located by name — it may have "
            "been renamed or removed since indexing. The served slice is "
            "the indexed line range from the current file contents; verify "
            "before citing."
        )
    return response


async def _serve_symbol(
    ctx: Any,
    repository: Any,
    row: Any,
    symbol_id: str,
    context_lines: int,
    depth: int,
    exclude_spec: Any,
    t0: float,
) -> dict:
    """Live-verified body of one resolved symbol, with callee bodies past depth 1."""
    repo_root = Path(str(ctx.path))
    text = _read_file_text(repo_root, row.file_path)
    if text is None:
        return _unreadable_source_response(row, symbol_id, repository, t0)

    from repowise.server.mcp_server._savings.reads import read_tokens

    full_tokens = read_tokens(text)

    # Stored bounds are checked against the live file first: stale ones are
    # corrected and healed, unfindable ones served as approximate.
    check = check_symbol_bounds(row, text)
    if check.corrected:
        await heal_symbol_row(ctx.session_factory, row, check.start_line, check.end_line)

    body = await _served_body(ctx, repository, row, text, check, context_lines)
    async with get_session(ctx.session_factory) as session:
        card_facts = await symbol_card_facts(session, repository.id, row.symbol_id)
    response = _symbol_body_response(row, check, body, repository, ctx.alias, t0, card_facts)
    # An outline answers for the shape; callee bodies would undo its size.
    if depth > 1 and body.outline is None:
        async with get_session(ctx.session_factory) as session:
            callee_block = await _pkg._expand_callees(
                session,
                repository.id,
                row,
                repo_root,
                depth,
                exclude_spec,
                ctx.alias,
            )
        if callee_block is not None:
            response["callee_bodies"] = callee_block

    # Serving one symbol replaced reading the whole file.
    from repowise.server.mcp_server._savings import declare_replaced

    declare_replaced(response, full_tokens)
    return response
