"""PostToolUse Read/Edit/Write per-session read intelligence.

A small JSON state file under .repowise/ tracks which files this agent
session has Read and Edited, keyed by the hook payload's session_id. Three
behaviors ride on it:

  * Stale-read notice — a Read of a file whose previous Read predates a
    recorded Edit/Write gets a one-line "earlier excerpts are stale" flag.
  * Skeleton replacement — an unbounded Read of a large indexed file is
    *served as* its skeleton via ``updatedToolOutput``, once per file per
    session, when the repo opted in. Gates and rationale live in
    :mod:`read_skeleton`; this module owns only the session-state gates.
  * Skeleton nudge — the fallback when the replacement does not apply: a
    one-line pointer at get_context(include=["skeleton"]) with a
    bounds-arithmetic estimate. Measured at 0.2%, and on the way out — it
    survives only to cover clients that cannot honour a replacement.

Rate limiting is the state file itself (per-file, per-session lists), NOT
the _claim_emission temp marker — that TTL-based dedup only suppresses the
two concurrently-registered hooks racing on one tool event, which still
applies on top. A new session_id resets the state.

The re-read notice lived here too, and was retired: it scored 100%
"respected" only because agents rarely read the same file a third time, so
it was measuring the base rate and changing nothing. The case it argued —
"you already have this, take a range instead" — is now handled by doing it
rather than saying it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._shared import HookResult, _extract_output_text, _find_repo_root, _relativize

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
        # Files served as a skeleton this session. Doubles as the once-per-
        # file gate and as the "did the agent come back for it" marker.
        "skeletonized": [],
        # Skeletonized files the agent later read in full, and files whose
        # edit-from-a-skeleton warning has already fired. Both exist to keep
        # that warning to exactly the window where it is true.
        "read_whole": [],
        "skeleton_edit_warned": [],
        "decisions_shown": [],
        # File ranges served by repowise MCP responses this session, kept for
        # the read-after-served KPI (see read_enrich; rel -> [[start, end]]).
        "served": {},
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


def _save_session_state(repo_path: Path, state: dict) -> bool:
    """Persist session state; trims unbounded growth, never raises.

    Returns whether the write landed. Most callers can ignore that — a lost
    notice-dedup entry costs a repeated notice. The skeleton replacement
    cannot: its once-per-file gate *is* this file, so it checks.
    """
    for key in ("reads", "edits"):
        entries = state.get(key, {})
        if len(entries) > 500:
            keep = sorted(entries, key=entries.get, reverse=True)[:400]
            state[key] = {k: entries[k] for k in keep}
    try:
        _session_state_path(repo_path).write_text(json.dumps(state), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return False
    return True


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
    """Record an Edit/Write; surface the file's governing decision and bug history.

    Both notices fire once per session per file under their own strict
    per-session caps (see :func:`decision_inject._edit_decision_notice` and
    :func:`decision_inject._edit_fix_history_notice`) so a governed or
    much-fixed file gets a heads-up, not a drumbeat. A file that is both emits
    two lines and never more. Codex lifecycle hooks call this with
    ``with_decisions=False``: they get their own edit banner.
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

    notices: list[str] = []
    skeleton_warning = _edit_from_skeleton_notice(rel, state)
    if skeleton_warning:
        notices.append(skeleton_warning)
    if with_decisions:
        from .decision_inject import _edit_decision_notice, _edit_fix_history_notice

        for emit in (
            lambda: _edit_decision_notice(repo_path, rel, session_id, state),
            lambda: _edit_fix_history_notice(repo_path, rel, session_id),
        ):
            try:
                line = emit()
            except Exception:
                line = None
            if line:
                notices.append(line)

    _save_session_state(repo_path, state)
    return "\n".join(notices) or None


def _edit_from_skeleton_notice(rel: str, state: dict) -> str | None:
    """Flag an edit to a file this session has only ever seen as a skeleton.

    The replacement satisfies Claude Code's read-before-edit precondition with
    a Read whose *content* the agent never saw, and that cuts the wrong way in
    three places the header alone cannot reach: an ``Edit`` with
    ``replace_all`` rewrites occurrences inside elided spans, a ``Write``
    reconstructs the file from signatures and destroys every body, and
    "does this file do X?" gets answered from a map. Once per file per
    session, and it stops as soon as the agent reads the file for real —
    which is what makes it a guard rather than a drumbeat.
    """
    if rel not in state["skeletonized"]:
        return None
    if rel in state["read_whole"] or rel in state["skeleton_edit_warned"]:
        return None
    state["skeleton_edit_warned"].append(rel)
    return (
        f"[repowise] You have only seen {rel} as a skeleton this session — its bodies "
        "were elided. Read the range you are changing (or the whole file) before "
        "trusting an exact-match edit, a rewrite, or a conclusion about what this "
        "file contains."
    )


def _handle_read_post(
    tool_input: dict,
    tool_output: object,
    cwd: str,
    session_id: str,
) -> HookResult:
    """Stale-read notice, then either a skeleton replacement or the nudge."""
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return HookResult()
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return HookResult()
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return HookResult()

    state = _load_session_state(repo_path, session_id)
    notices: list[str] = []
    fired: list[tuple[str, str]] = []  # (category, text) for the efficacy ledger

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
        fired.append(("stale_read", notices[-1]))

    _log_skeleton_recovery(repo_path, rel, tool_input, state, fired)

    state["seq"] += 1
    state["reads"][rel] = state["seq"]

    replacement = _skeleton_replacement(
        repo_path, rel, tool_input, tool_output, state, edited_since_read=edited_since_read
    )
    if replacement is not None:
        fired.append(("skeleton_served", replacement.text))
    else:
        nudge = _skeleton_nudge(repo_path, rel, tool_output, state)
        if nudge:
            notices.append(nudge)
            fired.append(("skeleton_nudge", nudge))

    persisted = _save_session_state(repo_path, state)
    if replacement is not None and not persisted:
        # The "read it again and you get it whole" escape hatch is the
        # `skeletonized` list, and the list is that state file. If it did not
        # persist — read-only checkout, full disk — the promise in the header
        # would have no ceiling: every unbounded Read would return a skeleton
        # and none would return the file. No durable state, no replacement.
        replacement = None
        fired = [f for f in fired if f[0] != "skeleton_served"]

    for category, text in fired:
        _log_read_firing(repo_path, session_id, category, rel, text)
    return HookResult(
        context="\n".join(notices) if notices else None,
        replacement=replacement.text if replacement is not None else None,
        on_emitted=(
            (lambda r=replacement: _record_skeleton_saving(repo_path, r))
            if replacement is not None
            else None
        ),
    )


def _skeleton_replacement(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    tool_output: object,
    state: dict,
    *,
    edited_since_read: bool,
):
    """The skeleton to serve in place of this Read, or None. Never raises.

    Ordered cheapest-gate-first so a Read that cannot qualify — which is
    almost all of them — costs a handful of dict lookups and no import. The
    gates themselves are documented in :mod:`read_skeleton`.
    """
    try:
        from .read_skeleton import (
            enabled,
            is_unbounded_read,
            skeleton_replacement,
            supports_updated_output,
        )

        if not is_unbounded_read(tool_input):
            return None
        if _read_output_line_count(tool_output) < _READ_NUDGE_MIN_LINES:
            return None
        # A verification re-read after an edit needs fidelity, not structure.
        if edited_since_read or rel in state["skeletonized"]:
            return None
        if not enabled(repo_path) or not supports_updated_output():
            return None
        replacement = skeleton_replacement(
            repo_path,
            rel,
            min_ratio_gain=_READ_NUDGE_MAX_RATIO,
            min_saved_tokens=_READ_NUDGE_MIN_SAVINGS,
        )
    except Exception:
        # Everything is inside the try, gates included. A malformed config or
        # a stale index must cost this one enrichment, never the stale-read
        # notice and the ledger row that the rest of this handler owes.
        return None
    if replacement is not None:
        state["skeletonized"].append(rel)
    return replacement


def _log_skeleton_recovery(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    state: dict,
    fired: list[tuple[str, str]],
) -> None:
    """Note that the agent came back for a file we served as a skeleton.

    Gate A's two numbers live here. A *ranged* return is the elision contract
    working as designed; a *full* return is the replacement having been wrong
    about what the agent needed, and is what the gate caps. Both are
    measurement rows — neither is ever spoken about.

    The requested window rides in the row text on purpose. Ledger keys hash
    that text, so a full recovery (stable text) dedups to one row per file
    while each distinct ranged recovery gets its own — which is what Gate A's
    A1 needs, since it charges every recovery back against the saving and
    cannot do that from a count of files.
    """
    if rel not in state["skeletonized"]:
        return
    from .read_skeleton import is_unbounded_read

    if is_unbounded_read(tool_input):
        # The gate above already declined to replace this one, so the agent is
        # getting the real file — it has now genuinely seen it.
        if rel not in state["read_whole"]:
            state["read_whole"].append(rel)
        fired.append(("skeleton_recovered_full", f"skeleton_recovered_full:{rel}"))
        return
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    fired.append(("skeleton_ranged", f"skeleton_ranged:{rel}:{offset}:{limit}"))


def _log_read_firing(
    repo_path: Path, session_id: str, category: str, rel: str, text: str
) -> None:
    """Record one Read-surface firing in the shared efficacy ledger.

    Measurement only — never changes what the agent sees, and the once-per-
    file-per-session gate stays the state file above, not this row. The key is
    the shared text hash (:func:`_shared._ledger_key`) so the transcript
    classifier in :mod:`repowise.core.sessions.efficacy` settles *this* row
    rather than inserting a second one for the same firing.
    """
    if not session_id:
        return
    from ._shared import _ledger_key
    from .decision_inject import _claim_ledger

    _claim_ledger(
        repo_path,
        session_id,
        _ledger_key("read", category, text),
        node_id=rel,
        surface="read",
        category=category,
        chars=len(text),
    )


def _record_skeleton_saving(repo_path: Path, replacement) -> None:
    """Bill a served skeleton to the savings ledger. Never fails the hook.

    After the response is decided, for the same reason :func:`_count_run` is:
    the agent must not wait on bookkeeping, and a broken ledger must not cost
    an enrichment that is already computed.
    """
    try:
        from .read_skeleton import record_saving

        record_saving(repo_path, replacement)
    except Exception:
        return


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
