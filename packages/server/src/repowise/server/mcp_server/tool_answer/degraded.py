"""The synthesis-less get_answer reply.

No provider and a failed call share one payload. Retrieval succeeded and all
evidence (bodies, citations, best guesses, rationale) is read live from disk
with no LLM; body selection matches the question's identifiers, not prose.
"""

from __future__ import annotations

import time

from repowise.server.mcp_server._meta import _normalize_target_path
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_answer.bodies import (
    _build_symbol_bodies,
    _gather_body_candidates,
)
from repowise.server.mcp_server.tool_answer.confidence import (
    _degraded_confidence,
    _retrieval_quality,
    file_hybrid_rank,
    lead_leaves_retrieval,
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

    An empty ``answer`` reads as a failed call and gets a usable result
    discarded. Assembled only from what the payload carries, inventing no prose
    about the question. Best evidence first: bodies, a ranked retrieval, nothing.
    """
    if symbol_bodies:
        names = ", ".join(f"`{b['name']}`" for b in symbol_bodies)
        # Never say "full" over a body the payload itself flags as cut.
        cut = any(b.get("truncated") for b in symbol_bodies)
        return (
            f"No synthesized prose ({reason}), but the evidence is here: "
            f"`symbol_bodies` carries the live source of {names}, read from the "
            "current checkout"
            + (", cut at the line cap where noted; see `continuation`. " if cut else " in full. ")
            + "Answer from that; `retrieval`, `fallback_targets` and `candidate_files` "
            "cover the wider question."
        )
    if served:
        return (
            f"No synthesized prose ({reason}), but retrieval succeeded and this "
            f"payload is usable: {served} ranked "
            f"{'hit' if served == 1 else 'hits'} in `retrieval`, the files to open "
            "in `fallback_targets`, and the wider ranked shortlist in `candidate_files`. "
            "Read those rather than starting a fresh search."
        )
    return (
        f"No synthesized prose ({reason}), and retrieval matched nothing for "
        "this question. Rephrase with an identifier or path from the codebase, "
        "or search directly."
    )


def _rationale_in_rank_order(rows: list[dict], hits: list[dict], top: str | None) -> list[dict]:
    """Rationale rows from the top-ranked file first, the rest in hybrid-rank order.

    The miner orders rows by comment score, which would let a comment-heavy hub
    lead ``answer`` over the file retrieval put first. Ties keep that order;
    a hit with no hybrid rank sorts after the ranked ones, in hit order.
    """
    top_path = _normalize_target_path(top or "")
    paths = [_normalize_target_path(h.get("target_path") or "") for h in hits]

    def _key(row: dict) -> tuple[bool, int]:
        path = _normalize_target_path(row.get("path") or "")
        rank = file_hybrid_rank(hits, path)
        if rank is None:
            rank = len(hits) + (paths.index(path) if path in paths else len(hits))
        return path != top_path, rank

    return sorted(rows, key=_key)


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

    ``degraded`` is mirrored into ``_meta``, where consumers read health
    signals. ``confidence`` is graded by :func:`_degraded_confidence` from
    ``retrieval_quality`` and whether retrieval backs the file ``answer`` leads with.
    """
    retrieval_quality = _retrieval_quality(hits, agreement_dominant)
    repo_root = _repo_root(ctx)
    symbol_bodies, _served_named_body = _build_symbol_bodies(
        _gather_body_candidates(hits, "", anchor_names=question_ids or set()),
        repo_root,
    )
    # Inlined bodies are citations in the ordinary sense.
    citations = list(dict.fromkeys(b["path"] for b in symbol_bodies))

    summary = _degraded_summary(reason, symbol_bodies, len(hits))

    best_guesses = _build_best_guesses(hits)
    # No quotes to check against here — there is no prose to quote from.
    code_rationale = _rationale_in_rank_order(
        _drop_already_surfaced(
            await _gather_code_rationale(ctx, hits, fallback_targets, question), symbol_bodies
        ),
        hits,
        best_guesses[0]["file"] if best_guesses else None,
    )
    seen = {_normalize_target_path(path) for path in citations}
    rationale_paths = [
        path
        for row in code_rationale
        if (path := row.get("path")) and _normalize_target_path(path) not in seen
    ]
    if rationale_paths:
        # Cite the ranked guesses ahead of the rationale files they outrank.
        for path in (*(g["file"] for g in best_guesses), *rationale_paths):
            if (key := _normalize_target_path(path)) not in seen:
                seen.add(key)
                citations.append(path)

    # The file ``answer`` will name first, in the projection's order.
    if symbol_bodies:
        lead = symbol_bodies[0]["path"]
    elif best_guesses:
        lead = best_guesses[0]["file"]
    else:
        lead = code_rationale[0].get("path") if code_rationale else None
    confidence = _degraded_confidence(
        reason, retrieval_quality, lead_outside_top=lead_leaves_retrieval(hits, lead)
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
        # Not on a weak retrieval, where `_meta.hint` says to refine the query
        # and two disagreeing hints are worse than one.
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

    A whole body is a terminal answer. A cut body names a withheld symbol that
    resolves, else its ``continuation``. As on the synthesised path, the
    enclosing symbol's own name is not a withheld symbol, and
    ``_first_resolvable_id`` keeps a scanner-invented id out of the hint.
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
