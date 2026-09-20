"""``repowise augment`` — hook-driven context enrichment for AI coding agents.

Reads Claude Code or Codex hook payloads from stdin (JSON) and writes
targeted enrichment back as a hook response.

Design philosophy: an enrichment hook is only valuable when it tells the
agent something the raw tool output didn't. Anything else is noise the
agent has to scroll past. So the hook fires on every Grep/Glob/Bash but
returns *nothing* most of the time, and only speaks up when there is
asymmetric, durable value:

  PostToolUse → Grep / Glob
    * Flood digest: a multi-file grep flood is served as a compact
      per-file digest via updatedToolOutput, with match counts and anchor
      line numbers per file, with the dropped tail named. Opt-in
      (hooks.search_digest), default off, Claude Code only. Falls back
      to appending the same digest beside the flood, as it always did.
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

Codex SessionStart: adds short repowise MCP usage guidance. (``UserPromptSubmit``
is still answered for installs predating its retirement, but nothing we ship
registers it any more — see :mod:`.codex`.)

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
    * Skeleton replacement: an unbounded Read of a large indexed file is
      served as its skeleton via updatedToolOutput, elision markers and
      1-indexed line ranges intact, once per file per session. Opt-in
      (hooks.read_skeleton), default off.
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

  PostToolUseFailure → Read / Edit / Write / Grep / Glob / NotebookEdit
    * Wrong-path rescue: the call failed because the path is not in this
      tree, and exactly one indexed file carries that basename. Names it.
      Silent on an ambiguous basename, a directory target, a path in another
      checkout, and on the failures where Claude Code already printed its
      own suggestion — the whole value here is that a rescue can be trusted.

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

from repowise.cli._setup import configure_cli_logging
from repowise.cli.agent_adapters import adapter_for

from ._shared import HookResult, as_result
from .bash_staleness import _handle_bash_post
from .codex import _handle_codex_context_event, _handle_post_edit_use
from .read_state import _handle_edit_post, _handle_read_post, _record_edit
from .search import _handle_search_post
from .served_reads import _handle_mcp_read_post, _log_read_after_served
from .session_start import _handle_claude_session_start
from .wrong_path import _handle_tool_failure

#: One adapter per hook process, resolved from the ``--client`` marker. Every
#: "which tools does this agent call?" question below reads off it, so the
#: dispatcher carries no tool-name literals and a third harness needs no branch
#: here — see ``agent_adapters._REGISTRY``, which is the whole registration.
#:
#: This replaced a union of both harnesses' tool names, which routed a Codex
#: tool name for a Claude payload and vice versa. Harmless while the names
#: happened not to collide, and exactly the shape that stops being harmless
#: when a third agent names its editor something the second one already uses.
#:
#: Free at module scope: ``agent_adapters`` is stdlib-only by its own hot-path
#: contract, and ``tests/unit/cli/test_augment_hook_perf.py`` holds that.
_ADAPTERS: dict[str, object] = {}


def _adapter(client: str | None):
    """The adapter for this invocation, memoized for the process."""
    key = client or ""
    adapter = _ADAPTERS.get(key)
    if adapter is None:
        adapter = _ADAPTERS[key] = adapter_for(client)
    return adapter


@click.command("augment")
@click.option(
    "--client",
    type=click.Choice(["codex"]),
    default=None,
    help="Hook client marker. Codex lifecycle hooks pass this explicitly.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the hook pipeline.",
)
def augment_command(client: str | None = None, verbose: bool = False) -> None:
    """Enrich AI agent tool calls with codebase graph context (hook mode)."""
    configure_cli_logging(verbose=verbose)
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
        session_id = payload.get("session_id", "")
        session_id = session_id if isinstance(session_id, str) else ""
        result = _handle_codex_context_event(event, cwd, session_id)
        if result:
            _emit_response(event, result, session_id)
        _count_run(cwd, session_id, event, "", emitted=bool(result))
        return

    if event == "SessionStart":
        # Claude Code lifecycle hook: live index-freshness + trust context,
        # plus the relevance-ranked standing-decisions block.
        session_id = payload.get("session_id", "")
        session_id = session_id if isinstance(session_id, str) else ""
        result = _handle_claude_session_start(cwd, session_id)
        if result:
            _emit_response(event, result, session_id)
        _count_run(cwd, session_id, event, "", emitted=bool(result))
        return

    if event == "PostToolUseFailure":
        # A tool that just failed on a path this tree does not have is the one
        # moment the index can answer a question the agent actually asked.
        #
        # This event carries the failure in ``error`` and not in
        # ``tool_response``, which is PostToolUse-only, and it fires for a user
        # interrupt as well as for a real error. Both are load-bearing: reading
        # the wrong field is silence, and answering an interrupt is noise at
        # the moment the user asked for quiet.
        if payload.get("is_interrupt"):
            return
        session_id = payload.get("session_id", "")
        session_id = session_id if isinstance(session_id, str) else ""
        result = _handle_tool_failure(
            tool_name, tool_input, payload.get("error"), cwd, session_id=session_id
        )
        if result:
            _emit_response(event, result, session_id)
        _count_run(cwd, session_id, event, tool_name, emitted=bool(result))
        return

    if event != "PostToolUse":
        return

    tool_output = payload.get("tool_response", payload.get("tool_output", {}))
    session_id = payload.get("session_id", "")
    session_id = session_id if isinstance(session_id, str) else ""
    result = _handle_post_tool_use(
        tool_name,
        tool_input,
        tool_output,
        cwd,
        client=client,
        session_id=session_id,
    )
    if result:
        _emit_response(event, result, session_id)
    if result.on_emitted is not None:
        # Post-response bookkeeping, for the same reason _count_run is below:
        # the agent must not wait on accounting.
        with contextlib.suppress(Exception):
            result.on_emitted()
    _count_run(cwd, session_id, event, tool_name, emitted=bool(result))


def _count_run(cwd: str, session_id: str, event: str, tool: str, *, emitted: bool) -> None:
    """Bill this invocation to the hook-latency counter; never fails the hook.

    Deliberately after the response is written: the agent must not wait on
    bookkeeping, and a broken sidecar must not cost an enrichment that was
    already computed. The reported time therefore excludes this write itself.
    """
    if not session_id:
        return
    try:
        from repowise.cli.hook_ledger import _record_hook_run

        from ._shared import _find_repo_root

        repo_path = _find_repo_root(Path(cwd))
        if repo_path is not None:
            _record_hook_run(repo_path, session_id, event, tool, emitted=emitted)
    except Exception:
        return


def _emit_response(event: str, result: HookResult | str, session_id: str = "") -> None:
    """Write the hook JSON response to stdout.

    Two fields, and they are not alternatives: ``additionalContext`` is
    appended to what the agent sees, ``updatedToolOutput`` replaces the tool
    result outright. Claude Code accepts both together, and the Read surface
    can legitimately want both — a stale-read flag alongside a served
    skeleton.

    ``updatedToolOutput`` is typed by the tool being replaced, so it is
    whatever the handler built (a string for Bash-shaped output, an object for
    Read's) and is written through unchanged. Claude Code validates it and
    falls back to the original output on a mismatch, so getting the shape
    wrong fails *quietly* from the agent's side — the row still says served.

    Suppressed when an identical emission was just produced (see
    :func:`_claim_emission`) so two concurrently-registered repowise hooks —
    one bundled in the Claude Code plugin, one written to
    ``~/.claude/settings.json`` by ``repowise init`` — can't echo the same
    enrichment block twice on a single tool event.
    """
    result = as_result(result)
    replacement = result.replacement
    if replacement is None or isinstance(replacement, str):
        dedup_mark = replacement or ""
    else:
        dedup_mark = json.dumps(replacement, sort_keys=True)
    if not _claim_emission(event, f"{session_id}\x00{result.context or ''}\x00{dedup_mark}"):
        return
    payload: dict[str, object] = {"hookEventName": event}
    if result.context:
        payload["additionalContext"] = result.context
    if replacement:
        payload["updatedToolOutput"] = replacement
    sys.stdout.write(json.dumps({"hookSpecificOutput": payload}))
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

    The session id is part of the key (the caller folds it into *context*).
    Without it, two genuinely separate sessions — a parent and a subagent,
    which never share an id — that read the same file at the same point in
    their own tool sequence produce byte-identical notices, and the second one
    is silently swallowed. The duplicates this exists to catch are two
    processes on *one* tool event, which by definition share a session.
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
) -> HookResult:
    """Dispatch PostToolUse events from Claude or Codex.

    Read and Grep/Glob can replace the tool result, so they return a
    :class:`HookResult` themselves; every other branch yields plain additional
    context and keeps returning ``str | None``, which :func:`as_result` lifts
    here, so the two-field shape exists in one place rather than in eight.

    Every tool-name test below is the adapter's answer for *this* harness.
    """
    adapter = _adapter(client)
    # The edit-tool freshness notice is a Codex-only lifecycle hook, gated on
    # the Codex client so the widened Claude matcher (Read|Edit|Write) can't
    # emit Codex-flavored banners to Claude Code users. Both clients record
    # the edit for the per-session stale-read state machine; Claude clients
    # additionally get the once-per-decision governing-decision notice.
    if tool_name in adapter.edit_tool_names:
        if client == "codex":
            _record_edit(tool_input, cwd, session_id)
            return as_result(
                _handle_post_edit_use(cwd, session_id=session_id, tool_input=tool_input)
            )
        return as_result(_handle_edit_post(tool_input, cwd, session_id))
    if tool_name in adapter.read_tool_names:
        # Read-after-served KPI: logged to the ledger, never spoken about.
        _log_read_after_served(tool_input, tool_output, cwd, session_id)
        return _handle_read_post(tool_input, tool_output, cwd, session_id, adapter=adapter)
    if tool_name in adapter.shell_tool_names:
        # The PowerShell tool (Windows Claude Code) and Codex's several names
        # for its shell all surface the same stdout/stderr response shape as
        # Bash — one handler covers them.
        return as_result(_handle_bash_post(tool_input, tool_output, cwd))
    if tool_name in adapter.search_tool_names:
        # The adapter reaches this one because the flood digest can *replace*
        # the tool output, and not every harness's protocol can honour that.
        return _handle_search_post(
            tool_name, tool_input, tool_output, cwd, session_id, adapter=adapter
        )
    if tool_name.startswith("mcp__") and "repowise" in tool_name.lower():
        # Served-content bookkeeping for the read-after-served KPI. Never
        # emits — measurement only.
        _handle_mcp_read_post(tool_output, cwd, session_id)
        return HookResult()
    return HookResult()
