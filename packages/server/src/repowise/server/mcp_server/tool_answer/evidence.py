"""Live-source evidence get_answer attaches beside the prose.

Rationale comments mined out of the candidate files, the de-duplication that
keeps one comment from reaching the payload twice, and the resolvability check
every advertised ``symbol_id`` has to pass. None of it needs a provider, which
is why the degraded paths use it too.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._helpers import is_excluded
from repowise.server.mcp_server._symbol_lookup import (
    bare_name,
    parse_symbol_id,
    resolve_symbol_rows,
)
from repowise.server.mcp_server.tool_answer.symbols import _read_repo_text


def _repo_root(ctx) -> Path | None:
    """The checkout root as a Path, or None when the context carries no path.

    Every live source read is anchored on it, and a context without a path is
    the repo-less case those reads must skip rather than guess at.
    """
    return Path(str(ctx.path)) if getattr(ctx, "path", None) else None


def _is_readable_path(target: str) -> bool:
    """Whether a fallback_target is a file the agent can actually Read.

    Non-file graph nodes (community/SCC nodes, architectural layers) can ride in
    on retrieval hits with a ``target_path`` like ``"scc-607"`` or
    ``"layer:application"``: internal ids with no path separator and no file
    extension. An agent handed one in ``fallback_targets`` will try to Read it
    and dead-end, so keep only path-shaped entries.
    """
    t = (target or "").strip()
    if not t:
        return False
    if "/" in t or "\\" in t:
        return True
    dot = t.rfind(".")
    ext = t[dot + 1 :] if dot != -1 else ""
    return bool(ext) and ext.isalnum() and len(ext) <= 6


async def _first_resolvable_id(ids: list[str], ctx, repository, exclude_spec) -> str | None:
    """The first id ``get_symbol`` would actually answer, or None if none would.

    The regex scanner can name a non-symbol, and a next action must never
    dead-end. ``get_symbol`` answers from an indexed row or a live grep of the
    file, so either counts as resolved. An unreadable file is KEPT (absence of
    evidence); only a read lacking the name, with no index row, disqualifies.
    """
    repo_root = _repo_root(ctx)
    for sid in ids:
        file_part, name_part = parse_symbol_id(sid)
        if not file_part or not name_part:
            continue
        if is_excluded(file_part, exclude_spec):
            # get_symbol refuses excluded paths outright, index row or not.
            continue
        text = _read_repo_text(repo_root, file_part)
        if text is None or bare_name(name_part) in text:
            return sid
        # The name is provably absent from the live file, so the live-grep leg
        # cannot fire. An indexed row is the only way get_symbol still answers.
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                if await resolve_symbol_rows(session, repository.id, sid):
                    return sid
    return None


def _rationale_anchors(h: dict) -> list[tuple[str, int | None]]:
    """One ``(path, near-line)`` pair per reason this hit is worth scanning.

    A concept-anchored file leads, at its grep match line. Each anchored or
    matched symbol repeats the path at its definition line, so a file appears
    once per reason: the weighting the scan orders on.
    """
    path = h.get("target_path")
    if not path:
        return []
    anchors: list[tuple[str, int | None]] = []
    if h.get("_concept_anchored"):
        anchors.append((path, h.get("_concept_near_line")))
    for s in (h.get("_anchor_symbols") or []) + [
        s for s in (h.get("symbols") or []) if s.get("_matched")
    ]:
        anchors.append((path, s.get("start_line")))
    return anchors


async def _gather_code_rationale(ctx, hits: list[dict], fallback_targets: list[str], question: str):
    """Mine in-code rationale comments for a low-confidence answer.

    The "why" may be a plain code comment the wiki missed. Scans anchored and
    matched files first, then fallback_targets, for rationale comments
    overlapping the question. Best-effort ([] on failure) and off the event
    loop, so concurrent requests do not pay for it.
    """
    repo_root = getattr(ctx, "path", None)
    if not repo_root:
        return []
    candidates: list[str] = []
    near_lines: dict[str, int] = {}
    for h in hits or []:
        for path, near_line in _rationale_anchors(h):
            candidates.append(path)
            # First mention of a path wins its near-line boost.
            if near_line and path not in near_lines:
                near_lines[path] = near_line
    candidates.extend(p for p in (fallback_targets or []) if p)
    try:
        return await asyncio.to_thread(
            _mine_rationale, repo_root, candidates, question, near_lines=near_lines
        )
    except Exception:  # best-effort enrichment, never break the response
        return []


def _drop_already_surfaced(rationale: list[dict], *surfaced: list[dict]) -> list[dict]:
    """Drop mined rationale comments already shown elsewhere in the response.

    Drops any mined comment whose ``(path, line-range)`` overlaps a surfaced
    entry (a body, quote or line-ranged citation). Entries without a
    ``(path, lines)`` pair are ignored.
    """
    occupied = [span for entries in surfaced for span in map(_line_span, entries or []) if span]
    if not occupied:
        return rationale
    return [r for r in rationale if not _overlaps_any(_line_span(r), occupied)]


def _line_span(entry: dict) -> tuple[str, int, int] | None:
    """The ``(path, start, end)`` an entry occupies, or None if it names none.

    Entries without a usable ``(path, lines)`` pair cannot be compared for
    overlap in either direction, so they neither occupy space nor get dropped.
    """
    path = entry.get("path")
    lines = entry.get("lines")
    if path and isinstance(lines, (list, tuple)) and len(lines) == 2:
        return (path, lines[0], lines[1])
    return None


def _overlaps_any(span: tuple[str, int, int] | None, occupied: list[tuple[str, int, int]]) -> bool:
    """Whether ``span`` shares a file and any line with something already shown."""
    if span is None:
        return False
    path, start, end = span
    return any(p == path and not (end < s or start > e) for p, s, e in occupied)
