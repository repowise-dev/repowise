"""MCP Tool: get_symbol — byte-precise source retrieval for a single symbol.

This is the structural counterpart to get_context. Where get_context returns
file-level narrative (summary, symbol list, importers), get_symbol returns
the actual source bytes of one named symbol — function body, class body, or
method — by slicing the on-disk source file using the line range stored on
the WikiSymbol row at index time.

Why a separate tool instead of "include source" on get_context?
  * Granularity: a single function is ~30 lines vs a 300-line file. Cuts the
    cached prompt prefix by ~10x when the agent only needs one symbol.
  * Predictability: response size is bounded by the symbol size, never the
    file size — no surprise 50 KB payloads.
  * Verified bounds: the persisted line range is checked against the live
    file before serving (name-on-definition-line gate); on mismatch the
    file is re-parsed once, the bounds corrected and healed in the DB. A
    ``verified: true`` response never needs a follow-up Read.

The tool is intentionally additive — get_context remains the right call for
"explain this file" or "what's the relationship between A and B" questions.
get_symbol is for "show me the body of this function".

Resolution strategy (in order):
  1. Exact match on WikiSymbol.symbol_id (the canonical "{path}::{name}" key)
  2. Exact match on (file_path, qualified_name) — supports class.method form
  3. Exact match on (file_path, name) — supports unqualified names

The tool also resolves **omission refs**: a ``symbol_id`` of the form
``"repowise#<12-hex>"`` (from a ``[repowise#<ref>: ...]`` truncation marker)
retrieves the omitted content from the durable omission store instead of the
symbol index. The two id shapes are syntactically unambiguous — a real
symbol_id always contains a file path — so dispatch is trivial and the
symbol contract is untouched.

Returns a flat dict (not wrapped in `targets`) so the agent can pipe the
`source` field straight to its scratch buffer.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    is_excluded,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import symbol_hint as _symbol_hint
from repowise.server.mcp_server._verify import check_symbol_bounds, heal_symbol_row

_log = __import__("logging").getLogger("repowise.mcp.symbol")

# Safety cap so a misconfigured WikiSymbol row pointing at a giant file
# can never blow up the agent's context window. Set to ~18 KB of source:
# on a real index 99.93% of symbols are <600 lines, so a single get_symbol
# serves the WHOLE body in one call for all but a handful of monster
# functions — round-trip count, not payload size, is what dominates agent
# token cost (S1 dogfood). The rare overflow gets a clean `continuation`
# token rather than a guessed range read.
_MAX_SOURCE_LINES = 600

# Omission-ref dispatch: "repowise#<12-hex>" never collides with a
# "{path}::{name}" symbol_id. Also tolerates a pasted whole marker.
_OMISSION_REF_RE = re.compile(r"^repowise#([0-9a-f]{12})$")

# Range-read dispatch: "path/to/file.py:140-180". A single colon followed by
# a numeric range never collides with "{path}::{name}" (double colon) or an
# omission ref. The escape hatch for everything not indexed as a symbol —
# imports, module docstrings, decorators between symbols — without falling
# back to a whole-file Read.
_RANGE_ID_RE = re.compile(r"^(?P<path>.+?):(?P<start>\d+)-(?P<end>\d+)$")

# Range reads are bounded tighter than symbol reads: the caller names exact
# lines, so a large range is a deliberate choice to cap.
_MAX_RANGE_LINES = 200

# Dead-end recovery: max grep matches returned when a symbol lookup misses
# but the file exists on disk.
_MAX_FALLBACK_MATCHES = 8


def _extract_omission_ref(symbol_id: str) -> str | None:
    """Return the 12-hex omission ref when *symbol_id* is ref-shaped, else None."""
    candidate = symbol_id.strip()
    match = _OMISSION_REF_RE.match(candidate)
    if match:
        return match.group(1)
    if candidate.startswith("[repowise#"):
        from repowise.core.distill.markers import MARKER_RE

        marker = MARKER_RE.search(candidate)
        if marker:
            return marker.group("ref")
    return None


def _resolve_omission_ref(
    symbol_id: str, ref: str, query: str | None, repo_root: Any, t0: float
) -> dict:
    """Look *ref* up in the omission store(s) and shape a tool response.

    Checks the repo-local store first, then the user-level fallback —
    the same order as ``repowise expand``. Stores are only opened when the
    DB file already exists (opening would otherwise create an empty one).
    """
    from repowise.core.distill.store import OmissionStore, default_store_path

    candidates: list[Path] = []
    if repo_root:
        candidates.append(default_store_path(Path(str(repo_root))))
    home_store = default_store_path(Path.home())
    if home_store not in candidates:
        candidates.append(home_store)

    record: dict | None = None
    for db_path in candidates:
        if not db_path.exists():
            continue
        store = OmissionStore(db_path)
        try:
            record = store.get_record(ref, query=query)
        finally:
            store.close()
        if record is not None:
            break

    if record is None:
        return {
            "symbol_id": symbol_id,
            "ref": ref,
            "error": (
                f"No stored content for omission ref {ref!r} — it may have "
                "expired (7-day TTL), been pruned, or been produced in a "
                "different repo. Re-run the original call for fresh content."
            ),
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    created = record.get("created_at")
    response: dict[str, Any] = {
        "symbol_id": symbol_id,
        "ref": ref,
        "kind": "omission",
        "source": record.get("source"),
        "original_tokens": record.get("original_tokens"),
        "content": record.get("content"),
        "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
    }
    if isinstance(created, (int, float)):
        response["created_at"] = datetime.fromtimestamp(created, tz=UTC).isoformat()
    if query is not None:
        response["query"] = query
        if not record.get("content"):
            response["note"] = "No lines matched the query; omit query for the full content."
    return response


async def _resolve_range_read(
    symbol_id: str, path: str, start: int, end: int, context_lines: int, ctx: Any, t0: float
) -> dict:
    """Serve a live, bounded line-range read: "path/to/file.py:140-180".

    Always ``verified: true`` — the bytes come straight from the live file
    at the requested lines. Bounded to _MAX_RANGE_LINES; the full-file token
    count is declared as the counterfactual (the Read this call replaced).
    """
    repository = None
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

    def _err(msg: str) -> dict:
        return {
            "symbol_id": symbol_id,
            "error": msg,
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000, repository=repository
            ),
        }

    if is_excluded(path, _get_exclude_spec(ctx.path)):
        return _err(f"'{path}' is excluded from indexing.")
    if not ctx.path:
        return _err("MCP server has no repo path configured")

    text = _read_file_text(Path(str(ctx.path)), path)
    if text is None:
        return _err(f"File could not be read: {path!r}")

    from repowise.core.distill.budget import estimate_tokens

    full_tokens = estimate_tokens(text)

    if end < start:
        start, end = end, start
    requested_end = end
    requested_truncated = (end - start + 1) > _MAX_RANGE_LINES
    if requested_truncated:
        end = start + _MAX_RANGE_LINES - 1

    # Cap the served span at _MAX_RANGE_LINES *including* context expansion —
    # the docstring promises ≤200 lines, so context_lines must not push past
    # it. Truncated reflects either an oversized request or a context-clipped
    # span.
    source, s, e, total = _slice_text(text, start, end, context_lines, max_lines=_MAX_RANGE_LINES)
    range_truncated = requested_truncated or (e - s + 1) >= _MAX_RANGE_LINES

    response = {
        "symbol_id": symbol_id,
        "file": path,
        "kind": "range",
        "start_line": s,
        "end_line": e,
        "total_lines": total,
        "source": source,
        "truncated": range_truncated,
        "verified": True,
        "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000, repository=repository),
    }
    remainder_end = min(requested_end, total)
    if range_truncated and e < remainder_end:
        # Same clean-continuation contract as a truncated symbol read: name the
        # exact next range instead of leaving the agent to guess it.
        response["continuation"] = f"{path}:{e + 1}-{remainder_end}"
        response["note"] = (
            f"Range capped at {_MAX_RANGE_LINES} lines; served {s}-{e}. "
            f"Continue in one call: get_symbol({response['continuation']!r})."
        )
    from repowise.server.mcp_server._savings import declare_replaced

    declare_replaced(response, full_tokens)
    return response


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
    lines = text.splitlines()
    matches: list[dict] = []
    for i, line in enumerate(lines, 1):
        if bare in line:
            lo, hi = max(1, i - 2), min(len(lines), i + 2)
            matches.append({"line": i, "context": "\n".join(lines[lo - 1 : hi])})
            if len(matches) >= _MAX_FALLBACK_MATCHES:
                break
    return matches


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


def _pick_canonical(rows: list[WikiSymbol], queried_file_path: str | None) -> WikiSymbol | None:
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
    rows_sorted = sorted(rows, key=lambda r: r.id or "")
    if len(rows) > 1:
        _log.warning(
            "get_symbol: %d duplicate rows for lookup (file=%s); picked id=%s",
            len(rows),
            queried_file_path,
            rows_sorted[0].id,
        )
    return rows_sorted[0]


async def _resolve_symbol(session, repo_id: str, symbol_id: str) -> WikiSymbol | None:
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


def _read_file_text(repo_path: Path, file_path: str) -> str | None:
    """Read a repo file's live text, or None when unreadable/outside the root."""
    abs_path = (repo_path / file_path).resolve()
    # Defense in depth: never read outside the repo root, even if the
    # WikiSymbol.file_path was somehow tampered with.
    try:
        abs_path.relative_to(repo_path.resolve())
    except ValueError:
        _log.warning("get_symbol path escape attempt: %s", file_path)
        return None
    try:
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _log.warning("get_symbol read failed for %s: %s", abs_path, exc)
        return None


def _slice_text(
    text: str,
    start_line: int,
    end_line: int,
    context_lines: int,
    max_lines: int = _MAX_SOURCE_LINES,
) -> tuple[str, int, int, int]:
    """Slice ``text`` to (source, actual_start, actual_end, total_lines).

    Lines are 1-indexed inclusive to match WikiSymbol storage; the range is
    expanded by ``context_lines`` on both sides and capped at ``max_lines``
    (truncating from the tail — the head carries the signature, which is what
    the agent needs to ground itself). The cap is applied AFTER context
    expansion so the returned span never exceeds it.
    """
    lines = text.splitlines()
    total = len(lines)

    s = max(1, min(start_line, total))
    e = max(s, min(end_line, total))
    if context_lines > 0:
        s = max(1, s - context_lines)
        e = min(total, e + context_lines)

    if (e - s + 1) > max_lines:
        e = s + max_lines - 1

    return "\n".join(lines[s - 1 : e]), s, e, total


@mcp.tool()
async def get_symbol(
    symbol_id: str,
    context_lines: int = 0,
    repo: str | None = None,
    query: str | None = None,
) -> dict:
    """Read one function/class/constant with live-verified line bounds.

    Raw source of one indexed symbol, bounded (~600 lines) — cheaper than
    Read+offset math. ``verified: true`` = bounds checked (or corrected)
    against the live file: no follow-up Read needed. ``bounds:
    "approximate"`` = the symbol moved and re-location failed. Also serves
    live range reads ("path.py:140-180", ≤200 lines, always verified) and
    omission refs ("repowise#<12-hex>"). Index misses grep the live file
    and return fallback_lines instead of a dead end. When ``truncated`` is
    true the response carries a ``continuation`` token — the exact range read
    that fetches the remainder; pass it straight back to get_symbol.

    Args:
        symbol_id: "path/to/file.py::Name" (from get_context),
            "path/to/file.py:140-180" for a live range, or an omission ref.
        context_lines: extra lines before/after (0-50).
        repo: usually omitted.
        query: omission refs only — regex/substring filter on lines.
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

    omission_ref = _extract_omission_ref(symbol_id)
    if omission_ref is not None:
        return _resolve_omission_ref(symbol_id, omission_ref, query, ctx.path, t0)

    # Range read: "path/to/file.py:140-180" (single colon + numeric range —
    # never collides with "{path}::{name}").
    range_match = _RANGE_ID_RE.match(symbol_id.strip())
    if range_match and "::" not in symbol_id:
        return await _resolve_range_read(
            symbol_id,
            range_match.group("path"),
            int(range_match.group("start")),
            int(range_match.group("end")),
            max(0, min(50, context_lines)),
            ctx,
            t0,
        )

    repository = None
    if context_lines < 0 or context_lines > 50:
        # Bound context_lines to a sane range — runaway values would
        # defeat the whole point of symbol-level retrieval.
        context_lines = max(0, min(50, context_lines))

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        row = await _resolve_symbol(session, repository.id, symbol_id)

    exclude_spec = _get_exclude_spec(ctx.path)
    if row is None or is_excluded(getattr(row, "file_path", None), exclude_spec):
        # Dead-end recovery: constants/imports/aliases between indexed
        # symbols miss the index but live in the file — grep the live file
        # for the name and serve the matching lines instead of a pure error.
        if row is None and ctx.path:
            file_part, name_part = _parse_symbol_id(symbol_id)
            if file_part and name_part and not is_excluded(file_part, exclude_spec):
                matches = _live_grep_fallback(Path(str(ctx.path)), file_part, name_part)
                if matches:
                    return {
                        "symbol_id": symbol_id,
                        "file": file_part,
                        "resolution": "live_grep",
                        "fallback_lines": matches,
                        "verified": True,
                        "note": (
                            "Not an indexed symbol, but the name matches these "
                            "live-file lines (likely a constant, import, or "
                            "alias). For surrounding source use a range read: "
                            f'"{file_part}:<start>-<end>".'
                        ),
                        "_meta": _build_meta(
                            timing_ms=(time.perf_counter() - t0) * 1000,
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
                timing_ms=(time.perf_counter() - t0) * 1000,
                repository=repository,
            ),
        }

    if not ctx.path:
        return {
            "symbol_id": symbol_id,
            "error": "MCP server has no repo path configured",
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                repository=repository,
            ),
        }

    repo_root = Path(str(ctx.path))
    text = _read_file_text(repo_root, row.file_path)

    if text is None:
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
                timing_ms=(time.perf_counter() - t0) * 1000,
                repository=repository,
            ),
        }

    from repowise.core.distill.budget import estimate_tokens

    full_tokens = estimate_tokens(text)

    # Trust contract: verify the stored bounds against the live file before
    # serving a single byte. Stale bounds get corrected via a one-file
    # re-parse (and healed in the DB); un-relocatable symbols are served as
    # the indexed guess, explicitly tagged approximate.
    check = check_symbol_bounds(row, text)
    if check.corrected:
        await heal_symbol_row(ctx.session_factory, row, check.start_line, check.end_line)

    source, start, end, _total = _slice_text(text, check.start_line, check.end_line, context_lines)

    truncated = (end - start + 1) >= _MAX_SOURCE_LINES and (
        check.end_line - check.start_line + 1 + 2 * context_lines
    ) > _MAX_SOURCE_LINES

    response = {
        "symbol_id": row.symbol_id,
        "file": row.file_path,
        "name": row.name,
        "kind": row.kind,
        "qualified_name": row.qualified_name,
        "signature": row.signature,
        "language": row.language,
        "start_line": start,
        "end_line": end,
        "symbol_start_line": check.start_line,
        "symbol_end_line": check.end_line,
        "source": source,
        "truncated": truncated,
        "verified": check.verified,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_symbol_hint(row.symbol_id, check.end_line, check.start_line),
            repository=repository,
        ),
    }
    if truncated and not check.approximate and end < check.end_line:
        # The body exceeds the serve cap. Hand back the exact range read that
        # fetches the remainder so the agent never has to guess the next span
        # (the S1 dogfood found it would otherwise grub-around with a guessed
        # range, doubling the call cost).
        response["continuation"] = f"{row.file_path}:{end + 1}-{check.end_line}"
        response["note"] = (
            f"Symbol body ({check.start_line}-{check.end_line}) exceeds the "
            f"{_MAX_SOURCE_LINES}-line serve cap; served {start}-{end}. Fetch "
            f"the remainder in one call: get_symbol({response['continuation']!r})."
        )
    if check.approximate:
        response["bounds"] = "approximate"
        response["note"] = (
            "The live file no longer contains this symbol at its indexed "
            "location and it could not be re-located by name — it may have "
            "been renamed or removed since indexing. The served slice is "
            "the indexed line range from the current file contents; verify "
            "before citing."
        )
    # Declare the exact counterfactual: serving one symbol replaced Reading the
    # whole file. The savings instrumentation prefers this over its estimator.
    from repowise.server.mcp_server._savings import declare_replaced

    declare_replaced(response, full_tokens)
    return response
