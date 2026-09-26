"""Assembling what get_answer returns, once the answer itself has been decided.

Every reply shape lives here except the synthesis-less ones (``degraded``): the
serve-time size cuts, the ranked shortlist that travels with every
post-retrieval return, the answer-by-union reply, the legacy abstain reply, the
value fast path, and the graded synthesis reply with the note each gate writes.

The orchestrator decides WHAT the answer is; this module decides how it is
shaped on the wire.
"""

from __future__ import annotations

import time

from repowise.server.mcp_server._answer_context import (
    is_mechanism_question as _is_mechanism_question,
)
from repowise.server.mcp_server._answer_context import (
    is_why_question as _is_why_question,
)
from repowise.server.mcp_server._meta import NO_HITS_RECOVERY_HINT as _NO_HITS_RECOVERY_HINT
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server.tool_answer.confidence import (
    _Grade,
    _is_enclosing_continuation,
)
from repowise.server.mcp_server.tool_answer.config import (
    _GATED_RETURN_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _LEAN_HIGH_DROP_KEYS,
    _UNION_MECHANISM_DEFER_ENV,
    _flag_on,
    _lean_high,
)
from repowise.server.mcp_server.tool_answer.evidence import (
    _drop_already_surfaced,
    _first_resolvable_id,
    _gather_code_rationale,
    _repo_root,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    _CANDIDATE_LIMIT,
    _candidate_justification,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    serialize_candidates as _serialize_candidates,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    serialize_hits as _serialize_hits,
)
from repowise.server.mcp_server.tool_answer.symbols import (
    build_homonym_union_bodies,
    union_defers_to_synthesis,
)

# --- Serve-time size cuts ----------------------------------------------------


def _trim_served_payload(payload: dict) -> dict:
    """Every size cut that runs on the way OUT, on both the fresh and cache paths.

    Serve-time so cached rows from older builds are trimmed too, without a
    ``_ANSWER_SCHEMA_VERSION`` bump and its re-synthesis spend. Only cuts that
    REMOVE redundancy belong here; changing what an answer says owes a bump.
    """
    _cap_candidates(payload)
    _drop_duplicated_guess_excerpts(payload)
    return payload


def _cap_candidates(payload: dict) -> dict:
    """Hold ``candidates`` to :data:`_CANDIDATE_LIMIT` rows on the way out."""
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and len(candidates) > _CANDIDATE_LIMIT:
        payload["candidates"] = candidates[:_CANDIDATE_LIMIT]
    return payload


def _drop_duplicated_guess_excerpts(payload: dict) -> dict:
    """Drop ``best_guesses[].excerpt`` where ``retrieval[]`` already carries it.

    Keyed on the duplicate actually being present: ``retrieval`` is often empty
    (high confidence, legacy abstain), and there the guess excerpt is the only
    content, so the drop stays lossless.
    """
    guesses = payload.get("best_guesses")
    if not guesses:
        return payload
    # Substring, not equality: the two blocks cut their slabs independently and
    # the retrieval one is the longer of the two where they differ.
    carried = [r["excerpt"] for r in (payload.get("retrieval") or []) if r.get("excerpt")]
    if not carried:
        return payload
    for guess in guesses:
        excerpt = guess.get("excerpt")
        if excerpt and any(excerpt in c for c in carried):
            del guess["excerpt"]
    return payload


def _apply_lean_high(payload: dict, question: str) -> dict:
    """Strip re-read evidence from a mainline high-confidence answer, in place.

    No-op unless the flag is on and confidence is high. Evidence is kept where
    it IS the answer: grounded fast paths (a ``grounding`` key) and why-questions,
    whose "because X" rests on the code_rationale / quotes this strips.
    """
    if not _lean_high() or payload.get("confidence") != "high" or payload.get("grounding"):
        return payload
    if _is_why_question(question):
        return payload
    for k in _LEAN_HIGH_DROP_KEYS:
        payload.pop(k, None)
    return payload


# --- Shared blocks -----------------------------------------------------------


def _build_best_guesses(hits: list[dict]) -> list[dict]:
    """Decision-shaped candidate list: per-file justification, score, excerpt.

    Lets the agent pick ONE file to verify on an ambiguous retrieval. ``file``
    goes through ``hit_file_path`` and unresolvable hits are skipped, since a
    spotlight or module page's ``target_path`` is not an openable file.
    """
    return [
        {
            "file": hit_file_path(h),
            "why_relevant": _candidate_justification(h),
            "score": round(h.get("score", 0.0), 3),
            # Absent rather than null: most rows carry no penalty.
            **({"domain_penalty": h["_domain_penalty"]} if h.get("_domain_penalty") else {}),
            **({"excerpt": h["excerpt"]} if h.get("excerpt") else {}),
        }
        for h in hits[:_GATED_RETURN_HITS]
        if hit_file_path(h)
    ]


def _with_candidates(payload: dict, resolved_pool: list[dict]) -> dict:
    """Attach the ranked shortlist to a payload that is about to be returned.

    Every post-retrieval early return empties ``retrieval``, but the pre-cap
    ranked pool is still free and useful, so a caller whose question tripped a
    special path is not left with less than one whose question did not. Adds to
    the payload only; no gate changes.
    """
    candidates = _serialize_candidates(resolved_pool)
    if candidates:
        payload["candidates"] = candidates
    return payload


def _no_answer_payload(note: str, *, repository, t0: float) -> dict:
    """The reply for a post-retrieval gate that has nothing to answer with.

    Used by the qualified-miss and no-hits guards; ``note`` carries the
    redirect. Callers still wrap it in :func:`_with_candidates`.
    """
    return {
        "answer": "",
        "citations": [],
        "confidence": "low",
        "note": note,
        "fallback_targets": [],
        "retrieval": [],
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_answer_hint("low"),
            repository=repository,
            targets=[],
        ),
    }


# --- Answer-by-union ---------------------------------------------------------


def _union_answer_payload(
    question: str,
    question_ids: set[str],
    homonyms: dict,
    ctx,
    repository,
    t0: float,
    retrieval_quality: str,
) -> dict | None:
    """The answer-by-union reply, or None to let synthesis handle the question.

    The question named a symbol with N>=2 undisambiguated defs, so the UNION of
    their bodies is inlined (char-budgeted) rather than a pointer list. The
    exact-name scan is the only thing that surfaces defs fuzzy retrieval missed.

    Returns None when the union is not the answer: a prose question merely
    mentioning a generic many-def method, a mechanism question whose answer
    often lives elsewhere, or unreadable bodies.
    """
    union_groups = homonyms.get("union") or {}
    if union_groups and union_defers_to_synthesis(question, question_ids, union_groups):
        union_groups = {}
    if union_groups and _flag_on(_UNION_MECHANISM_DEFER_ENV) and _is_mechanism_question(question):
        union_groups = {}
    if not union_groups:
        return None
    repo_root = _repo_root(ctx)
    union_bodies, more_defs = build_homonym_union_bodies(repo_root, union_groups)
    if not union_bodies:
        return None
    names = sorted(union_groups)
    total = sum(len(v) for v in union_groups.values())
    cited = sorted({b["path"] for b in union_bodies})
    # Returns before synthesis, so no confidence gate sees it. The withheld
    # dependency test cannot fire here (the question names the SERVED symbol),
    # and the bodies ARE the answer, so truncation alone caps the grade.
    union_truncated = any(b.get("truncated") for b in union_bodies)
    _union_confidence = "medium" if union_truncated else "high"
    note = (
        f"{total} definition(s) of {', '.join(names)} exist (exact-name "
        f"index scan; this is the complete set of DEFINITIONS). "
        f"{len(union_bodies)} inlined below in symbol_bodies as live "
        "source"
    )
    note += (
        "; use them directly, no verification Read."
        if not union_truncated
        else ". At least one body was truncated: see "
             "symbol_bodies[].withheld_symbols for what was not served, "
             "and call get_symbol with the continuation before relying "
             "on behaviour you cannot see."
    )
    if more_defs:
        note += (
            f" {len(more_defs)} more are in more_definitions; call "
            "get_symbol with the listed id, do NOT Read."
        )
    note += (
        " If the question was about something other than these definitions, "
        "candidates holds the files retrieval ranked for it."
    )
    payload: dict = {
        "answer": (
            f"`{', '.join(names)}` has {total} definition(s) in this repo; "
            "all are inlined in symbol_bodies below. They are distinct "
            "implementations, so pick the one for your context."
        ),
        "citations": cited,
        "confidence": _union_confidence,
        # Rates the `candidates` shortlist, not the exact-name bodies.
        "retrieval_quality": retrieval_quality,
        "grounding": "exact_symbol",
        "symbol_bodies": union_bodies,
        "fallback_targets": [b["path"] for b in union_bodies],
        "retrieval": [],
        "note": note,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_answer_hint(_union_confidence),
            repository=repository,
            targets=cited,
        ),
    }
    if more_defs:
        payload["more_definitions"] = more_defs
    return payload


# --- Pre-synthesis replies ---------------------------------------------------


async def build_abstain_payload(
    *, question: str, ctx, hits: list[dict], fallback_targets: list[str], repository, t0: float
) -> dict:
    """The legacy abstain reply (REPOWISE_ANSWER_ALWAYS_SYNTHESIZE=off).

    Retrieval is ambiguous, so synthesis is skipped and best_guesses carry
    excerpts: pointers alone send the agent on a Grep/Read spree, excerpts turn
    the miss into "pick one candidate, verify with at most one Read".
    """
    best_guesses = _build_best_guesses(hits)
    # Source-comment rationale the wiki/decision corpus missed.
    code_rationale = await _gather_code_rationale(ctx, hits, fallback_targets, question)
    has_excerpts = any("excerpt" in g for g in best_guesses)
    gated: dict = {
        "answer": "",
        "citations": [],
        "confidence": "low",
        "retrieval_quality": "weak",
        "best_guesses": best_guesses,
        "next_action_hint": (
            (
                f"Start from the excerpt of {best_guesses[0]['file']} — "
                "it scored highest; Read the file only to verify "
                "details the excerpt does not settle."
                if has_excerpts
                else f"Read {best_guesses[0]['file']} first — it scored "
                "highest but retrieval was ambiguous, so verify "
                "before answering."
            )
            if best_guesses
            else _NO_HITS_RECOVERY_HINT
        ),
        "fallback_targets": fallback_targets,
        "retrieval": [],
        "note": (
            "Multiple plausible candidates — synthesis skipped to "
            "avoid anchoring on a wrong frame. Each best_guess entry "
            "names why that file is in the running"
            + (", and its excerpt carries that page's actual content." if has_excerpts else ".")
        ),
    }
    if code_rationale:
        gated["code_rationale"] = code_rationale
        gated["note"] += (
            " code_rationale carries rationale comments mined from the "
            "candidate source — they may already answer the question."
        )
    gated["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=_answer_hint("low"),
        repository=repository,
        targets=fallback_targets,
    )
    return gated


def build_value_payload(
    *, extraction: dict, hits: list[dict], fallback_targets: list[str], repository, t0: float
) -> dict:
    """The value-extraction fast path reply.

    The verbatim assignment line (read live by the hydrator) IS the answer.
    Not cached: extraction is cheap and must reflect the current source.
    """
    top_score_fp = hits[0].get("score", 0.0) if hits else 0.0
    answer_text = extraction["answer"]
    if extraction.get("value_source"):
        answer_text += "\n\n" + extraction["value_source"]
    return {
        "answer": answer_text,
        "citations": [extraction["file"]],
        "confidence": "high",
        "retrieval_quality": (
            "high" if top_score_fp >= _HIGH_CONFIDENCE_SCORE_FLOOR else "partial"
        ),
        "grounding": "extracted",
        "fallback_targets": fallback_targets,
        "retrieval": [],
        "note": (
            "Extracted verbatim from the live source line — no LLM "
            "synthesis involved. Cite directly; no verification "
            "Read needed. candidates holds the files retrieval ranked, "
            "for the wider question the value sits inside."
        ),
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_answer_hint("high"),
            repository=repository,
            targets=[extraction["file"], *fallback_targets],
        ),
    }


# --- The graded synthesis reply ----------------------------------------------


async def build_synthesized_payload(
    *,
    question: str,
    answer_text: str,
    citations: list[str],
    grade: _Grade,
    retrieval_quality: str,
    hits: list[dict],
    fallback_targets: list[str],
    symbol_bodies: list[dict],
    served_named_body: bool,
    quotes: list[dict],
    dominant: bool,
    ctx,
    repository,
    exclude_spec,
) -> dict:
    """Shape the synthesised answer, with the note whichever gate fired writes.

    Two branches. A hedge is about the PROSE, so it keeps a lean retrieval block
    and redirects to whatever real evidence was resolved. Everything else takes
    the graded branch, where the ``retrieval`` block is confidence-conditional
    and the note is written by the first gate finding that applies.
    """
    confidence = grade.confidence
    if grade.hedged:
        payload = await _hedged_payload(
            question=question,
            answer_text=answer_text,
            citations=citations,
            confidence=confidence,
            retrieval_quality=retrieval_quality,
            hits=hits,
            fallback_targets=fallback_targets,
            symbol_bodies=symbol_bodies,
            served_named_body=served_named_body,
            ctx=ctx,
        )
    else:
        payload = await _graded_payload(
            question=question,
            answer_text=answer_text,
            citations=citations,
            grade=grade,
            retrieval_quality=retrieval_quality,
            hits=hits,
            fallback_targets=fallback_targets,
            symbol_bodies=symbol_bodies,
            quotes=quotes,
            ctx=ctx,
            repository=repository,
            exclude_spec=exclude_spec,
        )

    # Non-dominant retrieval: the prose ships with the abstain path's evidence
    # (best_guesses, code_rationale) and a caveat, "answered, but verify".
    if not dominant:
        payload.setdefault("best_guesses", _build_best_guesses(hits))
        if "code_rationale" not in payload:
            _cr = await _gather_code_rationale(ctx, hits, fallback_targets, question)
            _cr = _drop_already_surfaced(_cr, symbol_bodies, quotes)
            if _cr:
                payload["code_rationale"] = _cr
        if grade.high_reason == "symbol_body":
            # High rests on the served body, not the ranking, so the caveat
            # scopes doubt to the page choice rather than pointing at best_guesses.
            _caveat = (
                "Retrieval was ambiguous (no single dominant page), so the "
                "candidates listed are a ranking, not a finding — the confidence "
                "above rests on the symbol body served in this payload, not on "
                "which page ranked first."
            )
        else:
            _caveat = (
                "Retrieval was ambiguous (no single dominant page), so this was "
                f"synthesized across several candidates and held at {confidence} "
                "confidence — verify against best_guesses"
                + (" or the code_rationale comments." if payload.get("code_rationale") else ".")
            )
        payload["note"] = (payload["note"] + " " + _caveat) if payload.get("note") else _caveat
        if payload.get("best_guesses") and grade.high_reason != "symbol_body":
            payload.setdefault(
                "next_action_hint",
                f"Verify against {payload['best_guesses'][0]['file']} — it scored "
                "highest, but retrieval was ambiguous across the top candidates.",
            )
    return payload


async def _hedged_payload(
    *,
    question: str,
    answer_text: str,
    citations: list[str],
    confidence: str,
    retrieval_quality: str,
    hits: list[dict],
    fallback_targets: list[str],
    symbol_bodies: list[dict],
    served_named_body: bool,
    ctx,
) -> dict:
    """The reply for an answer whose own prose admits it could not answer.

    The retrieval block stays lean but non-empty: the ranked hits say WHICH
    source to read, and a hit absent from the prose-drawn citations would
    otherwise vanish entirely.
    """
    payload = {
        "answer": answer_text,
        "citations": citations,
        "confidence": confidence,
        "retrieval_quality": retrieval_quality,
        "fallback_targets": fallback_targets[:5],
        # The low branch's excerpt budget. Safe to cut at build time: hedged rows
        # are never served from cache, so no stored row keeps a wider shape.
        "retrieval": _serialize_hits(
            hits, limit=5, lean_symbols=True, excerpt_rows=_GATED_RETURN_HITS
        ),
        "note": (
            "Synthesis hedged: the LLM could not ground the question in "
            "the indexed wiki. Read one of fallback_targets to answer."
        ),
    }
    if symbol_bodies:
        payload["symbol_bodies"] = symbol_bodies
        if served_named_body:
            # The hedge is about the prose; the named body is the answer.
            payload["grounding"] = "symbol_body"
            payload["note"] = (
                "Synthesis hedged on the prose, but symbol_bodies carries "
                "the full live body of the symbol(s) you named — cite that "
                "directly, no verification Read needed."
            )
        else:
            payload["note"] = (
                "Synthesis hedged, but symbol_bodies carries the live body "
                "of the symbol(s) you named — read that to answer."
            )
    # A hedge often means the rationale is a code comment, not wiki prose. A
    # comment already visible in symbol_bodies must not surface twice.
    code_rationale = await _gather_code_rationale(ctx, hits, fallback_targets, question)
    code_rationale = _drop_already_surfaced(code_rationale, symbol_bodies)
    if code_rationale:
        payload["code_rationale"] = code_rationale
        payload["note"] += (
            " code_rationale carries rationale comments mined from the "
            "cited source — they may already answer the question."
        )
    return payload


def _high_confidence_note(grade: _Grade, tail: str) -> str:
    """The high-confidence note, written from the reason the grade was reached.

    Each branch quotes only the measurement its own tier made (see
    :func:`dominance_reason`). Only the dominance tiers and ``"symbol_body"``
    reach *tail*, the "need not re-read" line; ``"grounding"`` shows the prose
    is not fabricated, not that the page is the right one.
    """
    if grade.high_reason == "symbol_body":
        return (
            "High confidence: symbol_bodies below carries the live body of the "
            "symbol you named, so the answer rests on source in this payload "
            "rather than on the ranking, which was ambiguous (top score "
            f"{grade.top_score:.2f}, runner-up {grade.second_score:.2f}). " + tail
        )
    if grade.high_reason == "grounding":
        return (
            "High confidence: every mechanism the answer names appears in the "
            "cited source, so the prose is not fabricated. Retrieval was still "
            f"ambiguous (top score {grade.top_score:.2f}, runner-up "
            f"{grade.second_score:.2f}), so verify which file answers the "
            "question rather than the wording of the answer."
        )
    if grade.high_reason == "gap":
        return (
            "High confidence: the top retrieval result clearly dominates, by "
            f"{grade.top_score - grade.second_score:.2f} points over the "
            f"runner-up (top score {grade.top_score:.2f}). Both scores are "
            "strong, so the gap is the measure here and not the ratio. " + tail
        )
    if grade.high_reason == "agreement":
        return (
            "High confidence: both retrievers independently rank this page at "
            "the top, which is the measure here — fused scores are compressed, "
            f"so the {grade.ratio:.2f}x ratio understates the agreement "
            f"(top score {grade.top_score:.2f}). " + tail
        )
    if grade.high_reason == "sole_hit":
        return (
            "High confidence: one page matched, so there was no competing "
            f"candidate to be ambiguous against (top score {grade.top_score:.2f}). "
            + tail
        )
    return (
        "High confidence: top retrieval result clearly dominates "
        f"(dominance ratio {grade.ratio:.2f}x, top score {grade.top_score:.2f}). " + tail
    )


async def _graded_payload(
    *,
    question: str,
    answer_text: str,
    citations: list[str],
    grade: _Grade,
    retrieval_quality: str,
    hits: list[dict],
    fallback_targets: list[str],
    symbol_bodies: list[dict],
    quotes: list[dict],
    ctx,
    repository,
    exclude_spec,
) -> dict:
    """The non-hedged reply, with the note the first applicable gate finding writes.

    The retrieval block is confidence-conditional: empty at high (citations
    suffice), two truncated hits at medium, and a lean block at low (symbols
    without docstrings/excerpts, the largest and least-used part).
    """
    confidence = grade.confidence
    if confidence == "high":
        retrieval_view: list[dict] = []
    elif confidence == "medium":
        retrieval_view = _serialize_hits(
            hits, limit=2, summary_chars=160, symbols_for_expanded=False
        )
    else:
        retrieval_view = _serialize_hits(hits, limit=_GATED_RETURN_HITS, lean_symbols=True)
    payload = {
        "answer": answer_text,
        "citations": citations,
        "confidence": confidence,
        "retrieval_quality": retrieval_quality,
        "fallback_targets": fallback_targets,
        "retrieval": retrieval_view,
    }
    if quotes:
        payload["quotes"] = quotes
    if symbol_bodies:
        payload["symbol_bodies"] = symbol_bodies
    if grade.high_reason == "symbol_body":
        # Also what stops `_apply_lean_high` stripping the body the note cites.
        payload["grounding"] = "symbol_body"
    if grade.ungrounded_values:
        payload["note"] = (
            f"Value-grounding gate: the answer asserts {grade.ungrounded_values} "
            "but none of these appear in any retrieved excerpt — the "
            "value(s) may be synthesised. Read "
            f"{fallback_targets[0] if fallback_targets else 'the cited file'} "
            "to confirm before citing a number."
        )
        if fallback_targets:
            payload["next_action_hint"] = (
                f"Read {fallback_targets[0]} and verify the asserted value(s) "
                f"{grade.ungrounded_values} against the live source."
            )
    elif grade.frame_unsupported:
        # The real mechanism likely lives in uncaptured code; mine source
        # comments so the downgrade ships a lead, not just a warning.
        code_rationale = await _gather_code_rationale(ctx, hits, fallback_targets, question)
        code_rationale = _drop_already_surfaced(code_rationale, symbol_bodies, quotes)
        if code_rationale:
            payload["code_rationale"] = code_rationale
        payload["note"] = (
            f"Claim-support gate: the answer names {grade.frame_unsupported} as the "
            "mechanism, but that term is absent from every retrieved excerpt "
            "— it may be conflated with a different function/file. Downgraded "
            "to medium; verify against "
            f"{fallback_targets[0] if fallback_targets else 'the cited source'}"
            + (" or the code_rationale comments below." if code_rationale else ".")
        )
        payload["next_action_hint"] = (
            f"Verify the mechanism before citing: the asserted term(s) "
            f"{grade.frame_unsupported} are not in the retrieved material."
        )
    elif grade.exclusivity_over_truncated:
        # Names the axis of doubt, not the check that triggered it.
        payload["note"] = (
            "Answer may not cover every relevant site: a cited symbol's body "
            "was truncated and the answer makes an unqualified causal claim. "
            "Other functions may also participate; call get_symbol for the "
            "full body or verify against "
            f"{fallback_targets[0] if fallback_targets else 'the cited source'}."
        )
        if fallback_targets:
            payload["next_action_hint"] = (
                f"Read {fallback_targets[0]} to verify whether other functions "
                "participate beyond what the truncated symbol body shows."
            )
    elif grade.withheld_implicated:
        await _attach_withheld_note(
            payload,
            withheld_implicated=grade.withheld_implicated,
            symbol_bodies=symbol_bodies,
            ctx=ctx,
            repository=repository,
            exclude_spec=exclude_spec,
        )
    elif grade.lookup_body_truncated:
        # The lookup gate fired; without this the demotion would ship no note.
        cut = grade.named_body_cut
        payload["note"] = (
            f"You asked for {cut['name']} and its body did not fit: "
            f"lines {cut['lines'][0]}-{cut['lines'][1]} are "
            f"served and it continues at {cut['continuation']}. Held at "
            "medium because on a symbol lookup the part you cannot see is part of "
            "the answer."
        )
        payload["next_action_hint"] = (
            f"call get_symbol id='{cut['continuation']}' for the rest of {cut['name']}"
        )
    elif confidence == "high":
        # "No hedging" is not cited as evidence: the prompt forbids hedging, so
        # reading its absence back as a signal would be circular.
        _tail = (
            "Cite this answer; do not re-read the source unless a specific "
            "detail is missing."
            if not any(b.get("truncated") for b in symbol_bodies)
            # Never say "skip re-reading" when the payload admits it withheld
            # part of a cited body.
            else "Some cited bodies were truncated; see "
                 "symbol_bodies[].withheld_symbols for what was not served."
        )
        payload["note"] = _high_confidence_note(grade, _tail)

    # A concept-anchored hit's comment is the literal rationale for a "why is
    # X = <number>" question; surface it unless a gate already did.
    if "code_rationale" not in payload and any(h.get("_concept_anchored") for h in hits):
        concept_rationale = await _gather_code_rationale(ctx, hits, fallback_targets, question)
        concept_rationale = _drop_already_surfaced(concept_rationale, symbol_bodies, quotes)
        if concept_rationale:
            payload["code_rationale"] = concept_rationale
    return payload


async def _attach_withheld_note(
    payload: dict,
    *,
    withheld_implicated: list[str],
    symbol_bodies: list[dict],
    ctx,
    repository,
    exclude_spec,
) -> None:
    """Write the note and next action for a withheld symbol the answer depends on.

    An enclosing-symbol continuation (see :func:`_is_enclosing_continuation`)
    is pointed at by its ``continuation``, itself a valid get_symbol id
    ("path.py:174-221"), rather than reported as not served.
    """
    _implicated = set(withheld_implicated)
    _continuing = [b for b in symbol_bodies if _is_enclosing_continuation(b, _implicated)]
    _continuing_names = {b["name"] for b in _continuing}
    _absent = [n for n in withheld_implicated if n not in _continuing_names]
    # Only advertise an id get_symbol can answer: the regex scanner can name a
    # non-symbol, and this id becomes the next action. Names are still reported.
    _hint_id = await _first_resolvable_id(
        [
            s["symbol_id"]
            for b in symbol_bodies
            for s in (b.get("withheld_symbols") or [])
            if s.get("name") in set(_absent)
        ],
        ctx,
        repository,
        exclude_spec,
    )
    _parts = []
    if _absent:
        _parts.append(
            "Part of the code this answer depends on was not served: "
            f"{', '.join(_absent)}."
            + (
                " Continue in this tool with "
                f"get_symbol id='{_hint_id}' before relying on the mechanism."
                if _hint_id
                else ""
            )
        )
    # Qualify by path only when the same name was cut in more than one file.
    _dupe = len({_b["name"] for _b in _continuing}) < len(_continuing)
    for _b in _continuing:
        _who = f"{_b['name']} ({_b['path']})" if _dupe else _b["name"]
        _parts.append(
            f"{_who} was served through line {_b['lines'][1]}; the rest "
            f"of its body is at {_b['continuation']}."
        )
    payload["note"] = " ".join(_parts)
    payload["next_action_hint"] = (
        f"call get_symbol id='{_hint_id}' for the withheld body"
        if _hint_id
        else (
            f"call get_symbol id='{_continuing[0]['continuation']}' for the rest "
            f"of {_continuing[0]['name']}"
            if _continuing
            else "request the withheld body before citing"
        )
    )
