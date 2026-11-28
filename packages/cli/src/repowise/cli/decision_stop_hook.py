"""Stop hook: ask the agent to record a decision it reasoned out, after real work.

The commit-time capture prompt reaches only sessions that commit, and most
durable choices are made in sessions that never do: research, review and
coordination. This asks at the one moment every session reaches, the stop.

Opt-in per repository, ``decisions.stop_prompt`` (off by default). Claude Code
fires Stop at the end of every turn, not once at session end, so the ask is
paced by work: it fires only once the session made enough tool calls since the
last ask. The text goes out as the Stop event's ``additionalContext``, which
Claude Code hands to the model and continues the turn on, without the
"blocking error" label a ``decision: block`` gets.

Gates, cheapest first, every one silent when it fails:

1. A ``Stop`` event with a session id, and ``stop_hook_active`` false: the
   stop that follows an ask carries it, and answering that one would loop.
2. A ``.repowise/config.yaml`` that names ``stop_prompt`` at all. Read as text,
   before any import past :mod:`repowise.cli._helpers.paths`, so a repository
   with the switch off pays one file read.
3. The resolved decision policy has it on (mode ``off`` and ``enabled: false``
   silence it).
4. Fewer than :data:`MAX_ASKS` asks this session so far.
5. Enough work since the last ask (or since the session started): the
   transcript shows at least :data:`MIN_TOOL_CALLS` tool calls, and each later
   ask needs :data:`SPACING` times the gap before it. A turn that made no tool
   call never crosses it, so a trivial turn stays silent. Counted from the
   transcript, read on from where the last stop left off, so two installed
   copies of the tool hooks cannot double the count.
6. Nothing recorded a decision since the last ask (or since the session
   started). A record moves the mark instead, so the next ask waits for more
   work after it.

The ledger row, one per ask number, is the atomic claim, so two installed
copies of this hook (the plugin's and ``settings.json``'s) cannot both ask.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

#: The setting's leaf name, also the text pre-check in the config file.
SWITCH = "stop_prompt"

#: Tool calls in the session's transcript before the first ask. A quick lookup or a one-question session stays unasked.
MIN_TOOL_CALLS = 20

#: Each later ask needs this many times the previous gap, so a long session is
#: asked after 20, 80 and 260 calls rather than three times close together.
SPACING = 3

#: Asks a session at most. Over 130 of this repository's own session
#: transcripts, the rule asks 23 sessions never, 15 once, 41 twice and 51
#: three times.
MAX_ASKS = 3

#: A tool call as the transcript serializes it. Inside a tool result the same
#: text is escaped, so only real calls match.
_TOOL_USE = b'"type":"tool_use"'

#: How far into the transcript to look for the session's first timestamp.
_HEAD_LINES = 50

REASON = (
    "[repowise] If the work since the last check settled a durable choice a future "
    "change should follow, record it now:\n"
    "  repowise decision add --agent <your slug> --decision '<what you chose>' "
    "--rationale '<why, and what you rejected>' --affects <path>\n"
    'If there is none, reply "No decision to record." and stop.'
)


def run(stdin: TextIO, out: TextIO) -> None:
    """Read one Stop payload from *stdin*; write the ask to *out* when the gates pass."""
    try:
        payload = json.loads(stdin.read() or "{}")
    except ValueError:
        return
    reason = block_reason(payload) if isinstance(payload, dict) else None
    if reason:
        # additionalContext, not ``decision: block``: Claude Code continues the
        # turn on either, but labels a block "Stop hook blocking error".
        response = {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": reason}}
        out.write(json.dumps(response))
        out.flush()


def block_reason(payload: dict) -> str | None:
    """The text to ask with at this stop, or ``None`` to stay silent."""
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
    asks = _int(state.get("stop_asks"))
    if asks >= MAX_ASKS:
        return None
    seen = _int(state.get("stop_offset"))
    calls = tool_calls(transcript_path, state)
    if calls - _int(state.get("stop_mark")) < MIN_TOOL_CALLS * SPACING**asks:
        if state.get("stop_offset") != seen:
            _save_shared_state(repo_path, state)
        return None
    since = state.get("stop_mark_at")
    if not isinstance(since, str) or not since:
        since = session_started(transcript_path)
    recorded = None if since is None else recorded_since(repo_path, since)
    if recorded is None:
        return None
    if recorded:
        # Recorded unasked: start the next gap from here, without spending an ask.
        _mark(repo_path, state, calls)
        return None

    from repowise.cli.commands.augment_cmd._shared import _ledger_key
    from repowise.cli.hook_ledger import _claim_ledger

    claimed, _ = _claim_ledger(
        repo_path,
        session_id,
        # One row per ask number: the key is the claim, so each ask needs its own.
        _ledger_key("capture", SWITCH, f"{REASON}#{asks + 1}"),
        node_id="",
        surface="capture",
        category=SWITCH,
        chars=len(REASON),
    )
    state["stop_asks"] = asks + 1
    _mark(repo_path, state, calls)
    # Unclaimed means this ask already went out, from a concurrent copy or a
    # run whose state write was lost: advancing the state above heals that.
    return REASON if claimed else None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _mark(repo_path: Path, state: dict, calls: int) -> None:
    """Start the next gap at *calls*, and the record check at now."""
    from datetime import UTC, datetime

    from repowise.cli.commands.augment_cmd.read_state import _save_shared_state

    state["stop_mark"] = calls
    state["stop_mark_at"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    _save_shared_state(repo_path, state)


def _policy_on(repo_path: Path) -> bool:
    from repowise.core.analysis.decisions.policy_store import load_policy

    return load_policy(repo_path).policy.stop_capture


def tool_calls(transcript_path: object, state: dict) -> int:
    """Tool calls in the main session's transcript so far.

    Reads only what was appended since the last stop (``stop_offset``, up to
    the last whole line) and keeps the running count in *state*
    (``stop_calls``), so a long session costs its new lines, not the whole
    file. An unreadable transcript adds nothing.
    """
    seen = _int(state.get("stop_offset"))
    calls = _int(state.get("stop_calls"))
    if not isinstance(transcript_path, str) or not transcript_path:
        return calls
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(seen)
            chunk = handle.read()
    except OSError:
        return calls
    whole = chunk.rfind(b"\n") + 1
    if whole:
        calls += chunk[:whole].count(_TOOL_USE)
        state["stop_offset"] = seen + whole
        state["stop_calls"] = calls
    return calls


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


def recorded_since(repo_path: Path, started: str) -> bool | None:
    """Whether an agent or a person recorded a decision here since *started* (UTC).

    Any session's record counts, so a concurrent session's can quiet this one;
    a missed ask is the cheaper error. ``None`` when the store cannot tell,
    which keeps this quiet without moving the mark.
    """
    import sqlite3

    from repowise.cli.commands.augment_cmd.decision_inject import _open_wiki_ro

    conn = _open_wiki_ro(repo_path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM decision_records "
            "WHERE source IN ('agent', 'cli') AND created_at >= ? LIMIT 1",
            (started,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row is not None
