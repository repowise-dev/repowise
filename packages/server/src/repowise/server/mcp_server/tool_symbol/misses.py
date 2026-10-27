"""Index misses: live-file grep lines, retry suggestions or not-found."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from repowise.core.persistence.database import get_session

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


def _live_grep_response(
    ctx: Any, repository: Any, symbol_id: str, exclude_spec: Any, t0: float
) -> dict | None:
    """Live-file lines naming a symbol the index lacks (a constant, import or alias), else None."""
    file_part, name_part = _parse_symbol_id(symbol_id)
    if not file_part or not name_part or _pkg.is_excluded(file_part, exclude_spec):
        return None
    matches = _live_grep_fallback(Path(str(ctx.path)), file_part, name_part)
    if not matches:
        return None
    likely = (
        "defined here, so likely added after the last index; "
        "run `repowise update` to index it"
        if any(m.get("defines") for m in matches)
        else "likely a constant, import, or alias"
    )
    return {
        "symbol_id": symbol_id,
        "file": file_part,
        "resolution": "live_grep",
        "fallback_lines": matches,
        "verified": True,
        "note": (
            "Not an indexed symbol, but the name matches these "
            f"live-file lines ({likely}). For surrounding source "
            f'use a range read: "{file_part}:<start>-<end>".'
        ),
        "_meta": _build_meta(
            timing_ms=_elapsed_ms(t0),
            repository=repository,
            targets=[file_part],
        ),
    }


async def _not_found_response(
    ctx: Any, repository: Any, symbol_id: str, exclude_spec: Any, t0: float
) -> dict:
    """Live lines, exact-id suggestions, or a not-found error for an index miss."""
    if ctx.path:
        live = _live_grep_response(ctx, repository, symbol_id, exclude_spec, t0)
        if live is not None:
            return live
    async with get_session(ctx.session_factory) as session:
        suggestions = await _symbol_suggestions(session, repository.id, symbol_id, exclude_spec)
    from repowise.server.mcp_server._savings.interaction import note_error_reason

    # Both replies below are a miss, with or without suggestions.
    note_error_reason("not_found")
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
