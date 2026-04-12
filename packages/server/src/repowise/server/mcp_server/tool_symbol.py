"""MCP Tool: get_symbol — byte-precise source retrieval for a single symbol.

This is the structural counterpart to get_context. Where get_context returns
file-level narrative (summary, symbol list, importers), get_symbol returns
the actual source bytes of one named symbol — function body, class body, or
method — by slicing the on-disk source file using the line range stored on
the WikiSymbol row at index time.

Why a separate tool instead of "include source" on get_context?
  * Granularity: a single function is ~30 lines vs a 300-line file. Cuts the
    cached prompt prefix by ~10× when the agent only needs one symbol.
  * Predictability: response size is bounded by the symbol size, never the
    file size — no surprise 50 KB payloads.
  * No reparsing: the bytes come straight from disk via the persisted line
    range. Tree-sitter never runs at retrieval time.

The tool is intentionally additive — get_context remains the right call for
"explain this file" or "what's the relationship between A and B" questions.
get_symbol is for "show me the body of this function".

Resolution strategy (in order):
  1. Exact match on WikiSymbol.symbol_id (the canonical "{path}::{name}" key)
  2. Exact match on (file_path, qualified_name) — supports class.method form
  3. Exact match on (file_path, name) — supports unqualified names

Returns a flat dict (not wrapped in `targets`) so the agent can pipe the
`source` field straight to its scratch buffer.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import symbol_hint as _symbol_hint
from repowise.server.mcp_server._server import mcp

_log = __import__("logging").getLogger("repowise.mcp.symbol")

# Safety cap so a misconfigured WikiSymbol row pointing at a giant file
# can never blow up the agent's context window. Tuned to ~12 KB of source.
_MAX_SOURCE_LINES = 400


def _parse_symbol_id(symbol_id: str) -> tuple[str | None, str | None]:
    """Split a "{path}::{name}" id. Either side may be None if missing.

    Tolerant of double-colons in qualified names like "Foo::Bar::baz" by
    splitting on the FIRST "::" only — the first segment is always the file
    path. Returns (file_path, name) where name may itself contain "::" for
    nested qualified forms ("Class::method").
    """
    if not symbol_id or "::" not in symbol_id:
        return symbol_id or None, None
    file_part, _, name_part = symbol_id.partition("::")
    return (file_part or None, name_part or None)


# Separators used between name segments AFTER the file path. Different
# languages use different conventions: Python/TS/Go use ".", C++/Rust use
# "::", and some tools emit "/". The lookup must be uniform across all of
# them — we never encode a single language's rule.
_NAME_SEPARATORS = (".", "::", "/")


def _name_variants(name: str) -> list[str]:
    """Generate all separator variants of a qualified name segment.

    Given "App.update_template_context" we yield the same name with every
    supported separator between segments, so a DB storing "App::method"
    still resolves when the agent passed dot-form (or vice versa).

    Operates only on the *name* (post file-path), never on the path itself.
    """
    if not name:
        return []
    # Split on any of the known separators to get atomic segments.
    segments = [name]
    for sep in _NAME_SEPARATORS:
        next_segments: list[str] = []
        for seg in segments:
            next_segments.extend(seg.split(sep))
        segments = next_segments
    segments = [s for s in segments if s]
    if not segments:
        return [name]
    variants: list[str] = []
    seen: set[str] = set()
    for sep in _NAME_SEPARATORS:
        v = sep.join(segments)
        if v not in seen:
            seen.add(v)
            variants.append(v)
    # Also include the original as-is in case it used a mixed separator.
    if name not in seen:
        variants.append(name)
    return variants


def _symbol_id_variants(symbol_id: str) -> list[str]:
    """Generate {file_path}::{name_variant} for every name separator form."""
    file_path, name = _parse_symbol_id(symbol_id)
    if not file_path or not name:
        return [symbol_id]
    out: list[str] = []
    seen: set[str] = set()
    for nv in _name_variants(name):
        sid = f"{file_path}::{nv}"
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    if symbol_id not in seen:
        out.append(symbol_id)
    return out


def _bare_name(name: str) -> str:
    """Return the last name segment regardless of separator style."""
    tail = name
    for sep in _NAME_SEPARATORS:
        tail = tail.rsplit(sep, 1)[-1]
    return tail


def _pick_canonical(
    rows: list[WikiSymbol], queried_file_path: str | None
) -> WikiSymbol | None:
    """Deterministically select one row from a candidate list.

    Priority:
      1. file_path matches the file_path embedded in the queried symbol_id
      2. deterministic tiebreak on the (id) primary key (ascending)
    """
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    if queried_file_path:
        matching = [r for r in rows if r.file_path == queried_file_path]
        if matching:
            rows = matching
    # Deterministic: lowest id wins. (No confidence column today; leaving
    # room to add one without changing the fallback.)
    rows_sorted = sorted(rows, key=lambda r: (r.id or ""))
    if len(rows) > 1:
        _log.warning(
            "get_symbol: %d duplicate rows for lookup (file=%s); picked id=%s",
            len(rows),
            queried_file_path,
            rows_sorted[0].id,
        )
    return rows_sorted[0]


async def _resolve_symbol(
    session, repo_id: str, symbol_id: str
) -> WikiSymbol | None:
    """Look up a symbol by id, qualified_name, or bare name. None if absent.

    Language-agnostic: the qualified-name portion of the symbol_id is
    normalized across ``.``, ``::`` and ``/`` separators before matching,
    so callers can pass any of ``Class.method``, ``Class::method``, or
    ``Class/method`` and still resolve. Only the name part is normalized —
    file paths are never rewritten.

    Duplicate-safe: every query uses ``.all()`` + :func:`_pick_canonical`
    instead of ``.scalar_one_or_none()``. The canonical constraint
    ``uq_wiki_symbol`` on ``(repository_id, symbol_id)`` already prevents
    duplicates on the primary key, but the fallback lookups on
    ``(file_path, qualified_name)`` and ``(file_path, name)`` can legitimately
    return several rows (e.g. overloads, re-exports, conditional defs).
    """
    file_path, _name = _parse_symbol_id(symbol_id)
    variants = _symbol_id_variants(symbol_id)

    # 1. Exact symbol_id — try every separator variant.
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.symbol_id.in_(variants),
        )
    )
    rows = list(res.scalars().all())
    picked = _pick_canonical(rows, file_path)
    if picked is not None:
        return picked

    _, name = _parse_symbol_id(symbol_id)
    if not name:
        return None

    name_variants = _name_variants(name)

    # 2. Match on (file_path, qualified_name) across name variants.
    if file_path:
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
                WikiSymbol.qualified_name.in_(name_variants),
            )
        )
        rows = list(res.scalars().all())
        picked = _pick_canonical(rows, file_path)
        if picked is not None:
            return picked

        # 3. Match on (file_path, name) — last segment of qualified name.
        bare = _bare_name(name)
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
                WikiSymbol.name == bare,
            )
        )
        rows = list(res.scalars().all())
        picked = _pick_canonical(rows, file_path)
        if picked is not None:
            return picked

    return None


def _slice_source(
    repo_path: Path, file_path: str, start_line: int, end_line: int, context_lines: int
) -> tuple[str, int, int, int | None]:
    """Read the file and return (source, actual_start, actual_end, total_lines).

    Returns ("", start, end, None) on read failure. Lines are 1-indexed in the
    inputs and outputs to match WikiSymbol storage. Honors _MAX_SOURCE_LINES.
    """
    abs_path = (repo_path / file_path).resolve()
    # Defense in depth: never read outside the repo root, even if the
    # WikiSymbol.file_path was somehow tampered with.
    try:
        abs_path.relative_to(repo_path.resolve())
    except ValueError:
        _log.warning("get_symbol path escape attempt: %s", file_path)
        return "", start_line, end_line, None
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _log.warning("get_symbol read failed for %s: %s", abs_path, exc)
        return "", start_line, end_line, None

    lines = text.splitlines()
    total = len(lines)

    # 1-indexed inclusive range, then expand by context_lines on both sides.
    s = max(1, min(start_line, total))
    e = max(s, min(end_line, total))
    if context_lines > 0:
        s = max(1, s - context_lines)
        e = min(total, e + context_lines)

    span = e - s + 1
    if span > _MAX_SOURCE_LINES:
        # Truncate from the tail; the head usually has the signature
        # which is what the agent needs to ground itself.
        e = s + _MAX_SOURCE_LINES - 1
        span = _MAX_SOURCE_LINES

    sliced = "\n".join(lines[s - 1 : e])
    return sliced, s, e, total


@mcp.tool()
async def get_symbol(
    symbol_id: str,
    context_lines: int = 0,
    repo: str | None = None,
) -> dict:
    """Return the exact source body of one symbol by ID. Trust the result.

    Cheaper than Read for "show me X" / "what does Y do" questions.
    Returns {file, name, kind, signature, start_line, end_line, source}.
    On miss returns {error: "Symbol not found"}.

    Args:
        symbol_id: "path/to/file.py::SymbolName" (canonical id from get_context).
        context_lines: extra source lines before/after (0–50, default 0).
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_symbol")
    ctx = await _resolve_repo_context(repo)

    t0 = time.perf_counter()
    if not symbol_id or not symbol_id.strip():
        return {
            "symbol_id": symbol_id,
            "error": "symbol_id is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }
    if context_lines < 0 or context_lines > 50:
        # Bound context_lines to a sane range — runaway values would
        # defeat the whole point of symbol-level retrieval.
        context_lines = max(0, min(50, context_lines))

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        row = await _resolve_symbol(session, repository.id, symbol_id)

    if row is None:
        return {
            "symbol_id": symbol_id,
            "error": (
                f"Symbol not found: {symbol_id!r}. Use get_context to list "
                "available symbols in the file, then try again with the "
                "exact symbol_id from that response."
            ),
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    if not ctx.path:
        return {
            "symbol_id": symbol_id,
            "error": "MCP server has no repo path configured",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    repo_root = Path(str(ctx.path))
    source, start, end, total = _slice_source(
        repo_root, row.file_path, row.start_line, row.end_line, context_lines
    )

    if not source:
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
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    truncated = (end - start + 1) >= _MAX_SOURCE_LINES and (
        row.end_line - row.start_line + 1 + 2 * context_lines
    ) > _MAX_SOURCE_LINES

    return {
        "symbol_id": row.symbol_id,
        "file": row.file_path,
        "name": row.name,
        "kind": row.kind,
        "qualified_name": row.qualified_name,
        "signature": row.signature,
        "language": row.language,
        "start_line": start,
        "end_line": end,
        "symbol_start_line": row.start_line,
        "symbol_end_line": row.end_line,
        "source": source,
        "truncated": truncated,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_symbol_hint(row.symbol_id, row.end_line, row.start_line),
        ),
    }
