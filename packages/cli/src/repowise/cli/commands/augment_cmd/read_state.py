"""PostToolUse Read/Edit/Write per-session read intelligence.

A small JSON state file under .repowise/ tracks which files this agent
session has Read and Edited, keyed by the hook payload's session_id. Two
behaviors ride on it:

  * Stale-read notice — a Read of a file whose previous Read predates a
    recorded Edit/Write gets a one-line "earlier excerpts are stale" flag.
  * Skeleton nudge — a large Read of an indexed file gets a pointer at
    get_context(include=["skeleton"]) with a bounds-arithmetic estimate.

Rate limiting is the state file itself (per-file, per-session lists), NOT
the _claim_emission temp marker — that TTL-based dedup only suppresses the
two concurrently-registered hooks racing on one tool event, which still
applies on top. A new session_id resets the state.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from ._shared import _extract_output_text, _find_repo_root, _relativize

_READ_NUDGE_MIN_LINES = 100  # Read output lines before a skeleton nudge
_READ_NUDGE_MIN_TOKENS = 3000  # full-file tokens below which a nudge is noise
_READ_NUDGE_MIN_SAVINGS = 1500  # estimated tokens saved must clear this
_READ_NUDGE_MAX_RATIO = 0.5  # skeleton must be at most this fraction of full


def _session_state_path(repo_path: Path) -> Path:
    return Path(repo_path) / ".repowise" / ".augment-session.json"


def _load_session_state(repo_path: Path, session_id: str) -> dict:
    """Load the per-session read/edit state, resetting on session change.

    ``reads``/``edits`` map repo-relative paths to a monotonically increasing
    per-session sequence number (``seq``) rather than wall-clock time — two
    hook events can land within one clock tick on Windows, and ordering is
    the only thing the stale-read comparison needs.
    """
    fresh = {
        "session_id": session_id,
        "seq": 0,
        "reads": {},
        "edits": {},
        "nudged": [],
        "stale_notified": [],
        "reread_notified": [],
        "decisions_shown": [],
    }
    try:
        state = json.loads(_session_state_path(repo_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        return fresh
    for key, default in fresh.items():
        if not isinstance(state.get(key), type(default)):
            state[key] = default
    return state


def _save_session_state(repo_path: Path, state: dict) -> None:
    """Persist session state; trims unbounded growth, never raises."""
    for key in ("reads", "edits"):
        entries = state.get(key, {})
        if len(entries) > 500:
            keep = sorted(entries, key=entries.get, reverse=True)[:400]
            state[key] = {k: entries[k] for k in keep}
    with contextlib.suppress(OSError, TypeError, ValueError):
        _session_state_path(repo_path).write_text(json.dumps(state), encoding="utf-8")


def _record_edit(tool_input: dict, cwd: str, session_id: str) -> None:
    """Note an Edit/Write so a later Read of the file can flag staleness."""
    _handle_edit_post(tool_input, cwd, session_id, with_decisions=False)


def _handle_edit_post(
    tool_input: dict,
    cwd: str,
    session_id: str,
    *,
    with_decisions: bool = True,
) -> str | None:
    """Record an Edit/Write; surface the file's governing decision, if any.

    The decision notice fires once per session per decision under a strict
    per-session cap (see :func:`decision_inject._edit_decision_notice`) so a
    governed file gets one heads-up, not a drumbeat. Codex lifecycle hooks
    call this with ``with_decisions=False``: they get their own edit banner.
    """
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return None
    state = _load_session_state(repo_path, session_id)
    state["seq"] += 1
    state["edits"][rel] = state["seq"]

    notice: str | None = None
    if with_decisions:
        from .decision_inject import _edit_decision_notice

        try:
            notice = _edit_decision_notice(repo_path, rel, session_id, state)
        except Exception:
            notice = None

    _save_session_state(repo_path, state)
    return notice


def _handle_read_post(
    tool_input: dict,
    tool_output: object,
    cwd: str,
    session_id: str,
) -> str | None:
    """Stale-read notice + skeleton nudge for a completed Read."""
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return None

    state = _load_session_state(repo_path, session_id)
    notices: list[str] = []

    # Stale-read: this session Read the file, then Edited/Wrote it, and is
    # now Reading it again. The fresh Read is fine — the flag is about any
    # reasoning still anchored on the pre-edit excerpt.
    last_read = state["reads"].get(rel)
    last_edit = state["edits"].get(rel)
    edited_since_read = last_read is not None and last_edit is not None and last_read < last_edit
    if edited_since_read and rel not in state["stale_notified"]:
        state["stale_notified"].append(rel)
        notices.append(
            f"[repowise] {rel} changed (Edit/Write) after your previous read of it — "
            "excerpts from before that edit are stale."
        )

    # Re-read: already read this session, unchanged since (the read→edit→read
    # case is the stale notice above), and this is a full re-read of a
    # non-trivial file — the content is still in context, so re-reading the
    # whole file just re-bills it. A targeted range re-read is the behavior
    # we'd recommend, so it is deliberately not flagged; the line floor (the
    # skeleton nudge's) keeps small files from generating noise.
    partial_read = isinstance(tool_input, dict) and (
        tool_input.get("offset") is not None or tool_input.get("limit") is not None
    )
    if (
        last_read is not None
        and not edited_since_read
        and not partial_read
        and rel not in state["reread_notified"]
        and _read_output_line_count(tool_output) >= _READ_NUDGE_MIN_LINES
    ):
        state["reread_notified"].append(rel)
        notices.append(
            f"[repowise] You already read {rel} this session and it is unchanged — "
            "its content is still in context. For a specific symbol use "
            f'get_symbol("{rel}::Name") or a line-range read instead of re-reading the file.'
        )

    state["seq"] += 1
    state["reads"][rel] = state["seq"]

    nudge = _skeleton_nudge(repo_path, rel, tool_output, state)
    if nudge:
        notices.append(nudge)

    _save_session_state(repo_path, state)
    return "\n".join(notices) if notices else None


def _read_output_line_count(tool_output: object) -> int:
    """Line count of a Read result across the hook payload shapes we see."""
    if isinstance(tool_output, dict):
        file_block = tool_output.get("file")
        if isinstance(file_block, dict):
            n = file_block.get("numLines")
            if isinstance(n, int):
                return n
            content = file_block.get("content")
            if isinstance(content, str):
                return content.count("\n") + 1
    text = _extract_output_text(tool_output)
    return (text.count("\n") + 1) if text.strip() else 0


def _skeleton_nudge(repo_path: Path, rel: str, tool_output: object, state: dict) -> str | None:
    """One-line skeleton pointer for a large Read of an indexed file.

    Cheap by construction: bails before any non-stdlib import when the repo
    has no wiki.db, and the size estimate is pure bounds arithmetic — the
    skeleton itself is never rendered on the hook path.
    """
    if rel in state["nudged"]:
        return None
    if _read_output_line_count(tool_output) < _READ_NUDGE_MIN_LINES:
        return None
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None

    bounds = _file_symbol_bounds(db_path, rel)
    if not bounds:
        return None
    try:
        size = (repo_path / rel).stat().st_size
    except OSError:
        return None
    full_tokens = size // 4
    if full_tokens < _READ_NUDGE_MIN_TOKENS:
        return None

    from repowise.core.distill.skeleton import estimate_skeleton_tokens

    skeleton_tokens = estimate_skeleton_tokens(bounds, file_size_bytes=size)
    if skeleton_tokens > full_tokens * _READ_NUDGE_MAX_RATIO:
        return None
    # A nudge is only worth the agent's attention when acting on it buys a
    # real saving — a few hundred tokens on a mid-size file is noise.
    if full_tokens - skeleton_tokens < _READ_NUDGE_MIN_SAVINGS:
        return None

    state["nudged"].append(rel)
    return (
        f"[repowise] A skeleton of {rel} is ~{skeleton_tokens} tokens vs ~{full_tokens} "
        f'for the full file. For structure-level questions use get_context(["{rel}"], '
        'include=["skeleton"]).'
    )


def _file_symbol_bounds(db_path: Path, rel: str) -> list[tuple[int, int]]:
    """Persisted (start_line, end_line) pairs for one file, or [] on any miss.

    Direct read-only stdlib sqlite3 — the hook path must not pay the
    sqlalchemy import for two integers per symbol.
    """
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(
                "SELECT start_line, end_line FROM wiki_symbols WHERE file_path = ?",
                (rel,),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [(s, e) for s, e in rows if isinstance(s, int) and isinstance(e, int) and s > 0]
