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


#: Per-session state lives in its own file, and that is not a detail.
#:
#: It used to be a single ``.augment-session.json`` per repo, keyed on
#: ``session_id``, which **reset the whole file whenever the id changed**. A
#: subagent sidechain runs under a different session id from its parent, and
#: PostToolUse fires in sidechains, so a parent Read and a subagent Read
#: alternating through one repo wiped each other's state on every event. No
#: Read surface could hold a once-per-file gate long enough to fire, which
#: looked like sidechains not being reachable and was really this file name.
#:
#: Partitioning is also the semantically right answer, not merely the fix: a
#: subagent never received the parent's bytes, so it must not inherit the
#: parent's "you already have this".
_STATE_DIR = "hook-sessions"

#: Session files older than this are swept, once, when a session first writes.
#: A session lasts hours at the outside and the files are a few KB, so this is
#: generous on purpose: keeping one too long costs nothing, deleting a live one
#: costs a repeated notice.
_STATE_TTL_SECONDS = 7 * 24 * 3600


def _session_state_path(repo_path: Path, session_id: str = "") -> Path:
    """This session's state file. One per session id — see :data:`_STATE_DIR`."""
    import hashlib

    digest = hashlib.sha1(session_id.encode("utf-8", "replace")).hexdigest()[:16]
    return Path(repo_path) / ".repowise" / _STATE_DIR / f"{digest}.json"


def _sweep_stale_sessions(directory: Path) -> None:
    """Drop session files nothing will read again. Best-effort, never raises.

    Runs only when a session writes its state for the first time, so the
    directory listing is paid once per session rather than once per Read.
    """
    import time

    cutoff = time.time() - _STATE_TTL_SECONDS
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _load_session_state(repo_path: Path, session_id: str) -> dict:
    """Load the per-session read/edit state, resetting on session change.

    ``reads``/``edits`` map repo-relative paths to a monotonically increasing
    per-session sequence number (``seq``) rather than wall-clock time — two
    hook events can land within one clock tick on Windows, and ordering is
    the only thing the stale-read comparison needs.

    The ``session_id`` check below is belt and braces now rather than the
    partition itself (see :data:`_STATE_DIR`): the file is already per-session,
    so a mismatch means a digest collision or a hand-edited file, and starting
    fresh is the right answer to both.
    """
    fresh = {
        "session_id": session_id,
        "seq": 0,
        "reads": {},
        "edits": {},
        # Per path, what the previous Read of it actually delivered: content
        # digest, the file's size and mtime then, the range requested, and the
        # tool call it happened on. The whole basis of the re-read collapse,
        # and every field is load-bearing — see :mod:`.reread`.
        "read_meta": {},
        "stale_notified": [],
        # Files already flagged as changed-under-the-agent, so the notice is
        # once per file per session like every other one here.
        "changed_notified": [],
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
        state = json.loads(
            _session_state_path(repo_path, session_id).read_text(encoding="utf-8")
        )
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
    notice-dedup entry costs a repeated notice. The two replacing Read
    surfaces cannot: their once-per-file gate *is* this file, so they check.
    """
    for key in ("reads", "edits"):
        entries = state.get(key, {})
        if len(entries) > 500:
            keep = sorted(entries, key=entries.get, reverse=True)[:400]
            state[key] = {k: entries[k] for k in keep}
    # ``read_meta`` is the biggest thing here (a digest and three ints per
    # path), so it is trimmed to whatever survived above rather than growing
    # beside it. A dropped entry costs one uncollapsed re-read, never a wrong
    # answer: the collapse needs the entry to fire at all.
    meta = state.get("read_meta")
    reads = state.get("reads", {})
    if isinstance(meta, dict) and len(meta) > len(reads):
        state["read_meta"] = {k: v for k, v in meta.items() if k in reads}
    path = _session_state_path(repo_path, state.get("session_id", ""))
    try:
        first_write = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return False
    if first_write:
        _sweep_stale_sessions(path.parent)
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
    *,
    adapter=None,
) -> HookResult:
    """Stale-read notice, then whichever Read replacement applies.

    Two surfaces can replace this Read and **at most one of them does**. The
    collapse is tried first: when it fires, the agent already holds these
    bytes, so a skeleton of them answers a question nobody asked.

    The ordering is not the whole story, and getting the rest wrong produced a
    lie. The collapse's premise is *the agent received these exact bytes*, and
    a Read that some other surface replaced delivered something else entirely.
    So a replaced Read records **no** content observation
    (:func:`_record_read_meta`), and the next Read of that file has nothing to
    compare against: no collapse claiming bytes the agent never saw, and no
    change notice fired by a digest that only differs because a skeleton was
    served in between.
    """
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.strip():
        return HookResult()
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return HookResult()
    rel = _relativize(file_path, repo_path)
    if rel is None:
        return HookResult()
    if adapter is None:
        from repowise.cli.agent_adapters import adapter_for

        adapter = adapter_for(None)

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

    # Read *before* this Read is recorded: both need what the previous one
    # delivered, and the line below overwrites it.
    prior_meta = state["read_meta"].get(rel)
    state["seq"] += 1
    state["reads"][rel] = state["seq"]

    served, on_emitted = _collapse_replacement(
        repo_path,
        rel,
        tool_input,
        tool_output,
        state,
        adapter,
        prior_meta=prior_meta,
        edited_since_read=edited_since_read,
        notices=notices,
        fired=fired,
    )
    # A file that just changed underneath the agent is served whole. The
    # change notice says the content below is current, and a skeleton would
    # make that half true: current, but with every body elided, at exactly the
    # moment the agent has most reason to look at one.
    changed_under_agent = any(category == "changed_since_read" for category, _ in fired)
    if served is None and not changed_under_agent:
        served, on_emitted = _skeleton_replacement(
            repo_path,
            rel,
            tool_input,
            tool_output,
            state,
            adapter,
            edited_since_read=edited_since_read,
        )
        if served is not None:
            fired.append((served.category, served.text))
            state["skeletonized"].append(rel)

    _record_read_meta(repo_path, rel, tool_input, tool_output, state, replaced=served is not None)

    if not _save_session_state(repo_path, state):
        # Every once-per-file gate here *is* that state file. If it did not
        # persist — read-only checkout, full disk — nothing bounds a
        # replacement: every qualifying Read would be replaced and none would
        # return the file, so the "read it again and you get it whole" escape
        # hatch would not exist. The forgone measurement fails the same way,
        # and worse: without a durable gate the same file is counted again and
        # again, inflating the very number it exists to state honestly.
        # No durable state, no replacement and no measurement.
        if served is not None:
            fired = [f for f in fired if f[0] != served.category]
            served = None
        on_emitted = None

    for category, text in fired:
        _log_read_firing(repo_path, session_id, category, rel, text)
    return HookResult(
        context="\n".join(notices) if notices else None,
        replacement=served.payload if served is not None else None,
        on_emitted=on_emitted,
    )


def _record_read_meta(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    tool_output: object,
    state: dict,
    *,
    replaced: bool = False,
) -> None:
    """Remember what this Read delivered, so the next one can be collapsed.

    *replaced* means some surface stood in for the tool result, so the bytes in
    ``tool_output`` are **not** what the agent received. Forgetting the file
    instead of recording them is the only safe answer: recording them would let
    the next Read be collapsed with "you were served the whole file" when the
    agent got a skeleton, and recording what it *did* receive would make the
    next Read look like an external change. The file simply has no observation
    this session until the agent reads it for real.

    Otherwise recorded for every Read, including one just collapsed — the
    digest is unchanged, and re-recording it is what keeps a fourth read of the
    file collapsible after the third returned the bytes.

    Stat failures are not fatal: a missing mtime costs the "written since"
    clause, never correctness, because the digest is what decides.
    """
    if replaced:
        state["read_meta"].pop(rel, None)
        return
    content = _read_content(tool_output)
    if content is None:
        return
    from .reread import content_digest

    size = mtime = -1
    try:
        stat = (repo_path / rel).stat()
        size, mtime = stat.st_size, int(stat.st_mtime_ns)
    except OSError:
        pass
    state["read_meta"][rel] = {
        "h": content_digest(content),
        "sz": size,
        "mt": mtime,
        "off": tool_input.get("offset") if isinstance(tool_input, dict) else None,
        "lim": tool_input.get("limit") if isinstance(tool_input, dict) else None,
        "seq": state["seq"],
    }


def _read_content(tool_output: object) -> str | None:
    """The text this Read actually delivered, or None if the shape is unfamiliar.

    Only the documented Read shape counts. Guessing at an unknown envelope is
    how a collapse would compare two things that are not the same bytes.
    """
    if not isinstance(tool_output, dict):
        return None
    file_block = tool_output.get("file")
    if not isinstance(file_block, dict):
        return None
    content = file_block.get("content")
    return content if isinstance(content, str) else None


def _same_range(tool_input: dict, prior: dict) -> bool:
    """Whether this Read asked for the same window as the recorded one."""
    if not isinstance(tool_input, dict):
        return False
    return tool_input.get("offset") == prior.get("off") and tool_input.get(
        "limit"
    ) == prior.get("lim")


def _collapse_replacement(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    tool_output: object,
    state: dict,
    adapter,
    *,
    prior_meta: dict | None,
    edited_since_read: bool,
    notices: list[str],
    fired: list[tuple[str, str]],
):
    """``(served, on_emitted)`` for a re-read of unchanged bytes. Never raises.

    Three outcomes, and the ordering of the checks is the safety argument:

    * no previous read, or an Edit since one, or a different range → nothing;
    * same range, same digest → collapse;
    * same range, **different** digest → serve the bytes and say what changed.

    The digest comparison is what decides, not the mtime: identical bytes are
    identical whatever the filesystem says happened to them. mtime and size are
    the corroborating signal, and their only effect is one extra clause in the
    notice when the file was written but the content did not move.
    """
    if not isinstance(prior_meta, dict) or edited_since_read:
        return None, None
    if not _same_range(tool_input, prior_meta):
        return None, None
    content = _read_content(tool_output)
    if content is None:
        return None, None
    try:
        from .reread import CONFIG_FLAG, SAVINGS_SOURCE, changed_notice, collapse, content_digest

        digest = content_digest(content)
        if digest != prior_meta.get("h"):
            # It genuinely changed, and not through an Edit of ours. The agent
            # gets the bytes; the only thing worth adding is why they moved.
            if rel not in state["changed_notified"]:
                state["changed_notified"].append(rel)
                notices.append(changed_notice(rel, prior_meta))
                fired.append(("changed_since_read", notices[-1]))
            return None, None

        touched = False
        try:
            stat = (repo_path / rel).stat()
            touched = (
                prior_meta.get("sz") not in (-1, stat.st_size)
                or prior_meta.get("mt") not in (-1, int(stat.st_mtime_ns))
            )
        except OSError:
            pass

        from .replacement import offer

        served, on_emitted = offer(
            repo_path,
            adapter,
            flag=CONFIG_FLAG,
            source=SAVINGS_SOURCE,
            tool_name="Read",
            tool_response=tool_output,
            build=lambda: collapse(rel, content, prior_meta, touched=touched),
            forgone_gate=lambda: _claim_forgone(rel, state),
        )
    except Exception:
        return None, None
    if served is not None:
        fired.append((served.category, served.text))
    return served, on_emitted


def _skeleton_replacement(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    tool_output: object,
    state: dict,
    adapter,
    *,
    edited_since_read: bool,
):
    """``(served, on_emitted)`` for this Read. Never raises.

    Only the gates that are about *this session's reading of this file* live
    here, ordered cheapest-first so a Read that cannot qualify — which is
    almost all of them — costs a handful of dict lookups and no import.
    Everything from the harness capability probe onwards is
    :func:`replacement.offer`'s, shared with the other replacing surfaces.
    """
    try:
        from .read_skeleton import (
            CONFIG_FLAG,
            SAVINGS_SOURCE,
            is_unbounded_read,
            skeleton_replacement,
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
        from .replacement import offer

        return offer(
            repo_path,
            adapter,
            flag=CONFIG_FLAG,
            source=SAVINGS_SOURCE,
            tool_name="Read",
            tool_response=tool_output,
            build=lambda: skeleton_replacement(
                repo_path,
                rel,
                min_ratio_gain=_READ_NUDGE_MAX_RATIO,
                min_saved_tokens=_READ_NUDGE_MIN_SAVINGS,
            ),
            forgone_gate=lambda: _claim_forgone(rel, state),
        )
    except Exception:
        # Everything is inside the try, gates included. A malformed config or
        # a stale index must cost this one enrichment, never the stale-read
        # notice and the ledger row that the rest of this handler owes.
        return None, None


#: Files per session the counterfactual will measure before it stops. The real
#: path spends its time and hands back thousands of tokens; this one spends the
#: same and hands back nothing but a number, on the *common* case — every
#: qualifying Read in a repo that has the surface off. A few dozen files is
#: plenty to characterise a repo, and the cap is what keeps a measurement from
#: costing more than the thing it is measuring.
_MAX_FORGONE_PER_SESSION = 40


def _claim_forgone(rel: str, state: dict) -> bool:
    """Claim a counterfactual measurement slot for *rel*. Computes nothing.

    Runs only when a replacing surface is off, which is the point: a repo that
    declined can still see what declining costs, before switching anything on.

    The claim has to happen now, synchronously, because it belongs in the state
    file the caller is about to write; the measurement itself is deferred (see
    :func:`replacement.offer`). Claiming before knowing whether the file
    qualifies means a file that turns out not to qualify still spends a slot.
    That is the conservative direction: the cap bounds what measuring *costs*,
    and an attempt costs whether or not it produces a row.

    One list across both Read surfaces on purpose. A file counted as forgone by
    one of them must not be counted again by the other, or the same read would
    be advertised as two separate savings nobody made.

    **This says what the replacement would have taken off the bill and nothing
    else.** It cannot say what the agent would then have had to read back,
    because nothing was replaced, so nothing was recovered. A large forgone
    total is not a verdict; see the caveat ``repowise saved`` prints.
    """
    if rel in state["forgone"] or len(state["forgone"]) >= _MAX_FORGONE_PER_SESSION:
        return False
    state["forgone"].append(rel)
    return True


def _log_skeleton_recovery(
    repo_path: Path,
    rel: str,
    tool_input: dict,
    state: dict,
    fired: list[tuple[str, str]],
) -> None:
    """Note that the agent came back for a file we served as a skeleton.

    A *ranged* return is the elision contract working as designed; a *full*
    return is the replacement having been wrong about what the agent needed.
    Both are measurement rows — neither is ever spoken about — and keeping them
    apart is what lets a recovery be charged back against the saving.

    The requested window rides in the row text on purpose. Ledger keys hash
    that text, so a full recovery (stable text) dedups to one row per file
    while each distinct ranged recovery gets its own, which a count of files
    could not express.
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
    from repowise.cli.hook_ledger import _claim_ledger

    from ._shared import _ledger_key

    _claim_ledger(
        repo_path,
        session_id,
        _ledger_key("read", category, text),
        node_id=rel,
        surface="read",
        category=category,
        chars=len(text),
    )


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
