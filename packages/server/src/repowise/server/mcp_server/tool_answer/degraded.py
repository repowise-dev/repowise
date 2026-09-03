"""The synthesis-less get_answer reply.

Both ways synthesis can be missing — no provider resolvable, or the call failed —
return the same payload from the same evidence. Only synthesis is missing here:
retrieval ran, ranked the corpus and succeeded, and everything below the provider
check (bodies, citations, best guesses, mined rationale, the next action) is read
live off disk from index anchors and needs no LLM. The only thing that coupled
that evidence to synthesis was that body selection matched against the answer
text, so here the question's own identifiers stand in.
"""

from __future__ import annotations

import time

from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_answer.bodies import (
    _build_symbol_bodies,
    _gather_body_candidates,
)
from repowise.server.mcp_server.tool_answer.confidence import (
    _degraded_confidence,
    _retrieval_quality,
)
from repowise.server.mcp_server.tool_answer.evidence import (
    _drop_already_surfaced,
    _first_resolvable_id,
    _gather_code_rationale,
    _repo_root,
)
from repowise.server.mcp_server.tool_answer.payload import (
    _build_best_guesses,
    _with_candidates,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    serialize_hits as _serialize_hits,
)


def _degraded_summary(reason: str, symbol_bodies: list[dict], served: int) -> str:
    """The ``answer`` sentence a synthesis-less payload states about itself.

    An empty ``answer`` beside a working retrieval reads as a failed call rather
    than a partial one, and a reader who takes it at face value discards a usable
    result and starts over. So the sentence is assembled from what the payload
    actually carries — it can never claim more than it has, and it invents no
    prose about the question itself, that being precisely the part that needs a
    provider. Three cases, best evidence first: bodies in hand, a ranked
    retrieval, or nothing.
    """
    if symbol_bodies:
        names = ", ".join(f"`{b['name']}`" for b in symbol_bodies)
        # Never say "full" over a body the payload itself flags as cut. The
        # entry carries `truncated` and a `continuation` two keys away, so the
        # claim is refutable from inside the same response.
        cut = any(b.get("truncated") for b in symbol_bodies)
        return (
            f"No synthesized prose ({reason}), but the evidence is here: "
            f"`symbol_bodies` carries the live source of {names}, read from the "
            "current checkout"
            + (", cut at the line cap where noted; see `continuation`. " if cut else " in full. ")
            + "Answer from that; `retrieval`, `fallback_targets` and `candidates` "
            "cover the wider question."
        )
    if served:
        return (
            f"No synthesized prose ({reason}), but retrieval succeeded and this "
            f"payload is usable: {served} ranked "
            f"{'hit' if served == 1 else 'hits'} in `retrieval`, the files to open "
            "in `fallback_targets`, and the wider ranked shortlist in `candidates`. "
            "Read those rather than starting a fresh search."
        )
    return (
        f"No synthesized prose ({reason}), and retrieval matched nothing for "
        "this question. Rephrase with an identifier or path from the codebase, "
        "or search directly."
    )


async def _degraded_payload(
    *,
    reason: str,
    note: str,
    question: str,
    hits: list[dict],
    fallback_targets: list[str],
    repository,
    t0: float,
    ctx=None,
    question_ids: set[str] | None = None,
    exclude_spec=None,
    agreement_dominant: bool = False,
    resolved_pool: list[dict] | None = None,
) -> dict:
    """Shape a synthesis-less get_answer response.

    ``degraded`` is mirrored into ``_meta`` because consumers read freshness and
    health signals from there; the failure path used to set only the top-level
    key, so a caller watching ``_meta`` saw a normal empty answer.

    ``confidence`` is graded by :func:`_degraded_confidence` from the retrieval
    this payload actually served, not pinned. It rates what the caller can act on
    without further work, which is the same thing it rates on the keyed path; the
    boilerplate ``answer`` is why "high" is unreachable here rather than why the
    field says nothing. ``retrieval_quality`` rates the retrieval on its own and
    is what the grade is derived from, so the two can never disagree.
    """
    retrieval_quality = _retrieval_quality(hits, agreement_dominant)
    confidence = _degraded_confidence(reason, retrieval_quality)
    repo_root = _repo_root(ctx)
    symbol_bodies, _served_named_body = _build_symbol_bodies(
        _gather_body_candidates(hits, "", anchor_names=question_ids or set()),
        repo_root,
    )
    # Cite what is actually in hand. `[]` was right when the payload carried no
    # source; with bodies inlined the paths they were read from are citations in
    # the ordinary sense, and a caller filtering on non-empty `citations` stops
    # discarding this reply.
    citations = list(dict.fromkeys(b["path"] for b in symbol_bodies))

    summary = _degraded_summary(reason, symbol_bodies, len(hits))

    best_guesses = _build_best_guesses(hits)
    # No quotes to check against here — there is no prose to quote from.
    code_rationale = _drop_already_surfaced(
        await _gather_code_rationale(ctx, hits, fallback_targets, question), symbol_bodies
    )
    citations.extend(
        path
        for row in code_rationale
        if (path := row.get("path")) and path not in citations
    )

    payload: dict = {
        "answer": summary,
        "citations": citations,
        "confidence": confidence,
        "retrieval_quality": retrieval_quality,
        "degraded": reason,
        "fallback_targets": fallback_targets,
        "retrieval": _serialize_hits(hits),
        "note": note,
    }
    if best_guesses:
        payload["best_guesses"] = best_guesses
    if code_rationale:
        payload["code_rationale"] = code_rationale
        payload["note"] += (
            " code_rationale carries rationale comments mined from the candidate "
            "source — they may already answer the question."
        )
    if symbol_bodies:
        payload["symbol_bodies"] = symbol_bodies
        payload["grounding"] = "symbol_body"
        payload["next_action_hint"] = await _degraded_next_action(
            symbol_bodies, ctx, repository, exclude_spec
        )
        payload["note"] += (
            " symbol_bodies carries the live body of the symbol(s) you named, so "
            "answer from that rather than re-reading the file."
        )
    elif best_guesses and retrieval_quality != "weak":
        # No body, so the next step is a choice between files. Not on a weak
        # retrieval: `_meta.hint` there says "refine the query rather than
        # reading these files in order", and two hints that disagree are worse
        # than one. Names `best_guesses`, not its excerpt — that copy is dropped
        # on the way out, since this path always has a populated `retrieval`.
        payload["next_action_hint"] = (
            f"Start from {best_guesses[0]['file']} — it ranked highest, and "
            "best_guesses says why each candidate is in the running."
        )
    payload["_meta"] = {
        **_build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_answer_hint(
                confidence,
                degraded=reason,
                retrieval_quality=retrieval_quality,
                has_bodies=bool(symbol_bodies),
            ),
            repository=repository,
            targets=[*citations, *fallback_targets],
        ),
        "degraded": reason,
    }
    return _with_candidates(payload, resolved_pool if resolved_pool is not None else hits)


async def _degraded_next_action(symbol_bodies: list[dict], ctx, repository, exclude_spec) -> str:
    """The one next step a degraded payload with bodies in hand should name.

    A whole body is a terminal answer, so say so rather than sending the caller
    to Read a file it already holds. A cut body is not: name the continuation, or
    a withheld symbol when one resolves.

    Two filters on the withheld ids, both already established on the synthesised
    path. A withheld entry carrying the served body's OWN name is the enclosing
    symbol continuing past the cut, not something that never arrived, so it is
    dropped and the ``continuation``, which fetches exactly the missing part, is
    the pointer. And the ids come from a regex scanner over source lines, so it
    can name something that is not a symbol at all: ``_first_resolvable_id``
    keeps a fabricated id from becoming the next action here exactly as it does
    there.
    """
    cut = next((b for b in symbol_bodies if b.get("continuation")), None)
    if cut is None:
        return (
            f"Read the {symbol_bodies[0]['name']} body in symbol_bodies: it is the "
            "full live source, so no follow-up call is needed."
        )
    hint_id = await _first_resolvable_id(
        [
            s["symbol_id"]
            for s in (cut.get("withheld_symbols") or [])
            if s.get("symbol_id") and s.get("name") != cut["name"]
        ],
        ctx,
        repository,
        exclude_spec,
    )
    return (
        f"{cut['name']} was served through line {cut['lines'][1]}; call get_symbol "
        + (
            f"id='{hint_id}' for the withheld body."
            if hint_id
            else f"id='{cut['continuation']}' for the rest of it."
        )
    )
