"""``repowise augment`` — hook-driven context enrichment for AI coding agents.

Reads Claude Code or Codex hook payloads from stdin (JSON) and writes
targeted enrichment back as a hook response.

Design philosophy: an enrichment hook is only valuable when it tells the
agent something the raw tool output didn't. Anything else is noise the
agent has to scroll past. So the hook fires on every Grep/Glob/Bash but
returns *nothing* most of the time, and only speaks up when there is
asymmetric, durable value:

  PostToolUse → Grep / Glob
    * Zero-result rescue: grep returned 0 hits but the wiki has a
      semantic match (FTS on docs, fuzzy symbol match, decision record
      mention). Surfaces the closest hit so the agent doesn't burn
      another round on a synonym.
    * Triage on flood: grep returned a large unfocused result set
      (>=_TRIAGE_THRESHOLD lines). Surfaces the top 3 files by
      PageRank so the agent can prioritise. The raw matches are still
      visible — this is just a ranking lens.
    * Skip otherwise: a focused result set means the agent already
      found what it wanted; further graph context is just noise.

Codex SessionStart/UserPromptSubmit: adds short repowise MCP usage guidance.

  SessionStart (Claude Code)
    * Emits a one-paragraph context block: whether the index is current,
      behind (with a changed-file count), or mid-update, plus the core-tool
      trust rule. CLAUDE.md stays static and cache-friendly; live freshness
      arrives here instead. When active decisions are relevant to the
      session's working set, a capped standing-decisions block rides along.

  PostToolUse → Bash
    * After a successful git commit/merge/rebase/cherry-pick/pull, if
      the wiki HEAD has drifted from .repowise/state.json's last sync
      commit AND no `repowise update` is in flight AND we haven't
      already warned for this HEAD, emit a one-line stale-wiki notice.

  PostToolUse → Read
    * Skeleton nudge: a large Read of an indexed file gets a one-line
      pointer at the skeleton surface (get_context include=["skeleton"]),
      with a cheap bounds-arithmetic estimate of the saving. Once per file
      per session.
    * Stale-read notice: when this file was Edited/Written after the
      session's previous Read of it, flag that earlier excerpts are stale.
      Once per file per session, never blocking.

  PostToolUse → Edit / Write
    * Record the edit in the per-session state file so a later Read can
      detect staleness. Claude clients additionally get a one-line notice
      when the edited file has a governing decision (once per session per
      decision, strictly capped); Codex lifecycle hooks instead get the
      index-staleness reminder below.

  PostToolUse → edit tools (Codex)
    * After file edits, emit a short reminder that the indexed context may
      be stale.

There is intentionally NO PreToolUse handling. Earlier versions enriched
every Grep/Glob unconditionally with importers/dependencies/symbols; in
practice this added noise on the >70% of searches where the agent had
already located what it wanted. PostToolUse is strictly more informed —
it can see the actual result count — and is the only entry point now.

Operational invariants:
  * No LLM calls, no network. Pure local SQLite + Python.
  * Cold start budget: well under the 10s hook timeout. Heavy imports
    (sqlalchemy, asyncio) are deferred until we actually have work.
  * Graceful failure: any unexpected error exits 0 with empty stdout
    so a repowise problem never surfaces in the agent transcript.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import click

from .bash_staleness import _handle_bash_post
from .codex import _handle_codex_context_event, _handle_post_edit_use
from .read_state import _handle_edit_post, _handle_read_post, _record_edit
from .search import _handle_search_post
from .session_start import _handle_claude_session_start

_EDIT_TOOL_NAMES = {"apply_patch", "Edit", "Write"}


@click.command("augment")
@click.option(
    "--client",
    type=click.Choice(["codex"]),
    default=None,
    help="Hook client marker. Codex lifecycle hooks pass this explicitly.",
)
def augment_command(client: str | None = None) -> None:
    """Enrich AI agent tool calls with codebase graph context (hook mode)."""
    try:
        _run_augment(client=client)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        # Hooks must never fail — exit silently on any error.
        sys.exit(0)


def _run_augment(*, client: str | None = None) -> None:
    """Main entry point — reads stdin, dispatches to hook handlers."""
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = payload.get("hook_event_name", "")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = payload.get("cwd", "")

    if client == "codex" and event in ("SessionStart", "UserPromptSubmit"):
        result = _handle_codex_context_event(event, cwd)
        if result:
            _emit_response(event, result)
        return

    if event == "SessionStart":
        # Claude Code lifecycle hook: live index-freshness + trust context,
        # plus the relevance-ranked standing-decisions block.
        session_id = payload.get("session_id", "")
        result = _handle_claude_session_start(
            cwd, session_id if isinstance(session_id, str) else ""
        )
        if result:
            _emit_response(event, result)
        return

    if event != "PostToolUse":
        return

    tool_output = payload.get("tool_response", payload.get("tool_output", {}))
    session_id = payload.get("session_id", "")
    result = _handle_post_tool_use(
        tool_name,
        tool_input,
        tool_output,
        cwd,
        client=client,
        session_id=session_id if isinstance(session_id, str) else "",
    )
    if result:
        _emit_response(event, result)


def _emit_response(event: str, context: str) -> None:
    """Write the hook JSON response to stdout.

    Suppressed when an identical emission was just produced (see
    :func:`_claim_emission`) so two concurrently-registered repowise hooks —
    one bundled in the Claude Code plugin, one written to
    ``~/.claude/settings.json`` by ``repowise init`` — can't echo the same
    enrichment block twice on a single tool event.
    """
    if not _claim_emission(event, context):
        return
    response = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


# Window within which an identical (event, context) emission is treated as a
# duplicate. Generous enough to cover two hook processes racing on the same
# tool event, short enough that a genuinely repeated search later still speaks.
_EMIT_DEDUP_TTL_SECONDS = 8.0


def _claim_emission(event: str, context: str) -> bool:
    """Return True if this caller may emit ``context`` for ``event`` now.

    Both repowise PostToolUse hooks fire on the same tool call and compute the
    same enrichment. A temp lock file keyed on the emission content lets
    exactly one win: the first caller creates it atomically and emits; a second
    caller within the TTL sees a fresh marker and stays silent. Fail-open — any
    error returns True so a dedup glitch can never swallow a real emission.
    """
    import hashlib
    import os
    import tempfile
    import time

    try:
        key = hashlib.sha1(f"{event}\x00{context}".encode()).hexdigest()[:16]
        marker = Path(tempfile.gettempdir()) / f".repowise-augment-{key}"
        now = time.time()
        try:
            # O_EXCL: only the first concurrent caller creates the file.
            fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(now).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            # Someone emitted recently — defer unless the marker is stale, in
            # which case take it over (handles a real repeat search later, and
            # a crashed prior run that left the marker behind).
            try:
                if now - marker.stat().st_mtime <= _EMIT_DEDUP_TTL_SECONDS:
                    return False
            except OSError:
                return True
            with contextlib.suppress(OSError):
                marker.write_text(str(now), encoding="utf-8")
            return True
    except Exception:
        return True


def _handle_post_tool_use(
    tool_name: str,
    tool_input: dict,
    tool_output: dict | str,
    cwd: str,
    *,
    client: str | None = None,
    session_id: str = "",
) -> str | None:
    """Dispatch PostToolUse events from Claude or Codex."""
    # The edit-tool freshness notice is a Codex-only lifecycle hook, gated on
    # the Codex client so the widened Claude matcher (Read|Edit|Write) can't
    # emit Codex-flavored banners to Claude Code users. Both clients record
    # the edit for the per-session stale-read state machine; Claude clients
    # additionally get the once-per-decision governing-decision notice.
    if tool_name in _EDIT_TOOL_NAMES:
        if client == "codex":
            _record_edit(tool_input, cwd, session_id)
            return _handle_post_edit_use(cwd)
        return _handle_edit_post(tool_input, cwd, session_id)
    if tool_name == "Read":
        return _handle_read_post(tool_input, tool_output, cwd, session_id)
    if tool_name in ("Bash", "PowerShell"):
        # The PowerShell tool (Windows Claude Code) surfaces the same
        # stdout/stderr response shape as Bash — one handler covers both.
        return _handle_bash_post(tool_input, tool_output, cwd)
    if tool_name in ("Grep", "Glob"):
        return _handle_search_post(tool_name, tool_input, tool_output, cwd)
    return None
