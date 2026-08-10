"""PostToolUse Read → when the bytes are unchanged, serve a pointer, not them.

Agents re-read files they have already read, without editing them in between,
and the second copy of a file is worth nothing: it is already in the context
window a few tool calls up. This replaces that second copy with a short notice
naming the earlier read.

**Correctness here is arithmetic, not a judgment.** The agent received these
exact bytes, in this session, and they hash the same now. That is the whole
argument for the surface, and it is what separates it from an enrichment that
guesses at what the agent wanted.

The honest limit: a file can genuinely change under the agent — a
``git checkout``, a formatter, a sibling agent. That is detectable here, and
when it happens the file is served. **On any doubt, serve the file.**

What the collapse deliberately does not touch:

* **Post-edit re-reads.** A Read that follows an Edit of the same file is
  verification and needs fidelity. Excluded by construction in
  :mod:`.read_state`, never by a heuristic here.
* **A re-read at a *different* range.** Different bytes are different bytes.
  The hash settles it without anyone having to reason about ranges, and the
  notice still names the range the agent was served last time, so a partial
  earlier read is never presented as a whole-file one.
* **A file whose bytes changed.** Then the agent gets them — and gets told
  they changed, which is worth more than the bytes were (see
  :func:`changed_notice`). "It changed underneath you" is a fact the session
  otherwise has no way to learn, because no Edit of ours recorded it.

**Never twice in a row for the same file.** The premise is that the bytes are
still in the agent's context, and after a context compaction they are not: the
earlier read is gone and a notice pointing at it names something that no longer
exists. There is no way to detect that from inside a hook, so the contract is
the escape hatch instead — reading the file once more always returns the
content, exactly as the skeleton replacement promises. It costs the second and
subsequent collapses of a hot file and buys a bound on how wrong this can be.

That falls out of one rule rather than a second list: a Read that any surface
replaced records no content observation at all (``read_state._record_read_meta``),
because what the agent received was not the file. So the next Read has nothing
to compare against and is served in full — which is also what stops a collapse
claiming "you were served the whole file" to an agent that was served a
skeleton.

Not a silent truncation: the notice names the file, the tool call of the
earlier read, the range served then, and how to get the bytes back.

This is **not** the retired ``("read", "reread")`` advisory, which told the
agent it had already read the file and left it to act. This does the thing
instead of saying it, and is ledgered under a different name so the two are
never pooled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .replacement import Offer

if TYPE_CHECKING:  # pragma: no cover - hot path keeps pathlib out of the graph
    from pathlib import Path

#: Savings-ledger identity. Same ``hook-read`` surface as the skeleton — both
#: are Reads made cheaper — with its own filter name so ``repowise saved --by
#: filter`` never pools a collapse with a skeleton.
SAVINGS_SOURCE = "hook-read"
_SAVINGS_FILTER = "read_reread"

CONFIG_FLAG = "read_reread"


def enabled(repo_path: Path) -> bool:
    """True when this repo opted into re-read collapsing. Fails closed."""
    from ._shared import hook_flag_enabled

    return hook_flag_enabled(repo_path, CONFIG_FLAG)


def content_digest(content: str) -> str:
    """Short hash of exactly what the agent was served for this Read.

    Hashing the *served* text rather than the file on disk is what makes the
    comparison arithmetic: two Reads whose payloads hash the same delivered the
    same bytes, whatever their offsets were and whatever happened to the file
    in between. Twelve hex is ~48 bits — collision risk across the few hundred
    file-reads of a session is nil, and the alternative to a short digest is
    keeping whole file contents in a session state file.
    """
    import hashlib

    return hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()[:12]


def _range_label(offset: object, limit: object) -> str:
    """How to name the range an earlier Read was served."""
    if offset is None and limit is None:
        return "the whole file"
    if isinstance(offset, int) and isinstance(limit, int):
        return f"lines {offset}-{offset + limit - 1}"
    if isinstance(offset, int):
        return f"from line {offset}"
    if isinstance(limit, int):
        return f"the first {limit} lines"
    return "the same range"


def collapse(rel: str, content: str, prior: dict, *, touched: bool) -> Offer | None:
    """The notice to serve instead of *content*, or None when it saves nothing.

    *prior* is this session's recorded metadata for the previous Read of *rel*;
    the caller has already established that its digest matches this one and
    that no Edit intervened. *touched* says the file's mtime or size moved even
    though the served range did not, which is worth one clause: something wrote
    the file, and the agent may be about to be told a stale story by something
    else.

    The clause is careful about *what* is unchanged. On a ranged read the
    digest proves the served window is identical and says nothing about the
    rest of the file, so the wording claims only the window.

    **No tuned threshold.** The only gate is that the notice is smaller than
    what it replaces, which is an exact comparison rather than a floor. A file
    short enough to fail it is one where collapsing would have saved nothing.
    """
    whole_file = prior.get("off") is None and prior.get("lim") is None
    label = _range_label(prior.get("off"), prior.get("lim"))
    turn = prior.get("seq")
    when = f" (tool call {turn} of this session)" if isinstance(turn, int) and turn else ""
    if not touched:
        also = ""
    elif whole_file:
        also = (
            " The file has been written since — a timestamp changed — but the bytes "
            "are identical."
        )
    else:
        # The digest covers the served window only. Claiming the file is
        # unchanged here would be claiming something never checked.
        also = (
            " The file has been written since — a timestamp changed — and those lines "
            "are identical, but the rest of the file may not be."
        )
    text = (
        f"[repowise] Unchanged since you read it: {rel}. You were served {label}"
        f"{when} and it hashes the same now, so those bytes are already in your "
        f"context and are not repeated here.{also}\n"
        "Read it again and you get the content — this is never collapsed twice in a row."
    )
    raw_tokens = max(1, len(content) // 4)
    new_tokens = max(1, len(text) // 4)
    if new_tokens >= raw_tokens:
        return None
    return Offer(
        key=rel,
        text=text,
        raw_tokens=raw_tokens,
        new_tokens=new_tokens,
        category="reread_collapsed",
        filter_name=_SAVINGS_FILTER,
    )


def changed_notice(rel: str, prior: dict) -> str:
    """One line for a file that changed under the agent, with no Edit of ours.

    When the bytes genuinely differ the agent gets them, and saying *why* they
    differ is something the session cannot work out for itself. The stale-read
    notice covers the case where this session did the editing; this covers a
    ``git checkout``, a formatter, or another agent, which leave no trace in
    our state at all.

    Only ever emitted for the *same* requested range as the earlier Read.
    Different offsets legitimately return different bytes, and calling that a
    change would be the judgment this surface exists to avoid making.
    """
    turn = prior.get("seq")
    when = f" at tool call {turn}" if isinstance(turn, int) and turn else " earlier"
    return (
        f"[repowise] {rel} changed on disk since you read it{when}, and not through an "
        "Edit in this session — something outside it wrote the file. The content below "
        "is current; anything you concluded from the earlier read may not be."
    )
