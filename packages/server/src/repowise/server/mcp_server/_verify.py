"""Serve-time freshness verification for symbol bounds.

WikiSymbol line bounds are written at index time and the source file may
have changed since — historically get_symbol sliced the live file with
stale bounds and served the wrong lines while ``_meta`` claimed the index
was fresh. The agent's rational response was to re-read every MCP response
from disk, which is the verification tax this module deletes.

The contract:

* ``verified: true``  — the served bytes were checked against the live
  file (either the stored bounds still match, or the file was re-parsed
  and the bounds corrected). A verified response never needs a follow-up
  Read.
* ``bounds: "approximate"`` — the live file no longer contains the symbol
  where the index said (rename/delete since indexing) and re-location
  failed; the served slice is the indexed guess, treat accordingly.

Verification is two-tier:

1. Cheap gate (every call): the symbol's bare name must appear on its
   stored definition line in the live file. String containment on one
   line — effectively free since the file is already read for slicing.
2. Re-parse (mismatch only): tree-sitter re-parses just this file
   (compiled queries are process-cached; milliseconds), the symbol is
   re-located by id → (name, parent) → name, and the corrected bounds are
   written back to the WikiSymbol row so the next call hits the cheap
   gate again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from repowise.core.persistence.models import WikiSymbol

_log = logging.getLogger("repowise.mcp.verify")


@dataclass
class BoundsCheck:
    """Outcome of verifying one symbol's bounds against live source."""

    start_line: int
    end_line: int
    verified: bool
    corrected: bool = False  # True when re-parse moved the bounds
    approximate: bool = False  # True when the symbol could not be re-located


def name_at_line(lines: list[str], name: str, start_line: int) -> bool:
    """Cheap gate: does the stored definition line still carry the name?

    ``lines`` is the live file split into lines; ``start_line`` is
    1-indexed. The bare name (last segment of a qualified name) must appear
    as a substring of the definition line — true for def/class/assignment
    lines across every indexed language.
    """
    if start_line < 1 or start_line > len(lines):
        return False
    bare = name.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    return bool(bare) and bare in lines[start_line - 1]


def relocate_symbol(row: WikiSymbol, source_text: str) -> tuple[int, int] | None:
    """Re-parse the live file and find the symbol's current bounds.

    Returns (start_line, end_line) or None when the symbol no longer
    exists in the file (deleted/renamed) or the file cannot be parsed.
    """
    try:
        from repowise.core.ingestion.models import FileInfo
        from repowise.core.ingestion.parser import ASTParser

        fi = FileInfo(
            path=row.file_path,
            abs_path=row.file_path,
            language=row.language,
            size_bytes=len(source_text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed = ASTParser().parse_file(fi, source_text.encode("utf-8", errors="replace"))
    except Exception as exc:
        _log.warning("re-parse failed for %s: %s", row.file_path, exc)
        return None

    symbols = parsed.symbols or []
    # Strongest match first: exact symbol_id, then (name, parent), then name.
    for sym in symbols:
        if sym.id == row.symbol_id:
            return sym.start_line, sym.end_line
    for sym in symbols:
        if sym.name == row.name and (sym.parent_name or None) == (row.parent_name or None):
            return sym.start_line, sym.end_line
    candidates = [s for s in symbols if s.name == row.name]
    if len(candidates) == 1:
        return candidates[0].start_line, candidates[0].end_line
    return None


def check_symbol_bounds(row: WikiSymbol, source_text: str) -> BoundsCheck:
    """Verify (and if needed correct) a symbol's bounds against live source."""
    lines = source_text.splitlines()
    if name_at_line(lines, row.name, row.start_line):
        end = min(max(row.end_line, row.start_line), len(lines))
        return BoundsCheck(start_line=row.start_line, end_line=end, verified=True)

    located = relocate_symbol(row, source_text)
    if located is not None:
        _log.info(
            "bounds corrected for %s: %d-%d -> %d-%d",
            row.symbol_id,
            row.start_line,
            row.end_line,
            located[0],
            located[1],
        )
        return BoundsCheck(
            start_line=located[0], end_line=located[1], verified=True, corrected=True
        )

    return BoundsCheck(
        start_line=row.start_line,
        end_line=row.end_line,
        verified=False,
        approximate=True,
    )


async def heal_symbol_row(session_factory, row: WikiSymbol, start: int, end: int) -> None:
    """Persist corrected bounds so the next serve hits the cheap gate.

    Best-effort: a failed heal only costs a re-parse on the next call.
    """
    from sqlalchemy import update

    from repowise.core.persistence.database import get_session

    try:
        async with get_session(session_factory) as session:
            await session.execute(
                update(WikiSymbol)
                .where(WikiSymbol.id == row.id)
                .values(start_line=start, end_line=end)
            )
            await session.commit()
    except Exception as exc:
        _log.warning("bounds self-heal failed for %s: %s", row.symbol_id, exc)
