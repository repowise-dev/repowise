"""Stop hook: ask the agent, once a session, to record a decision it reasoned out.

The commit-time capture prompt reaches only sessions that commit, and most
durable choices are made in sessions that never do: research, review and
coordination. This asks at the one moment every session reaches, the stop.

Opt-in per repository, ``decisions.stop_prompt`` (off by default). Claude Code
shows a Stop hook's text to the model only when the hook blocks the stop, so
this blocks it once with a short reason, and the agent either records the
decision or says there is none and stops again.

Gates, cheapest first, every one silent when it fails:

1. A ``Stop`` event with a session id, and ``stop_hook_active`` false: the
   stop that follows a block carries it, and answering that one would loop.
2. A ``.repowise/config.yaml`` that names ``stop_prompt`` at all. Read as text,
   before any import past :mod:`repowise.cli._helpers.paths`, so a repository
   with the switch off pays one file read.
3. The resolved decision policy has it on (mode ``off`` and ``enabled: false``
   silence it).
4. The session has not been asked yet (the shared hook-session state).
5. The session did real work: an Edit or Write, or at least
   :data:`MIN_TOOL_CALLS` tool calls the hooks counted (``hook_runs``).
6. Nothing recorded a decision since the session started.

The ledger row is the atomic once-per-session claim, so two installed copies
of this hook (the plugin's and ``settings.json``'s) cannot both block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

#: The setting's leaf name, also the text pre-check in the config file.
SWITCH = "stop_prompt"

#: Tool calls the hooks counted before a session without an edit counts as
#: work. A quick lookup or a one-question session stays unasked; the session
#: that read, searched and reasoned for a while does not. On this repository's
#: own ledger, 255 of 281 sessions clear it, 244 of them by an edit alone.
MIN_TOOL_CALLS = 20

#: How far into the transcript to look for the session's first timestamp.
_HEAD_LINES = 50

REASON = (
    "[repowise] Before you stop: if this session settled a durable choice a future "
    "change should follow, record it now:\n"
    "  repowise decision add --agent <your slug> --decision '<what you chose>' "
    "--rationale '<why, and what you rejected>' --affects <path>\n"
    'If there is none, reply "No decision to record." and stop. Asked once a session.'
)


def run(stdin: TextIO, out: TextIO) -> None:
    """Read one Stop payload from *stdin*; write a block to *out* when the gates pass."""
    try:
        payload = json.loads(stdin.read() or "{}")
    except ValueError:
        return
    reason = block_reason(payload) if isinstance(payload, dict) else None
    if reason:
        out.write(json.dumps({"decision": "block", "reason": reason}))
        out.flush()


def block_reason(payload: dict) -> str | None:
    """The reason to block this stop with, or ``None`` to let it through."""
    if payload.get("hook_event_name") != "Stop" or payload.get("stop_hook_active"):
        return None
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str):
        return None
    try:
        if not _config_names_switch(cwd):
            return None
        return _ask(cwd, session_id, payload.get("transcript_path"))
    except Exception:
        # A hook never fails the agent: an unreadable config, index or ledger
        # lets the stop through, the cheaper error.
        return None


def _config_names_switch(cwd: str) -> bool:
    """Whether the config answering for *cwd* mentions the switch at all.

    A rule-out filter, not the answer: the walk is the short form of
    ``augment_cmd._shared._find_roots``, which importing would cost the whole
    augment package on every stop. The policy read in :func:`_ask` decides.
    """
    from repowise.cli._helpers.paths import borrowed_index_base

    current = Path(cwd).resolve()
    for _ in range(20):
        if (current / ".repowise").is_dir():
            return _mentions(current)
        base = borrowed_index_base(current)
        if base is not None:
            return _mentions(base)
        if current.parent == current:
            return False
        current = current.parent
    return False


def _mentions(repo: Path) -> bool:
    try:
        return SWITCH in (repo / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False


def _ask(cwd: str, session_id: str, transcript_path: object) -> str | None:
    from repowise.cli.commands.augment_cmd._shared import _find_repo_root

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None or not _policy_on(repo_path):
        return None

    from repowise.cli.commands.augment_cmd.read_state import (
        _load_shared_state,
        _save_shared_state,
    )

    state = _load_shared_state(repo_path, session_id)
    if state.get("stop_prompted"):
        return None
    if not did_work(repo_path, session_id):
        return None
    started = session_started(transcript_path)
    if started is None or recorded_since(repo_path, started):
        return None

    from repowise.cli.commands.augment_cmd._shared import _ledger_key
    from repowise.cli.hook_ledger import _claim_ledger

    claimed, _ = _claim_ledger(
        repo_path,
        session_id,
        _ledger_key("capture", SWITCH, REASON),
        node_id="",
        surface="capture",
        category=SWITCH,
        chars=len(REASON),
    )
    if not claimed:
        return None
    state["stop_prompted"] = True
    _save_shared_state(repo_path, state)
    return REASON


def _policy_on(repo_path: Path) -> bool:
    from repowise.core.analysis.decisions.policy_store import load_policy

    return load_policy(repo_path).policy.stop_capture


def did_work(repo_path: Path, session_id: str) -> bool:
    """An Edit or Write, or :data:`MIN_TOOL_CALLS` hook-counted tool calls, this session.

    Read from the ``hook_runs`` counter every augment hook writes, subagents
    included, since they share the session id. No ledger reads as no work.
    """
    import sqlite3

    from repowise.cli.agent_adapters import adapter_for
    from repowise.cli.hook_ledger import _open_injections

    conn = _open_injections(repo_path)
    if conn is None:
        return False
    try:
        rows = conn.execute(
            "SELECT tool, SUM(calls) FROM hook_runs "
            "WHERE session_id = ? AND event = 'PostToolUse' GROUP BY tool",
            (session_id,),
        ).fetchall()
    except sqlite3.Error:
        return False
    edits = adapter_for(None).edit_tool_names
    if any(tool in edits and calls for tool, calls in rows):
        return True
    return sum(calls or 0 for _, calls in rows) >= MIN_TOOL_CALLS


def session_started(transcript_path: object) -> str | None:
    """The session's first transcript timestamp, as ``YYYY-MM-DD HH:MM:SS`` UTC.

    The form ``decision_records.created_at`` is stored in, so the two compare
    as strings. ``None`` when the transcript cannot tell, which keeps this quiet.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as handle:
            stamps = (
                _line_stamp(line) for _, line in zip(range(_HEAD_LINES), handle, strict=False)
            )
            stamp = next((s for s in stamps if s), None)
        return _as_utc(stamp) if stamp else None
    except (OSError, ValueError):
        return None


def _line_stamp(line: str) -> str | None:
    try:
        stamp = json.loads(line).get("timestamp")
    except (ValueError, AttributeError):
        return None
    return stamp if isinstance(stamp, str) else None


def _as_utc(stamp: str) -> str:
    from datetime import UTC, datetime

    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def recorded_since(repo_path: Path, started: str) -> bool:
    """Whether an agent or a person recorded a decision here since *started*.

    Any session's record counts, so a concurrent session's can quiet this one;
    a missed ask is the cheaper error. Fails closed, into silence.
    """
    import sqlite3

    from repowise.cli.commands.augment_cmd.decision_inject import _open_wiki_ro

    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return True
    try:
        row = conn.execute(
            "SELECT 1 FROM decision_records "
            "WHERE source IN ('agent', 'cli') AND created_at >= ? LIMIT 1",
            (started,),
        ).fetchone()
    except sqlite3.Error:
        return True
    finally:
        conn.close()
    return row is not None
