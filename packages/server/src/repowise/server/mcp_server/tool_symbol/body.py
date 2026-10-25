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
from repowise.server.mcp_server._verify import (
    check_symbol_bounds,
    heal_symbol_row,
)

from .text import _MAX_SOURCE_LINES, _elapsed_ms, _number_lines, _read_file_text, _slice_text

# A container whose served body would exceed this is outlined instead: its
# header plus each member's signature, lines and id. Half the default reply
# budget, so smaller classes keep the one-call whole body, while a class big
# enough to crowd out the rest of the reply is usually wanted for its shape.
_OUTLINE_MIN_CHARS = DEFAULT_RESPONSE_CHARS // 2
_CONTAINER_KINDS = frozenset({"class", "interface", "struct", "enum", "trait", "impl", "module"})
# Header lines (decorators, declaration, docstring head) served above members.
_OUTLINE_HEADER_LINES = 12
_OUTLINE_SUMMARY_CHARS = 160
# Serialized chars of member rows listed; the rest are counted in members_total.
_OUTLINE_MEMBER_CHARS = DEFAULT_RESPONSE_CHARS // 2


async def _container_outline(
    session_factory: Any, repo_id: str, row: WikiSymbol, text: str, start: int, end: int
) -> tuple[list[dict[str, Any]], int, int] | None:
    """Direct members of the container *row* spanning ``start``-``end``, as outline rows.

    Returns the listed rows (capped at ``_OUTLINE_MEMBER_CHARS``), the line the
    first member starts on and the member total, or None when the index holds
    no members to outline. Each member is verified against the live file,
    parsing it at most once for the whole set.

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
        entry: dict[str, Any] = {
            "symbol_id": symbol_identity(member.symbol_id),
            "kind": member.kind,
            "signature": clean_signature(member.signature),
            "start_line": check.start_line,
            "end_line": check.end_line,
        }
        summary = (member.docstring or "").strip().splitlines()
        if summary:
            entry["summary"] = summary[0][:_OUTLINE_SUMMARY_CHARS]
        if check.approximate:
            entry["bounds"] = "approximate"
        members.append(entry)
    if not members:
        return None
    members.sort(key=lambda m: m["start_line"])
    # Overloads share one id, and get_symbol on it already serves every one.
    unique: dict[str, dict[str, Any]] = {}
    for entry in members:
        unique.setdefault(entry["symbol_id"], entry)
    members = list(unique.values())
    first = min((m["start_line"] for m in members if "bounds" not in m), default=end + 1)
    listed: list[dict[str, Any]] = []
    remaining = _OUTLINE_MEMBER_CHARS
    for entry in members:
        remaining -= len(json.dumps(entry)) + 2
        if remaining < 0:
            break
        listed.append(entry)
    return listed, first, len(members)


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
    outline: tuple[list[dict], int, int] | None


async def _outline_if_large(
    ctx: Any, repository: Any, row: Any, text: str, check: Any
) -> tuple[list[dict], int, int] | None:
    """Members outline of a verified container body too large to serve whole."""
    if row.kind not in _CONTAINER_KINDS or not check.verified:
        return None
    # Sized on the body alone, so context_lines never tips a class into an outline.
    bare, bare_start, _e, _t = _slice_text(text, check.start_line, check.end_line, 0)
    if len(_number_lines(bare, bare_start)) <= _pkg._OUTLINE_MIN_CHARS:
        return None
    return await _container_outline(
        ctx.session_factory, repository.id, row, text, check.start_line, check.end_line
    )


async def _served_body(
    ctx: Any, repository: Any, row: Any, text: str, check: Any, context_lines: int
) -> _ServedBody:
    """The body slice to serve: the whole body, a capped head, or a header plus outline."""
    source, start, end, _total = _slice_text(text, check.start_line, check.end_line, context_lines)
    numbered = _number_lines(source, start)

    truncated = (end - start + 1) >= _MAX_SOURCE_LINES and (
        check.end_line - check.start_line + 1 + 2 * context_lines
    ) > _MAX_SOURCE_LINES

    outline = await _outline_if_large(ctx, repository, row, text, check)
    if outline is None:
        return _ServedBody(numbered, start, end, truncated, None)
    _members, first_member, _members_total = outline
    header_end = min(first_member - 1, check.start_line + _OUTLINE_HEADER_LINES - 1, check.end_line)
    # context_lines widens the header upward only; below it are the members.
    source, start, end, _total = _slice_text(
        text,
        max(1, check.start_line - context_lines),
        max(check.start_line, header_end),
        0,
    )
    source = source.rstrip()
    end = start + len(source.splitlines()) - 1 if source else start
    return _ServedBody(_number_lines(source, start), start, end, False, outline)


def _note_outline(
    response: dict, outline: tuple[list[dict], int, int], row: Any, check: Any
) -> None:
    """Mark an outlined body and say how to fetch its members or the whole body."""
    members, _first_member, members_total = outline
    # An outline is not the body, so it never claims ``_meta.complete``.
    response["outlined"] = True
    response["members"] = members
    response["note"] = (
        f"Body ({check.start_line}-{check.end_line}) outlined: header plus "
        f"{len(members)} members. Fetch one member with get_symbol on its "
        f"symbol_id, or Read {row.file_path} lines "
        f"{check.start_line}-{check.end_line} for the whole body."
    )
    if members_total > len(members):
        response["members_total"] = members_total
        last = members[-1]["end_line"] if members else check.start_line
        response["note"] += (
            f" {members_total - len(members)} more members are not listed;"
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
    row: Any, check: Any, body: _ServedBody, repository: Any, alias: Any, t0: float
) -> dict:
    """The served body with its outline, continuation and bounds notes."""
    response = {
        "symbol_id": symbol_identity(row.symbol_id),
        "file": row.file_path,
        "name": row.name,
        "kind": row.kind,
        "qualified_name": row.qualified_name,
        "signature": row.signature,
        "language": row.language,
        "start_line": body.start,
        "end_line": body.end,
        "symbol_start_line": check.start_line,
        "symbol_end_line": check.end_line,
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

    from repowise.core.distill.budget import estimate_tokens

    full_tokens = estimate_tokens(text)

    # Stored bounds are checked against the live file first: stale ones are
    # corrected and healed, unfindable ones served as approximate.
    check = check_symbol_bounds(row, text)
    if check.corrected:
        await heal_symbol_row(ctx.session_factory, row, check.start_line, check.end_line)

    body = await _served_body(ctx, repository, row, text, check, context_lines)
    response = _symbol_body_response(row, check, body, repository, ctx.alias, t0)
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
