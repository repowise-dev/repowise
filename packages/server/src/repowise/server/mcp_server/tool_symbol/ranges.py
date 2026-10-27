"""Live line-range reads: ``path/to/file.py:140-180`` ids."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from repowise.core.persistence.database import get_session

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import completeness_line as _completeness_line
from repowise.server.mcp_server._references import (
    path_identity,
    source_reference,
)

from .text import _number_lines, _read_file_text, _slice_text

# Range-read dispatch: "path/to/file.py:140-180". A single colon followed by
# a numeric range never collides with "{path}::{name}" (double colon) or an
# omission ref. The escape hatch for everything not indexed as a symbol —
# imports, module docstrings, decorators between symbols — without falling
# back to a whole-file Read.
_RANGE_ID_RE = re.compile(r"^(?P<path>.+?):(?P<start>\d+)-(?P<end>\d+)$")

# Range reads are bounded tighter than symbol reads: the caller names exact
# lines, so a large range is a deliberate choice to cap.
_MAX_RANGE_LINES = 200


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

    # Before the exclusion spec: it is built from the repo path.
    if not ctx.path:
        return _err("MCP server has no repo path configured")
    if _pkg.is_excluded(path, _get_exclude_spec(ctx.path)):
        return _err(f"'{path}' is excluded from indexing.")

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
    if s == 1 and e == total and not range_truncated:
        # Only a range that covers the file end to end is a whole unit; any
        # other slice leaves lines the caller still has to go and read.
        response["_meta"]["complete"] = _completeness_line(files=1)
    remainder_end = min(requested_end, total)
    if range_truncated and e < remainder_end:
        # Same clean-continuation contract as a truncated symbol read: name the
        # exact next range instead of leaving the agent to guess it.
        continuation_reference = source_reference(
            ctx.alias,
            path,
            lines=[e + 1, remainder_end],
            verification_basis="live",
            source_kind="source",
        )
        response["continuation"] = continuation_reference["id"]
        response["continuation_reference"] = continuation_reference
        response["note"] = (
            f"Range capped at {_MAX_RANGE_LINES} lines; served {s}-{e}. "
            f"Continue in one call: get_symbol({response['continuation']!r})."
        )
    from repowise.server.mcp_server._savings import declare_replaced

    declare_replaced(response, full_tokens)
    return response


async def _range_id_read(symbol_id: str, context_lines: int, ctx: Any, t0: float) -> dict | None:
    """A live range read for a ``path:start-end`` id, else None."""
    range_match = _RANGE_ID_RE.match(symbol_id.strip())
    if not range_match or "::" in symbol_id:
        return None
    normalized_path = path_identity(range_match.group("path"))
    range_start = int(range_match.group("start"))
    range_end = int(range_match.group("end"))
    if range_end < range_start:
        range_start, range_end = range_end, range_start
    normalized_id = f"{normalized_path}:{range_start}-{range_end}"
    return await _resolve_range_read(
        normalized_id,
        normalized_path,
        range_start,
        range_end,
        max(0, min(50, context_lines)),
        ctx,
        t0,
    )
