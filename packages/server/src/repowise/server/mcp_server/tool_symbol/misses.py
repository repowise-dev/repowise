"""Index misses: live-file grep lines, retry suggestions or not-found."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._meta import build_meta as _build_meta

from .lookup import _bare_name, _parse_symbol_id, _symbol_suggestions
from .text import _elapsed_ms, _number_lines, _read_file_text

# Dead-end recovery: max grep matches returned when a symbol lookup misses
# but the file exists on disk.
_MAX_FALLBACK_MATCHES = 8

# A matched line that declares the name rather than using it: a definition
# keyword at line start, after decorators and modifiers. Keyword-based, so a
# definition without one (``const f = () =>``) reads as a plain match.
_DEF_KEYWORDS = r"(?:def|class|function\*?|func|fn|struct|interface|enum|trait|type|module|sub)"
_DEF_MODIFIERS = (
    r"(?:(?:export|default|pub(?:\([^)]*\))?|public|private|protected|internal|static"
    r"|abstract|final|override|virtual|async|unsafe|extern|inline|open|sealed|partial|data)\s+)*"
)
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--", ";")

# Symbol kinds whose bodies hold fields the index does not list as symbols.
_OWNER_KINDS = ("class", "struct", "interface", "trait", "enum", "impl")
_CLOSERS = ("}", ")", "]")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _member_indent(lines: list[str], start: int, end: int) -> int | None:
    """Indent of the direct members of the owner spanning *start*..*end* (1-based).

    Lines no deeper than the owner's own line (a wrapped header's ``> {``)
    are not members.
    """
    own = _indent(lines[start - 1])
    body = (_indent(ln) for ln in lines[start:end] if ln.strip())
    return min((n for n in body if n > own), default=None)


def _statement_end(lines: list[str], i: int) -> int:
    """Last line (1-based) of the statement opening on line *i*, by indentation."""
    base = _indent(lines[i - 1])
    end = i
    for j in range(i + 1, len(lines) + 1):
        line = lines[j - 1]
        if not line.strip() or _indent(line) > base:
            end = j if line.strip() else end
            continue
        if _indent(line) == base and line.strip().startswith(_CLOSERS):
            end = j
        break
    return end


def _member_lines(
    lines: list[str], file_path: str, name: str, owners: list[tuple[str, int, int]]
) -> list[dict]:
    """Lines declaring or assigning field *name* of an owner in *owners*.

    A field (``use: Handler`` with ``this.use = ...`` in the constructor,
    ``self.limit = ...``, a Go struct field) is not an indexed symbol. A
    declaration counts only at the owner's member indent, so a parameter
    of the same name is not taken for it. ``Owner::name`` keeps that
    owner's lines.
    """
    bare = _bare_name(name)
    wanted = _owner_qualifier(name)
    decl = re.compile(
        rf"^\s*{_DEF_MODIFIERS}(?:readonly\s+)?#?{re.escape(bare)}"
        rf"(?:[!?]?\s*(?::(?!:)|=(?![=>])|;)|(?:\s*,\s*\w+)*\s+[\w*\[(])"
    )
    assign = re.compile(rf"\b(?:this|self)\.#?{re.escape(bare)}\s*=(?![=>])")
    out: list[dict] = []
    for i, line in enumerate(lines, 1):
        owner = _owner_at(owners, i, wanted) if _code_naming(line, bare) else None
        if owner and (assign.search(line) or _declares(lines, i, decl, owner)):
            out.append(_member_match(lines, file_path, i, owner[0]))
            if len(out) >= _MAX_FALLBACK_MATCHES:
                break
    return out


def _owner_qualifier(name: str) -> str | None:
    """``Owner`` in ``Owner::name`` / ``Owner.name``, else None."""
    segments = [s for s in re.split(r"::|\.|/", name) if s]
    return segments[-2] if len(segments) > 1 else None


def _defined_outside(lines: list[str], bare: str, owners: list[tuple[str, int, int]]) -> bool:
    """A top-level line outside every owner defines *bare* (``LIMIT = 3``, ``def f``)."""
    top = re.compile(
        rf"^{_DEF_MODIFIERS}(?:(?:const|let|var|{_DEF_KEYWORDS})\s+(?:\([^)]*\)\s*)?"
        rf"{re.escape(bare)}\b|{re.escape(bare)}\s*(?::(?!:)|=(?!=)))"
    )
    return any(
        top.match(line) and not any(s <= i <= e for _, s, e in owners)
        for i, line in enumerate(lines, 1)
    )


def _code_naming(line: str, bare: str) -> bool:
    return bare in line and not line.lstrip().startswith(_COMMENT_PREFIXES)


def _declares(lines: list[str], i: int, decl: re.Pattern, owner: tuple[str, int, int]) -> bool:
    """Line *i* declares the field at *owner*'s member indent."""
    line = lines[i - 1]
    return bool(decl.match(line)) and _indent(line) == _member_indent(lines, *owner[1:])


def _owner_at(
    owners: list[tuple[str, int, int]], i: int, wanted: str | None
) -> tuple[str, int, int] | None:
    """The innermost owner whose body holds line *i*, when it is *wanted* (any if None)."""
    spans = [o for o in owners if o[1] < i <= o[2]]
    owner = min(spans, key=lambda o: o[2] - o[1]) if spans else None
    return owner if owner and wanted in (None, owner[0]) else None


def _member_match(lines: list[str], file_path: str, i: int, owner: str) -> dict:
    lo, hi = max(1, i - 2), min(len(lines), i + 2)
    match = {
        "line": i,
        "context": _number_lines("\n".join(lines[lo - 1 : hi]), lo),
        "member_of": owner,
    }
    stop = _statement_end(lines, i)
    if stop > i:
        match["read"] = f"{file_path}:{i}-{stop}"
    return match


def _live_grep_fallback(repo_root: Path, file_path: str, name: str) -> list[dict]:
    """Find ``name`` in the live file when the symbol index misses.

    Turns a dead-end call (pure cost) into an answer: constants, imports,
    aliases, and decorators live between indexed symbols, and the line that
    defines them is usually all the agent needs. ±2 lines of context per
    match, capped at _MAX_FALLBACK_MATCHES.
    """
    text = _read_file_text(repo_root, file_path)
    if text is None:
        return []
    bare = _bare_name(name)
    if not bare:
        return []
    defines = re.compile(
        rf"^\s*(?:@\S+\s+)*{_DEF_MODIFIERS}{_DEF_KEYWORDS}\s+(?:\([^)]*\)\s*)?{re.escape(bare)}\b"
    )
    lines = text.splitlines()
    matches: list[dict] = []
    for i, line in enumerate(lines, 1):
        if bare in line:
            lo, hi = max(1, i - 2), min(len(lines), i + 2)
            match = {"line": i, "context": _number_lines("\n".join(lines[lo - 1 : hi]), lo)}
            if not line.lstrip().startswith(_COMMENT_PREFIXES) and defines.match(line):
                match["defines"] = True
            matches.append(match)
            if len(matches) >= _MAX_FALLBACK_MATCHES:
                break
    return matches


def _member_note(file_part: str, name: str, matches: list[dict]) -> str:
    owners = list(dict.fromkeys(m["member_of"] for m in matches))
    note = f"Not an indexed symbol: a field of {', '.join(owners)}, declared or assigned on these lines."
    if len(owners) > 1:
        note += f' Pass "{file_part}::{owners[0]}::{_bare_name(name)}" to keep one class.'
    if any("read" in m for m in matches):
        note += ' A line with "read" opens a multi-line value; pass that id back for its source.'
    return note


def _grep_matches(
    repo_root: Path, file_part: str, name: str, owners: list[tuple[str, int, int]]
) -> tuple[list[dict], str]:
    """Live lines for an index miss and the note that explains them."""
    text = _read_file_text(repo_root, file_part) if owners else None
    lines = text.splitlines() if text else []
    # An unqualified name the module itself defines keeps the plain grep.
    if lines and (_owner_qualifier(name) or not _defined_outside(lines, _bare_name(name), owners)):
        members = _member_lines(lines, file_part, name, owners)
        if members:
            return members, _member_note(file_part, name, members)
    matches = _live_grep_fallback(repo_root, file_part, name)
    likely = (
        "defined here, so likely added after the last index; "
        "run `repowise update` to index it"
        if any(m.get("defines") for m in matches)
        else "likely a constant, import, or alias"
    )
    return matches, (
        "Not an indexed symbol, but the name matches these "
        f"live-file lines ({likely}). For surrounding source "
        f'use a range read: "{file_part}:<start>-<end>".'
    )


async def _file_owners(ctx: Any, repo_id: str, file_path: str) -> list[tuple[str, int, int]]:
    """``(name, start_line, end_line)`` of the classes, structs and the like in *file_path*."""
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(WikiSymbol.name, WikiSymbol.start_line, WikiSymbol.end_line).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
                WikiSymbol.kind.in_(_OWNER_KINDS),
            )
        )
        return [(n, s, e) for n, s, e in res.all() if n and s and e]


def _live_grep_response(
    ctx: Any,
    repository: Any,
    symbol_id: str,
    exclude_spec: Any,
    t0: float,
    owners: list[tuple[str, int, int]] | None = None,
) -> dict | None:
    """Live-file lines naming a symbol the index lacks (a field, constant, import or alias), else None."""
    file_part, name_part = _parse_symbol_id(symbol_id)
    if not file_part or not name_part or _pkg.is_excluded(file_part, exclude_spec):
        return None
    matches, note = _grep_matches(Path(str(ctx.path)), file_part, name_part, owners or [])
    if not matches:
        return None
    return {
        "symbol_id": symbol_id,
        "file": file_part,
        "resolution": "live_grep",
        "fallback_lines": matches,
        "verified": True,
        "note": note,
        "_meta": _build_meta(
            timing_ms=_elapsed_ms(t0),
            repository=repository,
            targets=[file_part],
        ),
    }


def _bare_file_path(ctx: Any, symbol_id: str, exclude_spec: Any) -> str | None:
    """*symbol_id* as a repo file path when it names a live file and no symbol in it."""
    path = symbol_id.strip().replace("\\", "/").strip("/")
    if not ctx.path or "::" in path or _pkg.is_excluded(path, exclude_spec):
        return None
    root = Path(str(ctx.path)).resolve()
    target = (root / path).resolve()
    # Only a file inside the checkout: the reply must not confirm one outside it.
    return path if target.is_relative_to(root) and target.is_file() else None


async def _not_found_response(
    ctx: Any, repository: Any, symbol_id: str, exclude_spec: Any, t0: float
) -> dict:
    """Live lines, exact-id suggestions, or a not-found error for an index miss."""
    if ctx.path:
        file_part, name_part = _parse_symbol_id(symbol_id)
        owners = (
            await _file_owners(ctx, repository.id, file_part) if file_part and name_part else []
        )
        live = _live_grep_response(ctx, repository, symbol_id, exclude_spec, t0, owners)
        if live is not None:
            return live
    from repowise.server.mcp_server._savings.interaction import note_error_reason

    # Every reply below is a miss.
    note_error_reason("not_found")
    if file_path := _bare_file_path(ctx, symbol_id, exclude_spec):
        return {
            "symbol_id": symbol_id,
            "error": (
                f"'{file_path}' is a file, not a symbol. Whole file: "
                f'get_context(targets=["{file_path}"], include=["skeleton"]). '
                f'Lines: get_symbol("{file_path}:1-120").'
            ),
            "_meta": _build_meta(timing_ms=_elapsed_ms(t0), repository=repository),
        }
    async with get_session(ctx.session_factory) as session:
        suggestions = await _symbol_suggestions(session, repository.id, symbol_id, exclude_spec)
    if suggestions:
        return {
            "symbol_id": symbol_id,
            "error": (
                f"Symbol not found: {symbol_id!r}. A symbol with this name "
                "exists at the path(s) below — retry with one of these "
                "exact symbol_ids."
            ),
            "suggestions": suggestions,
            "_meta": _build_meta(
                timing_ms=_elapsed_ms(t0),
                repository=repository,
            ),
        }
    return {
        "symbol_id": symbol_id,
        "error": (
            f"Symbol not found: {symbol_id!r}. Use get_context to list "
            "available symbols in the file, then try again with the "
            "exact symbol_id from that response."
        ),
        "_meta": _build_meta(
            timing_ms=_elapsed_ms(t0),
            repository=repository,
        ),
    }
