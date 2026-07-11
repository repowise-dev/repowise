"""Read-after-served tracking: the MCP adoption KPI, measured at the hook.

When a repowise MCP response has already served the content of a file (a
get_symbol body, a range read, get_answer's symbol_bodies/quotes) and the
agent later Reads that same content anyway, that Read is the single most
diagnostic adoption signal we have: served-then-read means the MCP answer
did not land. This module measures it and deliberately injects NOTHING —
nagging at that moment would burn trust for zero benefit.

Two hooks feed it:

  * PostToolUse on repowise MCP tools — record which file line-ranges the
    response actually served (source bytes only; skeletons and signatures
    don't count, a Read after those is expected).
  * PostToolUse on Read — when the served ranges cover most of the Read
    window, log one ``read_after_served`` row per file per session into the
    shared sessions.db ledger (chars=0: measurement, never an injection).

Same operational rules as the rest of augment: stdlib-only on the hook path,
unknown tool-output shapes are skipped rather than guessed, and any failure
degrades to silence.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._shared import _find_repo_root, _relativize
from .decision_inject import _claim_ledger

_SERVED_COVERAGE = 0.8  # served share of a Read window that counts as covered
_MAX_SERVED_FILES = 50  # served-range bookkeeping caps (state-file hygiene)
_MAX_SERVED_RANGES = 40

_SURFACE = "read_enrich"


# ---------------------------------------------------------------------------
# Read side: coverage check + ledger row
# ---------------------------------------------------------------------------


def _log_read_after_served(
    tool_input: dict,
    tool_output: object,
    cwd: str,
    session_id: str,
) -> None:
    """Log a Read whose window the MCP tools already served. Emits nothing."""
    if not session_id:
        return
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return
    window = _read_window(tool_input, tool_output)
    if window is None:
        # Unknown/unextractable Read response shape — skip, never guess.
        return
    start, end = window
    if _served_covers(repo_path, session_id, rel, start, end):
        _claim_ledger(
            repo_path,
            session_id,
            f"{_SURFACE}:read_after_served:{rel}",
            node_id=rel,
            surface=_SURFACE,
            category="read_after_served",
            chars=0,
        )


def _read_window(tool_input: dict, tool_output: object) -> tuple[int, int] | None:
    """(start_line, end_line) of a Read, or None when unknowable."""
    from .read_state import _read_output_line_count

    lines = _read_output_line_count(tool_output)
    if lines <= 0:
        return None
    offset = tool_input.get("offset")
    start = offset if isinstance(offset, int) and offset > 0 else 1
    return start, start + lines - 1


def _served_covers(repo_path: Path, session_id: str, rel: str, start: int, end: int) -> bool:
    """True when served ranges cover >= _SERVED_COVERAGE of the Read window."""
    from .read_state import _load_session_state

    state = _load_session_state(repo_path, session_id)
    served = state.get("served")
    ranges = served.get(rel) if isinstance(served, dict) else None
    if not isinstance(ranges, list) or end < start:
        return False
    spans: list[tuple[int, int]] = []
    for r in ranges:
        if not (isinstance(r, list) and len(r) == 2):
            continue
        s, e = r
        if isinstance(s, int) and isinstance(e, int) and s <= end and e >= start:
            spans.append((max(s, start), min(e, end)))
    covered = 0
    cursor = start - 1
    for s, e in sorted(spans):
        s = max(s, cursor + 1)
        if e > cursor:
            covered += e - s + 1
            cursor = e
    return covered / (end - start + 1) >= _SERVED_COVERAGE


# ---------------------------------------------------------------------------
# MCP side: served-content bookkeeping
# ---------------------------------------------------------------------------


def _handle_mcp_read_post(tool_output: object, cwd: str, session_id: str) -> None:
    """Record file ranges a repowise MCP response served.

    Best-effort bookkeeping in the per-session state file — a lost update
    undercounts the KPI, never spams the agent. Emits nothing.
    """
    if not session_id:
        return
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return
    ranges = _served_ranges(tool_output)
    if not ranges:
        return

    from .read_state import _load_session_state, _save_session_state

    state = _load_session_state(repo_path, session_id)
    served = state.setdefault("served", {})
    for rel, start, end in ranges:
        if rel not in served and len(served) >= _MAX_SERVED_FILES:
            continue
        entries = served.setdefault(rel, [])
        if [start, end] not in entries and len(entries) < _MAX_SERVED_RANGES:
            entries.append([start, end])
    _save_session_state(repo_path, state)


def _served_ranges(tool_output: object) -> list[tuple[str, int, int]]:
    """(rel_path, start_line, end_line) spans whose source text was served.

    Only the response fields known to carry actual source bytes are read:
    get_symbol's top-level/candidates bodies (file + start_line/end_line +
    source) and get_answer's symbol_bodies/quotes (path + lines). Skeletons
    and signatures don't count — a Read after those is expected, not an
    adoption failure. Unknown shapes yield nothing.
    """
    payload = _mcp_payload(tool_output)
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("result"), dict):
        payload = payload["result"]

    out: list[tuple[str, int, int]] = []

    def _add(path: object, start: object, end: object) -> None:
        if (
            isinstance(path, str)
            and path
            and isinstance(start, int)
            and isinstance(end, int)
            and 0 < start <= end
        ):
            out.append((path.replace("\\", "/"), start, end))

    def _body(entry: dict) -> None:
        if entry.get("source") or entry.get("content"):
            _add(entry.get("file"), entry.get("start_line"), entry.get("end_line"))

    _body(payload)
    for entry in payload.get("candidates") or []:
        if isinstance(entry, dict):
            _body(entry)
    for key in ("symbol_bodies", "quotes"):
        for entry in payload.get(key) or []:
            if isinstance(entry, dict) and (entry.get("source") or entry.get("quote")):
                lines = entry.get("lines")
                if isinstance(lines, list) and len(lines) == 2:
                    _add(entry.get("path"), lines[0], lines[1])
    return out


def _mcp_payload(tool_output: object) -> dict | None:
    """The response JSON dict from any of the MCP hook payload shapes."""
    if isinstance(tool_output, dict):
        return tool_output
    text = None
    if isinstance(tool_output, str):
        text = tool_output
    elif isinstance(tool_output, list):
        # Content-block list: [{"type": "text", "text": "{...}"}]
        for item in tool_output:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text = item["text"]
                break
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
