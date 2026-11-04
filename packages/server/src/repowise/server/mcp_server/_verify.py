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
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from repowise.core.persistence.models import WikiSymbol

_log = logging.getLogger("repowise.mcp.verify")

# Substring containment on a 1-2 char name is meaningless — a constant named
# ``T`` (TypeVar) or ``e`` "matches" almost any line. Names shorter than this
# skip the cheap gate and go straight to a re-parse, which can actually
# confirm them. (Module-level constants/variables now indexed make short
# names common, so the gate must not rubber-stamp them.)
_MIN_GATE_NAME_LEN = 3
# A line opening an annotation, attribute or decorator, which JVM, Swift, Rust
# and C# definitions can start on. Lines inside its brackets continue it.
_ATTRIBUTE_LINE = re.compile(r"^\s*(?:@|#\[|\[)")
# A C/C++ return-type or template line above the name (``LRESULT``,
# ``static int``, ``template <typename T>``): one type-like token, at most one
# qualifier before it. A bare statement keyword is not a type.
_TYPE_HEAD = re.compile(
    r"^\s*(?:(?:static|inline|extern|virtual|constexpr|const|unsigned|signed|friend|explicit)\s+)?"
    r"(template\s*<.*>|[A-Za-z_][\w:]*(?:<.*>)?)[\s*&]*$"
)
# Control-flow and statement words: a line led by one is not a definition or
# its type head.
NOT_A_DEFINITION = frozenset(
    {
        "if", "for", "while", "switch", "catch", "else", "do", "try", "return",
        "with", "using", "lock", "foreach", "case", "synchronized", "await",
        "yield", "new", "typeof", "in", "of", "when", "unless", "match",
        "raise", "throw", "assert", "del", "delete", "print", "elif", "except",
        "finally", "import", "from", "global", "nonlocal", "pass", "break",
        "continue", "go", "defer", "select", "range", "constructor",
    }
)
# ``end`` closes a Ruby or Lua block, but it is a real JS method name, so only
# the type-head check refuses it.
_NOT_A_TYPE_HEAD = NOT_A_DEFINITION | {"end"}
_MAX_TYPE_HEAD_LINES = 2
_MAX_ATTRIBUTE_LINES = 20


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
    lines across every indexed language. A recorded start on an annotation
    block (``@Override``, ``#[test]``, ``[Fact]``, one spanning lines) or a
    return-type line (``LRESULT``) is read past to the line it heads.
    """
    if start_line < 1 or start_line > len(lines):
        return False
    bare = _bare_name(name)
    if not bare:
        return False
    depth = heads = 0
    for text in lines[start_line - 1 : start_line - 1 + _MAX_ATTRIBUTE_LINES]:
        # An annotation's arguments can name anything (``@Deprecated(since="foo")``),
        # so only the code after it on the line counts.
        if depth:
            depth = _depth_after(depth, text)
        elif _ATTRIBUTE_LINE.match(text):
            if bare in _after_attributes(text):
                return True
            depth = _depth_after(0, text)
        elif bare in text:
            return True
        elif heads < _MAX_TYPE_HEAD_LINES and _is_type_head(text):
            heads += 1
        else:
            return False
    return False


def _depth_after(depth: int, text: str) -> int:
    return max(0, depth + sum(map(text.count, "([{")) - sum(map(text.count, ")]}")))


def _after_attributes(text: str) -> str:
    """*text* past its leading annotations (``@A(x) @B [C] #[d]``); empty when one stays open."""
    i, n = 0, len(text)
    while True:
        while i < n and text[i].isspace():
            i += 1
        if text.startswith("@", i):
            i += 1
            while i < n and (text[i].isalnum() or text[i] in "_.:"):
                i += 1
            if not text.startswith("(", i):
                continue
        elif text.startswith("#[", i):
            i += 1
        elif not text.startswith("[", i):
            return text[i:]
        depth = 0
        for j in range(i, n):
            depth += (text[j] in "([{") - (text[j] in ")]}")
            if not depth:
                i = j + 1
                break
        else:
            return ""


def _is_type_head(text: str) -> bool:
    m = _TYPE_HEAD.match(text)
    if m is None:
        return False
    token = m.group(1)
    return token.startswith("template") or (len(token) >= 3 and token not in _NOT_A_TYPE_HEAD)


def _bare_name(name: str) -> str:
    """Last segment of a qualified name, regardless of separator style."""
    return (name or "").rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def end_anchor_holds(lines: list[str], start_line: int, end_line: int) -> bool:
    """Cheap end check: the line past the stored end isn't still the body.

    The start gate confirms the definition line, but it cannot see an edit
    *inside* the body — lines inserted after the definition leave the def
    line in place while pushing the true end down, so the stored end now
    truncates the symbol. We can't pin the exact end without re-parsing, but
    we can catch that common failure cheaply: if the line immediately after
    the stored end is indented deeper than the definition line, the body
    grew and the stored end is wrong. EOF, a blank line, or a dedented line
    is consistent with a real boundary (brace-closers and module-level
    neighbours both satisfy ``<=`` the definition indent).
    """
    if end_line < start_line or end_line < 1 or end_line > len(lines):
        return False
    def_indent = _line_indent(lines[start_line - 1])
    if end_line >= len(lines):
        return True  # symbol ends at EOF
    after = lines[end_line]  # 0-indexed: first line past the 1-indexed end
    if not after.strip():
        return True
    return _line_indent(after) <= def_indent


def parse_live_symbols(row: WikiSymbol, source_text: str) -> list[Any] | None:
    """Parse the live file that holds *row*; None when it cannot be parsed."""
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
    return list(parsed.symbols or [])


def relocate_symbol(
    row: WikiSymbol,
    source_text: str,
    parsed: Callable[[], list[Any] | None] | None = None,
) -> tuple[int, int] | None:
    """Re-parse the live file and find the symbol's current bounds.

    Returns (start_line, end_line) or None when the symbol no longer
    exists in the file (deleted/renamed) or the file cannot be parsed.
    ``parsed`` lets a caller checking many rows of one file parse it once.
    """
    live = parsed() if parsed is not None else parse_live_symbols(row, source_text)
    if live is None:
        return None

    # An overload set is several rows under one id; the implementation (the
    # one non-declaration) goes first so a stub never answers for it.
    symbols = sorted(live, key=lambda sym: sym.is_declaration)
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


def check_symbol_bounds(
    row: WikiSymbol,
    source_text: str,
    parsed: Callable[[], list[Any] | None] | None = None,
) -> BoundsCheck:
    """Verify (and if needed correct) a symbol's bounds against live source.

    Cheap gate (no re-parse) only when ALL hold: the name is long enough for
    substring containment to mean something, it still sits on the stored
    definition line, AND the stored end still bounds the body. Any miss
    falls through to a one-file re-parse that pins the true bounds.
    """
    lines = source_text.splitlines()
    bare = _bare_name(row.name)
    cheap_ok = (
        len(bare) >= _MIN_GATE_NAME_LEN
        and name_at_line(lines, row.name, row.start_line)
        and end_anchor_holds(lines, row.start_line, row.end_line)
    )
    if cheap_ok:
        end = min(max(row.end_line, row.start_line), len(lines))
        return BoundsCheck(start_line=row.start_line, end_line=end, verified=True)

    located = relocate_symbol(row, source_text, parsed)
    if located is not None:
        corrected = located != (row.start_line, row.end_line)
        if corrected:
            _log.info(
                "bounds corrected for %s: %d-%d -> %d-%d",
                row.symbol_id,
                row.start_line,
                row.end_line,
                located[0],
                located[1],
            )
        return BoundsCheck(
            start_line=located[0], end_line=located[1], verified=True, corrected=corrected
        )

    # Unverified, but the end is still clamped to the live file: nothing can be
    # served past EOF, and the cheap path above already clamps. Leaving this one
    # return raw is what let a stored end that overshoots flag a body served
    # WHOLE as truncated, with a continuation pointing past the last line (D8).
    # Every consumer of an unverified bound inherited it -- get_answer's
    # hydrator, get_symbol and get_context all read this same return.
    return BoundsCheck(
        start_line=row.start_line,
        end_line=min(row.end_line, len(lines)) if row.end_line else row.end_line,
        verified=False,
        approximate=True,
    )


async def verify_and_heal(session_factory, row: WikiSymbol, source_text: str) -> BoundsCheck:
    """Run the bounds gate and self-heal a corrected row in one step.

    The shared serve-time contract for every site that turns a stored
    ``WikiSymbol`` bound into live bytes (get_symbol, the get_answer hydrator,
    the get_context skeleton): verify against the live file, and when a
    re-parse moves the bounds, persist the correction so the next serve hits
    the cheap gate. Healing is best-effort and skipped when no session factory
    is available (unit tests, index-only contexts) — correctness rides on the
    returned :class:`BoundsCheck`, not on the write landing.
    """
    check = check_symbol_bounds(row, source_text)
    if check.corrected and session_factory is not None:
        await heal_symbol_row(session_factory, row, check.start_line, check.end_line)
    return check


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
