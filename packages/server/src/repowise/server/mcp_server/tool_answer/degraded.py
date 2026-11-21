"""The synthesis-less get_answer reply.

No provider and a failed call share one payload. Retrieval succeeded and all
evidence (bodies, citations, best guesses, rationale) is read live from disk
with no LLM; body selection matches the question's identifiers, not prose.
"""

from __future__ import annotations

import logging
import time

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._code_rationale import can_lead_answer
from repowise.server.mcp_server._entry_trace import (
    _PATH_MAX_ROWS,
    call_path,
    call_route,
    is_call_path_question,
    names_destination,
    route_ends,
    symbol_at,
    symbols_named,
)
from repowise.server.mcp_server._meta import _normalize_target_path, served_root
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import keyless_note_meta as _keyless_note_meta
from repowise.server.mcp_server.tool_answer.bodies import (
    _build_symbol_bodies,
    _gather_body_candidates,
)
from repowise.server.mcp_server.tool_answer.callers import (
    NO_EVIDENCE,
    CallerEvidence,
    attach_graph_callers,
)
from repowise.server.mcp_server.tool_answer.confidence import (
    _degraded_confidence,
    _retrieval_quality,
    file_hybrid_rank,
    lead_leaves_retrieval,
)
from repowise.server.mcp_server.tool_answer.config import _BODY_NOTE, _RATIONALE_NOTE
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

_log = logging.getLogger("repowise.mcp.answer")

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
    graph_callers: CallerEvidence = NO_EVIDENCE,
) -> dict:
    """Shape a synthesis-less get_answer response.

    ``degraded`` is mirrored into ``_meta``, where consumers read health
    signals. ``confidence`` is graded by :func:`_degraded_confidence` from
    ``retrieval_quality`` and whether retrieval backs the file ``answer`` leads with.
    """
    retrieval_quality = _retrieval_quality(hits, agreement_dominant, question)
    repo_root = _repo_root(ctx)
    # A named body that leaves out the rest of the question is served as
    # evidence, never as the answer the payload leads with.
    symbol_bodies, served_named_body = _build_symbol_bodies(
        _gather_body_candidates(hits, "", anchor_names=question_ids or set()),
        repo_root,
        question=question,
        hits=hits,
    )
    # Inlined bodies are citations in the ordinary sense.
    citations = list(dict.fromkeys(b["path"] for b in symbol_bodies))

    summary = _degraded_summary(reason, symbol_bodies if served_named_body else [], len(hits))

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
    if served_named_body:
        lead = symbol_bodies[0]["path"]
    elif best_guesses:
        lead = best_guesses[0]["file"]
    else:
        lead = next((r.get("path") for r in code_rationale[:1] if can_lead_answer(r)), None)
    confidence = _degraded_confidence(
        reason,
        retrieval_quality,
        lead_outside_top=lead_leaves_retrieval(hits, lead),
        graph_answered=graph_callers.graph_answered,
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
    attach_graph_callers(payload, graph_callers)
    if code_rationale:
        payload["code_rationale"] = code_rationale
        payload["note"] += _RATIONALE_NOTE
    if symbol_bodies:
        payload["symbol_bodies"] = symbol_bodies
    if served_named_body:
        payload["grounding"] = "symbol_body"
        payload["next_action_hint"] = await _degraded_next_action(
            symbol_bodies, ctx, repository, exclude_spec
        )
        payload["note"] += _BODY_NOTE
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
                has_bodies=served_named_body,
            ),
            repository=repository,
            targets=[*citations, *fallback_targets],
        ),
        "degraded": reason,
    }
    # The upgrade line rides on the weak keyless replies only: a body or a
    # medium grade already gives the agent something to act on.
    if reason == "no-llm-provider" and (confidence == "low" or not symbol_bodies):
        payload["_meta"].update(
            await _keyless_note_meta(
                served_root(repository),
                session_factory=getattr(ctx, "session_factory", None),
                repo_id=getattr(repository, "id", None),
            )
        )
    await _attach_call_path(
        payload, question, question_ids or set(), symbol_bodies, ctx, repository, exclude_spec
    )
    return _with_candidates(payload, resolved_pool if resolved_pool is not None else hits)


async def _attach_call_path(
    payload: dict, question: str, question_ids: set[str], symbol_bodies: list[dict],
    ctx, repository, exclude_spec,
) -> None:
    """Swap the named body for the call path when the question asks how a call travels.

    The trust hint is re-read without bodies, and the upgrade line a body
    held back is added, so ``_meta`` describes the reply served.
    """
    start, hops = await _call_path_from_named(
        payload["degraded"], question, question_ids, symbol_bodies, ctx, repository, exclude_spec
    )
    if not hops:
        return
    _serve_call_path(payload, start, hops)
    meta = payload["_meta"]
    meta["hint"] = _answer_hint(
        payload["confidence"],
        degraded=payload["degraded"],
        retrieval_quality=payload.get("retrieval_quality"),
        has_bodies=False,
    )
    if "keyless_note" not in meta:
        meta.update(
            await _keyless_note_meta(
                served_root(repository),
                session_factory=getattr(ctx, "session_factory", None),
                repo_id=getattr(repository, "id", None),
            )
        )


def _named_body(symbol_bodies: list[dict], question_ids: set[str]) -> dict | None:
    """The first served body whose symbol the question names."""
    wanted = {q.lower() for q in question_ids} | {q.rsplit(".", 1)[-1].lower() for q in question_ids}
    for body in symbol_bodies:
        name = str(body.get("name") or "")
        if name.lower() in wanted or name.rsplit(".", 1)[-1].lower() in wanted:
            return body
    return None


async def _call_path_from_named(
    reason: str, question: str, question_ids: set[str], symbol_bodies: list[dict],
    ctx, repository, exclude_spec,
) -> tuple[str, list[str]]:
    """``(where the hops run, hops)`` for a keyless question about how a named call travels on.

    Without a model, the body of the symbol the question starts from answers
    "what is it", not "where does it go"; the hops do. A question that names
    both ends ("how does X reach Y") gets the route between them. Otherwise,
    or when the graph holds no such route, the hops out of (or into) the named
    symbol whose body was served, when there are at least two. Empty unless
    the question has that shape.
    """
    if reason != "no-llm-provider" or ctx is None:
        return "", []
    ends = route_ends(question, question_ids)
    if ends is None and not is_call_path_question(question):
        return "", []
    no_route = ""
    if ends is not None:
        start, wanted = ends
        try:
            async with get_session(ctx.session_factory) as session:
                route, reached = await _route_between(
                    session, repository.id, start, wanted, exclude_spec
                )
        except Exception:  # the walk below still answers
            _log.debug("get_answer: call route failed", exc_info=True)
            route, reached = None, []
        if route:
            return f"on the route from {start} to {', '.join(reached)}", route
        if route is not None:
            no_route = f" (no route to {', '.join(wanted)} found)"
    body = _named_body(symbol_bodies, question_ids)
    if body is None or not is_call_path_question(question):
        return "", []
    try:
        async with get_session(ctx.session_factory) as session:
            way, hops = await _hops_around(session, repository.id, body, question, exclude_spec)
            if len(hops) >= 2:
                return f"{way} {body['name']} {body['path']}:{body['lines'][0]}{no_route}", hops
    except Exception:  # the body stays the reply
        _log.debug("get_answer: call path failed", exc_info=True)
    return "", []


async def _route_between(
    session, repo_id: str, start: str, wanted: list[str], exclude_spec
) -> tuple[list[str] | None, list[str]]:
    """``(rows, names reached)``: the route from *start* to each name in *wanted*,
    shared hops once, at most as many rows as the walk serves.

    Rows are ``None`` when *start* names no indexed symbol, empty when no route is found.
    """
    starts = await symbols_named(session, repo_id, start)
    if not starts:
        return None, []
    rows: dict[str, None] = {}
    reached: list[str] = []
    for name in wanted:
        finals = await symbols_named(session, repo_id, name)
        route = await call_route(session, repo_id, starts, finals, exclude_spec)
        if route:
            reached.append(name)
            rows.update(dict.fromkeys(route))
    return list(rows)[:_PATH_MAX_ROWS], reached


async def _hops_around(session, repo_id: str, body: dict, question: str, exclude_spec) -> tuple[str, list[str]]:
    """``("out of" | "into", hops)`` walked from the symbol *body* defines."""
    if not isinstance(body.get("lines"), list):
        return "", []
    root = await symbol_at(session, repo_id, body["path"], body["lines"][0])
    if root is None:
        return "", []
    upstream = names_destination(question, str(body.get("name") or ""))
    hops = await call_path(session, repo_id, root, exclude_spec, upstream=upstream, question=question)
    return ("into" if upstream else "out of"), hops


def _serve_call_path(payload: dict, start: str, hops: list[str]) -> None:
    """Serve the hops in place of the named body and the rationale."""
    payload["call_path"] = hops
    for key in ("symbol_bodies", "code_rationale", "grounding"):
        payload.pop(key, None)
    payload["call_path_from"] = start
    # The caller's note stays; the sentences about what the hops replaced go.
    note = str(payload.get("note") or "")
    for gone in (_RATIONALE_NOTE, _BODY_NOTE):
        note = note.replace(gone, "")
    payload["note"] = note
    payload["next_action_hint"] = "Read the hops the question asks about at their lines."


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
