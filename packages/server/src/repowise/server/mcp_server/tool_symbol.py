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
  4. Suffix match — a bare filename or partial path ("answer.py::get_answer")
     resolves against any file whose path ends with that segment, on the leaf
     name; mirrors get_context's basename ladder. A total miss returns
     ``suggestions`` (real path-qualified ids) instead of a bare "not found".

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
    read_repo_file_text,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import symbol_hint as _symbol_hint
from repowise.server.mcp_server._symbol_lookup import (
    NAME_SEPARATORS,
    bare_name,
    name_variants,
    order_candidates,
    parse_symbol_id,
    resolve_symbol_rows,
    symbol_id_variants,
)
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

# When a lookup is ambiguous (overloads, re-exports, conditional defs) every
# candidate body is served in ONE response — a silently-picked wrong candidate
# sends the agent into a read-spiral that costs far more than the extra bytes.
# This caps the total candidate-source chars; the first candidate always
# renders, the rest render while budget remains and are otherwise listed with
# an exact range read that fetches them.
_AMBIGUITY_CHAR_BUDGET = 20_000


# Callee expansion. A symbol whose body names the next symbol you need is the
# common case, and fetching that next one is a fresh round trip: the chain gets
# walked a hop at a time, each hop a full request/response, when the graph
# already knows every edge. ``depth`` serves the reachable bodies together.
#
# Depth is capped low on purpose. Fan-out compounds, so depth 3 on a hub symbol
# is a payload nobody asked for; the observed hand-walks are chains, not trees,
# and are covered well before the cap.
_MAX_CALLEE_DEPTH = 3
# Total chars of callee source served. Sized against the symbol cap above: a
# handful of ordinary bodies, and a hub is truncated rather than serialised.
_CALLEE_CHAR_BUDGET = 24_000
# Per-hop fan-out cap, applied before any body is read.
_MAX_CALLEES_PER_HOP = 12
# Callee bodies are context for the root symbol, not the subject of the call,
# so they are bounded tighter than the root's ~600.
_MAX_CALLEE_BODY_LINES = 150


async def _expand_callees(
    session,
    repo_id: str,
    root_row: WikiSymbol,
    repo_root: Path,
    depth: int,
    exclude_spec: Any,
) -> dict[str, Any] | None:
    """Breadth-first walk of the call graph from *root_row*, bodies included.

    Symbol graph nodes are keyed by the same ``"{path}::{Name}"`` string as
    ``WikiSymbol.symbol_id``, so each hop is one edge query plus one row query
    regardless of fan-out. Every symbol is served at most once and at the
    shallowest depth it was reached from, which keeps a diamond in the call
    graph from being serialised twice.

    Returns None when the root has no outbound call edges, so the caller adds
    no empty block to an ordinary response.
    """
    from repowise.core.persistence.crud import get_graph_edges_for_node
    from repowise.server.mcp_server.tool_context.enrichment import (
        _CALL_EDGE_TYPES,
        _MIN_CALL_CONFIDENCE,
    )

    seen: set[str] = {root_row.symbol_id}
    frontier = [root_row.symbol_id]
    entries: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    text_cache: dict[str, str | None] = {}
    remaining = _CALLEE_CHAR_BUDGET

    for hop in range(1, depth):
        if not frontier:
            break
        next_ids: list[str] = []
        for node_id in frontier:
            edges = await get_graph_edges_for_node(
                session,
                repo_id,
                node_id,
                direction="callees",
                edge_types=_CALL_EDGE_TYPES,
                limit=_MAX_CALLEES_PER_HOP,
            )
            for e in edges:
                if (e.confidence or 0) < _MIN_CALL_CONFIDENCE:
                    continue
                if e.target_node_id not in seen:
                    seen.add(e.target_node_id)
                    next_ids.append(e.target_node_id)
        if not next_ids:
            break

        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.symbol_id.in_(next_ids),
            )
        )
        rows = [r for r in res.scalars().all() if not is_excluded(r.file_path, exclude_spec)]
        # Stable order so the same call returns the same payload twice.
        rows.sort(key=lambda r: (r.file_path or "", r.start_line or 0, r.symbol_id or ""))

        for row in rows:
            entry: dict[str, Any] = {
                "symbol_id": row.symbol_id,
                "name": row.name,
                "file": row.file_path,
                "kind": row.kind,
                "signature": _clean_symbol_signature(row.signature),
                "depth": hop,
            }
            if row.file_path not in text_cache:
                text_cache[row.file_path] = _read_file_text(repo_root, row.file_path)
            text = text_cache[row.file_path]
            if text is None:
                entry["note"] = "source file could not be read"
                omitted.append(entry)
                continue

            # Bounds are checked so ``verified`` is honest, but a correction is
            # not written back here: healing belongs to the read that asked for
            # the symbol, not to a neighbour swept up by a graph walk.
            check = check_symbol_bounds(row, text)
            source, start, end, _total = _slice_text(
                text, check.start_line, check.end_line, 0, max_lines=_MAX_CALLEE_BODY_LINES
            )
            numbered = _number_lines(source, start)
            if len(numbered) > remaining:
                # Out of budget: name the read that fetches it rather than
                # dropping the symbol silently.
                entry["fetch_with"] = f"{row.file_path}:{check.start_line}-{check.end_line}"
                omitted.append(entry)
                continue
            remaining -= len(numbered)
            entry.update(
                {
                    "start_line": start,
                    "end_line": end,
                    "source": numbered,
                    "verified": check.verified,
                }
            )
            if end < check.end_line:
                entry["truncated"] = True
                entry["continuation"] = f"{row.file_path}:{end + 1}-{check.end_line}"
            entries.append(entry)

        frontier = next_ids

    if not entries and not omitted:
        return None
    block: dict[str, Any] = {"depth": depth, "callees": entries}
    if omitted:
        block["not_rendered"] = omitted
        block["note"] = (
            "Callees past the response budget are listed in not_rendered; "
            "fetch one with its fetch_with range."
        )
    return block


def _clean_symbol_signature(signature: str | None) -> str:
    """Collapse a stored signature onto one line (CRLF indices keep the \\r\\n)."""
    return " ".join((signature or "").split())


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
        "source": _number_lines(source, s),
        "truncated": range_truncated,
        "verified": True,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            repository=repository,
            # A range read serves live bytes; index drift elsewhere is noise.
            targets=[path],
        ),
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
            matches.append({"line": i, "context": _number_lines("\n".join(lines[lo - 1 : hi]), lo)})
            if len(matches) >= _MAX_FALLBACK_MATCHES:
                break
    return matches


# The symbol-id grammar, separator normalisation and lookup ladder are shared
# with get_context (see _symbol_lookup for why they cannot diverge). Re-exported
# under the module-private names this file has always used.
_NAME_SEPARATORS = NAME_SEPARATORS
_parse_symbol_id = parse_symbol_id
_name_variants = name_variants
_symbol_id_variants = symbol_id_variants
_bare_name = bare_name
_order_candidates = order_candidates
_resolve_symbol = resolve_symbol_rows


async def _symbol_suggestions(session, repo_id: str, symbol_id: str, exclude_spec) -> list[str]:
    """Concrete symbol_ids to retry when a lookup misses entirely.

    The caller usually had the right leaf name but a wrong or partial path.
    Match that name across every file and hand back real ids (path-qualified)
    the agent can pass straight back to get_symbol — a bare "not found" would
    otherwise send it to get_context or a whole-file Read.
    """
    _, name = _parse_symbol_id(symbol_id)
    if not name:
        return []
    bare = _bare_name(name)
    res = await session.execute(
        select(WikiSymbol.symbol_id, WikiSymbol.file_path)
        .where(WikiSymbol.repository_id == repo_id, WikiSymbol.name == bare)
        .limit(20)
    )
    out: list[str] = []
    seen: set[str] = set()
    for sid, fpath in res.all():
        if sid and sid not in seen and not is_excluded(fpath, exclude_spec):
            seen.add(sid)
            out.append(sid)
            if len(out) >= 5:
                break
    return out


def _read_file_text(repo_path: Path, file_path: str) -> str | None:
    """Read a repo file's live text, or None when unreadable/outside the root."""
    text = read_repo_file_text(repo_path, file_path)
    if text is None:
        _log.warning("get_symbol could not serve %s (unreadable or outside repo root)", file_path)
    return text


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


def _number_lines(source: str, start_line: int) -> str:
    """Prefix each line with its 1-based file line number, Read-style.

    Byte-parity with the agent's own Read tool output (``cat -n`` format:
    right-aligned number, tab, line). Source served in the exact shape of an
    already-performed Read is treated as one — the agent edits from it
    instead of re-Reading the file to "see the real format".
    """
    return "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(source.splitlines(), start_line))


async def _render_ambiguous(
    rows: list[WikiSymbol],
    symbol_id: str,
    ctx: Any,
    repository: Any,
    t0: float,
    context_lines: int,
) -> dict:
    """Serve EVERY candidate body for an ambiguous symbol id in one response.

    A deterministic-but-wrong pick reads as authoritative and sends the agent
    editing the wrong overload; returning all candidates costs bytes once and
    removes the guess. The first candidate always renders; the rest render
    while the char budget lasts and are otherwise listed with the exact range
    read that fetches them.
    """
    repo_root = Path(str(ctx.path))
    text_cache: dict[str, str | None] = {}
    candidates: list[dict] = []
    not_rendered: list[dict] = []
    remaining = _AMBIGUITY_CHAR_BUDGET

    for i, row in enumerate(rows):
        if row.file_path not in text_cache:
            text_cache[row.file_path] = _read_file_text(repo_root, row.file_path)
        text = text_cache[row.file_path]

        entry: dict[str, Any] = {
            "symbol_id": row.symbol_id,
            "file": row.file_path,
            "name": row.name,
            "kind": row.kind,
            "qualified_name": row.qualified_name,
            "signature": row.signature,
        }
        if text is None:
            entry["note"] = "source file could not be read"
            not_rendered.append(entry)
            continue

        check = check_symbol_bounds(row, text)
        if check.corrected:
            await heal_symbol_row(ctx.session_factory, row, check.start_line, check.end_line)
        source, start, end, _total = _slice_text(
            text, check.start_line, check.end_line, context_lines
        )
        entry.update({"start_line": start, "end_line": end, "verified": check.verified})

        numbered = _number_lines(source, start)
        if i > 0 and len(numbered) > remaining:
            entry["fetch_with"] = f"{row.file_path}:{start}-{end}"
            not_rendered.append(entry)
            continue
        remaining -= len(numbered)
        entry["source"] = numbered
        candidates.append(entry)

    response: dict[str, Any] = {
        "symbol_id": symbol_id,
        "ambiguous": True,
        "match_count": len(rows),
        "candidates": candidates,
        "note": (
            f"{len(rows)} symbols match this id (overloads, re-exports, or "
            "conditional definitions). All candidate bodies are included — "
            "none was silently chosen; pick by signature and line range."
        ),
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            repository=repository,
            targets=sorted({r.file_path for r in rows}),
        ),
    }
    if not_rendered:
        response["not_rendered"] = not_rendered
        response["note"] += (
            " Candidates over the response budget are listed in not_rendered —"
            " fetch one via get_symbol with its fetch_with range."
        )

    from repowise.core.distill.budget import estimate_tokens
    from repowise.server.mcp_server._savings import declare_replaced

    full_tokens = sum(estimate_tokens(t) for t in text_cache.values() if t)
    if full_tokens:
        declare_replaced(response, full_tokens)
    return response


@mcp.tool()
async def get_symbol(
    symbol_id: str | None = None,
    context_lines: int = 0,
    repo: str | None = None,
    query: str | None = None,
    id: str | None = None,
    depth: int = 1,
) -> dict:
    """Follow-up read of one symbol whose id another response already gave you.

    **Not an entry point.** ``get_answer`` already ships ``symbol_bodies``, and
    for a whole file ``get_context(include=["skeleton"])`` or a plain Read is
    one call instead of many. Reach here for a body that was elided, or for a
    ``continuation`` / omission ref. Never walk a file symbol by symbol.

    Raw source of one indexed symbol, bounded (~600 lines). ``source`` uses
    Read's exact line-numbered format; treat it as an already-performed Read.
    ``verified: true`` = bounds checked (or corrected) against the live file:
    no follow-up Read needed. ``bounds: "approximate"`` = the symbol moved and
    re-location failed. An ambiguous id (overloads, re-exports) returns ALL
    matching bodies in ``candidates``; none is silently chosen. Also serves
    live range reads ("path.py:140-180", ≤200 lines, always verified) and
    omission refs ("repowise#<12-hex>"). An index miss returns fallback_lines
    from a live grep rather than a dead end. When ``truncated`` is true the
    response carries a ``continuation`` token: the exact range read that
    fetches the remainder; pass it straight back to get_symbol.

    Args:
        symbol_id: "path/to/file.py::Name", "path/to/file.py:140-180" for a
            live range, or an omission ref.
        context_lines: extra lines before/after (0-50).
        repo: usually omitted.
        query: omission refs only, regex/substring filter on lines.
        id: accepted alias for ``symbol_id``.
        depth: 1 (default) is this symbol alone; 2-3 also returns the bodies
            it calls, transitively, in ``callee_bodies``.
    """
    if repo == "all":
        return _unsupported_repo_all("get_symbol")
    ctx = await _resolve_repo_context(repo)

    t0 = time.perf_counter()
    # ``id`` is an alias for ``symbol_id``. The CLAUDE.md tool table and the
    # tool description both refer to this tool as ``get_symbol(id)``, so agents
    # naturally call it with ``id=`` and hit a pydantic "field required" error
    # on ``symbol_id`` — one isError early teaches the agent to abandon the
    # server (agent-context doctrine). Accept both; ``symbol_id`` wins when both
    # are given.
    if not symbol_id and id:
        symbol_id = id
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
    # Clamp rather than reject: an out-of-range depth is a caller reaching for
    # more of the chain, and the useful reply is the deepest walk we will do.
    depth = max(1, min(_MAX_CALLEE_DEPTH, depth))

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        rows = await _resolve_symbol(session, repository.id, symbol_id)

    exclude_spec = _get_exclude_spec(ctx.path)
    rows = [r for r in rows if not is_excluded(r.file_path, exclude_spec)]
    if not rows:
        # Dead-end recovery: constants/imports/aliases between indexed
        # symbols miss the index but live in the file — grep the live file
        # for the name and serve the matching lines instead of a pure error.
        if ctx.path:
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
                            targets=[file_part],
                        ),
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

    if len(rows) > 1:
        return await _render_ambiguous(rows, symbol_id, ctx, repository, t0, context_lines)

    row = rows[0]
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
        "source": _number_lines(source, start),
        "truncated": truncated,
        "verified": check.verified,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_symbol_hint(row.symbol_id, check.end_line, check.start_line),
            repository=repository,
            # Served bytes are live-verified; only this file's drift matters.
            targets=[row.file_path],
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
    if depth > 1:
        async with get_session(ctx.session_factory) as session:
            callee_block = await _expand_callees(
                session, repository.id, row, repo_root, depth, exclude_spec
            )
        if callee_block is not None:
            response["callee_bodies"] = callee_block

    # Declare the exact counterfactual: serving one symbol replaced Reading the
    # whole file. The savings instrumentation prefers this over its estimator.
    from repowise.server.mcp_server._savings import declare_replaced

    declare_replaced(response, full_tokens)
    return response
