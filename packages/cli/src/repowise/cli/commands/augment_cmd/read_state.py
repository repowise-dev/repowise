"""PostToolUse Read/Edit/Write per-session read intelligence.

A small JSON state file under .repowise/ tracks which files this agent
session has Read and Edited, keyed by the hook payload's session_id. Two
behaviors ride on it:

  * Stale-read notice — a Read of a file whose previous Read predates a
    recorded Edit/Write gets a one-line "earlier excerpts are stale" flag.
  * Skeleton replacement — an unbounded Read of a large indexed file is
    *served as* its skeleton via ``updatedToolOutput``, once per file per
    session, when the repo opted in. Gates and rationale live in
    :mod:`read_skeleton`; this module owns only the session-state gates.

Rate limiting is the state file itself (per-file, per-session lists), NOT
the _claim_emission temp marker — that TTL-based dedup only suppresses the
two concurrently-registered hooks racing on one tool event, which still
applies on top. A new session_id resets the state.

Two notices lived here and were retired for the same reason wearing two
different disguises. The re-read notice scored 100% "respected" only because
agents rarely read the same file a third time, so it was measuring the base
rate and changing nothing.

The skeleton nudge (a one-line pointer at ``get_context(include=
["skeleton"])`` on a large Read) was the loudest surface in the system and
the measurement never came back. Over 516 replayed firings a structure call
followed 11.4% of the time, against an 11.9% unconditioned base rate. The
offense it asks the agent to stop, reading the *next* large indexed file
whole, followed 57.1% of first nudges and 53.4% of later ones, so repeated
exposure moved nothing. It was also answering the wrong reads:
``is_unbounded_read`` gates the *replacement*, and the nudge sat on the
branch the replacement declines, so 53.3% of firings followed a ranged Read.
That is advice to be more targeted, given to a read that already was.

The case both notices argued, "you already have this, take the structure
instead", is handled by the replacement doing it rather than saying it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._shared import HookResult, _extract_output_text, _find_repo_root, _relativize

#: Replacement gates. Named for the retired nudge they were first tuned for,
#: and left that way on purpose: :mod:`search_digest` documents its own ratio
#: against ``_READ_NUDGE_MAX_RATIO`` by name, and the counterfactual leg has to
#: keep asking the same question as the real one.
_READ_NUDGE_MIN_LINES = 100  # Read output lines before a replacement is worth it
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
        "stale_notified": [],
        # Files served as a skeleton this session. Doubles as the once-per-
        # file gate and as the "did the agent come back for it" marker.
        "skeletonized": [],
        # Files this session measured a *forgone* saving for — the
        # counterfactual leg, which runs only while the feature is off.
        # Doubles as its once-per-file gate.
        "forgone": [],
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
    """Stale-read notice, then the skeleton replacement where it applies."""
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

    replacement, forgone = _skeleton_replacement(
        repo_path, rel, tool_input, tool_output, state, edited_since_read=edited_since_read
    )
    if replacement is not None:
        fired.append(("skeleton_served", replacement.text))

    persisted = _save_session_state(repo_path, state)
    if not persisted:
        # The "read it again and you get it whole" escape hatch is the
        # `skeletonized` list, and the list is that state file. If it did not
        # persist — read-only checkout, full disk — the promise in the header
        # would have no ceiling: every unbounded Read would return a skeleton
        # and none would return the file. No durable state, no replacement.
        if replacement is not None:
            replacement = None
            fired = [f for f in fired if f[0] != "skeleton_served"]
        # The counterfactual's gates are the same list, so they fail the same
        # way: without a durable `forgone` the once-per-file gate and the
        # per-session cap both reset on the next event, and the same file is
        # counted again and again. That inflates the very number the
        # measurement exists to state honestly, and removes the ceiling on
        # what it costs. No durable state, no measurement either.
        forgone = None

    for category, text in fired:
        _log_read_firing(repo_path, session_id, category, rel, text)
    return HookResult(
        context="\n".join(notices) if notices else None,
        replacement=replacement.payload if replacement is not None else None,
        on_emitted=_bookkeeping(repo_path, replacement, forgone),
    )


def _bookkeeping(repo_path: Path, replacement, forgone):
    """The ledger write this Read owes, to run after the response is emitted.

    At most one of the two is ever set. Both are deferred for the same reason:
    accounting must not sit between the agent and its tool result. The
    counterfactual needs this more than the saving does, not less — it takes a
    write lock on a database ``repowise distill`` also writes, and it does so
    on a Read that is getting nothing back for the wait.
    """
    if replacement is not None:
        return lambda: _record_skeleton_saving(repo_path, replacement)
    if forgone is not None:
        return lambda: _record_forgone_saving(repo_path, forgone)
    return None


def _skeleton_replacement(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    tool_output: object,
    state: dict,
    *,
    edited_since_read: bool,
):
    """``(replacement, forgone)`` for this Read — at most one set. Never raises.

    Ordered cheapest-gate-first so a Read that cannot qualify — which is
    almost all of them — costs a handful of dict lookups and no import. The
    gates themselves are documented in :mod:`read_skeleton`.

    The two legs share every gate above the flag on purpose: a counterfactual
    computed under looser conditions than the thing it stands in for would be
    measuring a different feature.
    """
    try:
        from .read_skeleton import (
            as_read_output,
            enabled,
            is_unbounded_read,
            skeleton_replacement,
            supports_updated_output,
        )

        if not is_unbounded_read(tool_input):
            return None, None
        if _read_output_line_count(tool_output) < _READ_NUDGE_MIN_LINES:
            return None, None
        # A verification re-read after an edit needs fidelity, not structure.
        # `forgone` joins the once-per-file gate so that flipping the flag on
        # mid-session cannot bill the same file to both ledgers — it would
        # appear in the headline saving *and* be advertised as not saved, with
        # identical token counts. Costs at most a deferred replacement: the
        # file is served as a skeleton from the next session on.
        if edited_since_read or rel in state["skeletonized"] or rel in state["forgone"]:
            return None, None
        if not enabled(repo_path):
            return None, _measure_forgone(repo_path, rel, state)
        if not supports_updated_output():
            return None, None
        replacement = skeleton_replacement(
            repo_path,
            rel,
            min_ratio_gain=_READ_NUDGE_MAX_RATIO,
            min_saved_tokens=_READ_NUDGE_MIN_SAVINGS,
        )
        if replacement is not None:
            # Build the wire payload here, not at emit time: if this Read's
            # tool_response is not the shape Read documents, the replacement
            # would be rejected downstream and the agent would get the whole
            # file *while* the ledger recorded a saving. No payload, no row.
            payload = as_read_output(tool_output, replacement.text)
            if payload is None:
                return None, None
            replacement.payload = payload
    except Exception:
        # Everything is inside the try, gates included. A malformed config or
        # a stale index must cost this one enrichment, never the stale-read
        # notice and the ledger row that the rest of this handler owes.
        return None, None
    if replacement is not None:
        state["skeletonized"].append(rel)
    return replacement, None


#: Files per session the counterfactual will measure before it stops. The real
#: path spends ~30ms and hands back thousands of tokens; this one spends the
#: same and hands back nothing but a number, on the *common* case — every
#: qualifying Read in a repo that has the feature off. A few dozen files is
#: plenty to characterise a repo, and the cap is what keeps a measurement from
#: costing more than the thing it is measuring.
_MAX_FORGONE_PER_SESSION = 40


def _measure_forgone(repo_path: Path, rel: str, state: dict) -> str | None:
    """Claim a measurement slot for *rel*, or None. Computes nothing.

    Runs only when the feature is off, which is the point: a repo that
    declined can still see what declining costs, and item 5's own saving can
    be characterised on real work before it is switched on anywhere.

    **Nothing here is on the critical path, and that is deliberate.** The real
    replacement has to build its skeleton before the response, because the
    skeleton *is* the response. This one hands the agent nothing at all, so
    making it wait — for an index query, a file read, a skeleton build and a
    write lock on a database ``repowise distill`` also writes — would be
    charging every qualifying Read in an opted-out repo for a number it will
    never see. All of it is deferred to :func:`_forgone_saving`; the only
    thing that must happen now is claiming the slot, because the claim has to
    be in the state file the caller is about to write.

    Claiming before knowing whether the file qualifies means a file that turns
    out not to qualify still spends a slot. That is the conservative
    direction: the cap exists to bound what measuring *costs*, and an attempt
    costs whether or not it produces a row.

    **This settles A1 and nothing else.** A1 asks whether the replacement is a
    net win, and the numbers are exact — the same skeleton, the same token
    counts the real path would have billed. A2 asks how often the agent has to
    come back for the whole file, and nothing was replaced here, so nothing
    was recovered and there is no recovery rate to read. A large forgone total
    is not a pass; see the caveat ``repowise saved`` prints.
    """
    if len(state["forgone"]) >= _MAX_FORGONE_PER_SESSION:
        return None
    state["forgone"].append(rel)
    return rel


def _record_forgone_saving(repo_path: Path, rel: str) -> None:
    """Build the skeleton nobody will see and record what it would have saved.

    The whole measurement, run after the response is on its way. Never raises:
    a counterfactual that broke a Read would be the worst possible trade.
    """
    try:
        from .read_skeleton import record_forgone, skeleton_replacement

        would_have = skeleton_replacement(
            repo_path,
            rel,
            min_ratio_gain=_READ_NUDGE_MAX_RATIO,
            min_saved_tokens=_READ_NUDGE_MIN_SAVINGS,
        )
        if would_have is not None:
            record_forgone(repo_path, would_have)
    except Exception:
        return


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
    from .ledger import _claim_ledger

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
