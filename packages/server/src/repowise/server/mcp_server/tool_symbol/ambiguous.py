"""Ambiguous ids: every candidate body served in one response."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from repowise.core.persistence.models import WikiSymbol

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._budget import register_post_shed
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import completeness_line as _completeness_line
from repowise.server.mcp_server._references import (
    source_reference,
    symbol_identity,
)
from repowise.server.mcp_server._verify import (
    check_symbol_bounds,
    heal_symbol_row,
)

from .text import _number_lines, _read_file_text, _slice_text

# When a lookup is ambiguous (overloads, re-exports, conditional defs) every
# candidate body is served in ONE response — a silently-picked wrong candidate
# sends the agent into a read-spiral that costs far more than the extra bytes.
# This caps the total candidate-source chars; the first candidate always
# renders, the rest render while budget remains and are otherwise listed with
# an exact range read that fetches them.
_AMBIGUITY_CHAR_BUDGET = 20_000


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
    remaining = _pkg._AMBIGUITY_CHAR_BUDGET
    whole_bodies = 0

    for i, row in enumerate(rows):
        if row.file_path not in text_cache:
            text_cache[row.file_path] = _read_file_text(repo_root, row.file_path)
        text = text_cache[row.file_path]

        entry: dict[str, Any] = {
            "symbol_id": symbol_identity(row.symbol_id),
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
            fetch_reference = source_reference(
                ctx.alias,
                row.file_path,
                lines=[start, end],
                verification_basis="live",
                source_kind="source",
            )
            entry["fetch_with"] = fetch_reference["id"]
            entry["fetch_reference"] = fetch_reference
            not_rendered.append(entry)
            continue
        remaining -= len(numbered)
        entry["source"] = numbered
        candidates.append(entry)
        # A candidate is whole only when the served span reaches both ends of
        # the verified bounds; a budget-clipped or relocated one does not count.
        if check.verified and start <= check.start_line and end >= check.end_line:
            whole_bodies += 1

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
    _finish_ambiguous(response, whole_bodies, not_rendered, text_cache)
    return response


def _finish_ambiguous(
    response: dict[str, Any],
    whole_bodies: int,
    not_rendered: list[dict],
    text_cache: dict[str, str | None],
) -> None:
    """Completeness, the not-rendered list and the replaced-read savings."""
    if whole_bodies:
        response["_meta"]["complete"] = _completeness_line(bodies=whole_bodies)
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


def _correct_ambiguity_note(result: dict[str, Any], _collector: Any) -> None:
    """Stop promising every candidate body once the budget has cut some.

    The ambiguous shape exists so a wrong pick is never made silently, and says
    so in ``note``. A shed that leaves the note standing makes exactly the
    claim the shape was built to avoid.
    """
    if not result.get("ambiguous"):
        return
    emitted = result.get("candidates_emitted")
    if emitted is None or emitted >= (result.get("match_count") or 0):
        return
    result["note"] = (
        f"{result.get('match_count')} symbols match this id (overloads, "
        f"re-exports, or conditional definitions). {emitted} candidate bodies "
        "fit this response; the rest are recoverable from the omission ref, or "
        "fetch one directly with its file range."
    )


register_post_shed("get_symbol", _correct_ambiguity_note)
