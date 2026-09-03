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

    Serve-time rather than build-time, and that is the whole point. A cut applied
    where the payload is assembled reaches only fresh answers: a cache row
    written by an older build keeps the old shape until ``_ANSWER_SCHEMA_VERSION``
    moves, and bumping that invalidates every user's answer cache — re-synthesis,
    i.e. real provider spend — to change the size of a block. Trimming on the way
    out fixes old and new rows alike and costs nobody a re-synthesis.

    Anything that only REMOVES redundancy belongs here. Anything that changes
    what an answer says does not, and still owes a schema bump.
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

    Both blocks slice the same page excerpt for the same file, so when both are
    present the guess copy is byte-for-byte redundant.

    **Conditional, and the condition matters.** ``retrieval`` is
    confidence-gated and shrinks to nothing as the prose gets more trustworthy;
    the legacy abstain path ships ``retrieval: []`` outright. On those responses
    the guess excerpt is the only content in the payload, not a duplicate of
    anything. So the drop is keyed on the duplicate actually being present, which
    makes it lossless rather than merely cheap — and keeps every ``excerpt``
    mentioned by ``note`` / ``next_action_hint`` on the paths that mention it.
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

    No-op unless the flag is on and confidence is high. Two carve-outs keep the
    evidence where it IS the answer: grounded fast paths (extracted /
    exact_symbol / symbol_body / data_shape, which carry a ``grounding`` key,
    and whose inlined body is the whole answer) and why-questions — a "because X"
    is justified by exactly the code_rationale / quotes this strips, so a lean
    why-answer loses the grounding its rationale stands on.
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

    The evidence an ambiguous-retrieval reply carries so the agent can pick ONE
    file to verify instead of skimming five. Shared by the legacy abstain path,
    the always-synthesize low/medium fold-in, and the degraded paths.

    ``file`` is resolved through ``hit_file_path``, and a hit resolving to no
    file is skipped — both for the reason ``serialize_candidates`` does it: a
    ``symbol_spotlight`` page's ``target_path`` is ``file.py::Symbol`` and a
    module page's is a group key, neither of which a consumer can open. This
    field is named "file" and gets Read.
    """
    return [
        {
            "file": hit_file_path(h),
            "why_relevant": _candidate_justification(h),
            "score": round(h.get("score", 0.0), 3),
            # Absent rather than null: a penalty applies to a minority of hits,
            # so the common row would pay characters to say nothing happened.
            **({"domain_penalty": h["_domain_penalty"]} if h.get("_domain_penalty") else {}),
            **({"excerpt": h["excerpt"]} if h.get("excerpt") else {}),
        }
        for h in hits[:_GATED_RETURN_HITS]
        if hit_file_path(h)
    ]


def _with_candidates(payload: dict, resolved_pool: list[dict]) -> dict:
    """Attach the ranked shortlist to a payload that is about to be returned.

    ``get_answer`` has several early returns that fire *after* retrieval has run:
    the qualified-miss guard, answer-by-union, the value-extraction fast path,
    the legacy abstain, and both degraded paths. Each was written as a complete
    reply in its own terms and each set ``retrieval`` to ``[]`` and returned.

    That is right about ``retrieval``, which is re-read evidence for a synthesised
    answer, and wrong about what the caller is left holding. ``resolved_pool``
    already exists at every one of these sites: the full ranked file list, built
    before the 5-hit synthesis cap, at no further cost. Discarding it means a
    caller whose question tripped one of these gates gets a narrower reply than
    one whose question did not, and gets it *because* we recognised their question
    more precisely.

    So the shortlist travels with every reply. This adds to a payload and takes
    nothing away: no gate stops firing, no predicate moves, and the special reply
    each gate exists to give is returned unchanged. It is deliberately NOT the
    fix of loosening a gate, which measured worse on recall@5.
    """
    candidates = _serialize_candidates(resolved_pool)
    if candidates:
        payload["candidates"] = candidates
    return payload


def _no_answer_payload(note: str, *, repository, t0: float) -> dict:
    """The reply for a post-retrieval gate that has nothing to answer with.

    Two gates end this way and both mean the same thing: retrieval ran, and the
    honest reply is "not this" plus what to do instead. The qualified-miss guard
    refuses to substitute a same-named symbol from another file; the no-hits
    guard has no candidates at all. ``note`` is the only part that differs, and
    it carries the redirect.

    Both callers still wrap this in :func:`_with_candidates`, which is what keeps
    the ranked shortlist travelling with every post-retrieval return.
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

    The question named a symbol with N>=2 defs no qualifier disambiguates
    (``_severity_for`` x 4). Instead of bailing to a best_guesses pointer list
    (the exact thing that triggers the agent's get_symbol/get_context drill),
    inline the UNION of the candidate bodies (char-budgeted, Read-parity) so the
    agent picks the one it wants from material already in-hand. This is the fix
    for the retrieval-MISS class: those defs are never in the fuzzy candidate
    set, so the exact-name scan is the only thing that surfaces them.

    Returns None in three cases, all of which mean the union is not the answer:
    the union is incidental (a prose question that merely mentions a many-def
    generic method like ``to_dict`` would otherwise dump every unrelated body as
    a confidence=high answer, burying what was actually asked), the question is a
    mechanism/"how" question whose real answer often lives in another file the
    union path never retrieves, or the bodies could not be read — in which case
    falling through to the normal gate path beats returning an empty union.
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
        # Bodies unreadable (no repo root / files gone) — fall through to the
        # normal retrieval/gate path rather than returning an empty union.
        return None
    names = sorted(union_groups)
    total = sum(len(v) for v in union_groups.values())
    cited = sorted({b["path"] for b in union_bodies})
    # This payload returns BEFORE synthesis, so none of the confidence gates ever
    # see it: it is served in no-LLM mode and it used to hardcode
    # confidence="high" with "no verification Read" even when a body arrived
    # truncated. "this is the complete set" is also an exclusivity claim,
    # generated by us rather than by a model, and it is true of the DEFINITION
    # SET while saying nothing about whether each body was served whole.
    #
    # The dependency test the synthesis gate uses CANNOT work here, and keying on
    # it made this gate dead code. This path is reached only for naming/lookup
    # questions about the homonym, so the question names the SERVED symbol by
    # construction while the withheld symbols are its inner members, and there is
    # no answer prose to inspect either. Here the bodies simply ARE the answer,
    # so truncation alone is the right condition.
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
        # Rates the `candidates` shortlist, not the bodies: the union answers by
        # exact name and the note offers that shortlist for "if you meant
        # something else". It is the one body-serving return that had no rating.
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

    Retrieval is ambiguous, so skip synthesis and hand back ranked excerpts +
    best_guesses for the agent to ground in. The excerpts those best_guesses
    carry were attached by the caller: a pointers-only gated payload sends the
    agent into a long Grep/Read spree that costs more than a bare agent, since it
    paid for the tool call and still had to acquire all content natively.
    Excerpts turn the miss path into "pick one candidate, verify with at most one
    Read".
    """
    best_guesses = _build_best_guesses(hits)
    # Mine source comments for rationale the wiki/decision corpus missed —
    # turns "go Read these 5 files" into a cited why.
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
    One call, zero LLM cost, and it cannot hallucinate. Not cached: extraction is
    cheap and must always reflect the current source.
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

    # Ambiguous-retrieval evidence (always-synthesize). The questions that used
    # to abstain (no dominant page) now carry synthesized PROSE — but the
    # retrieval was genuinely ambiguous, so ship the same evidence the old
    # abstain path did: best_guesses (per-file justification + excerpts) and
    # mined code_rationale, plus an honest caveat. This is the "answered, but
    # verify against these candidates" reply that replaced the empty pointer
    # list. Guarded so it never touches the dominant / high-confidence paths.
    if not dominant:
        payload.setdefault("best_guesses", _build_best_guesses(hits))
        if "code_rationale" not in payload:
            _cr = await _gather_code_rationale(ctx, hits, fallback_targets, question)
            _cr = _drop_already_surfaced(_cr, symbol_bodies, quotes)
            if _cr:
                payload["code_rationale"] = _cr
        if grade.high_reason == "symbol_body":
            # Held at high over an ambiguous ranking because the body of the
            # symbol the question named is inlined below. Telling the agent to
            # verify against best_guesses would send it to the ranked pages the
            # confidence deliberately does not rest on, so the caveat scopes the
            # doubt to the page choice and leaves the served body alone.
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

    Keep the retrieval payload lean but non-empty. The consumer has been told to
    read the source, but the ranked hits are exactly what tells it WHICH source —
    and a flow endpoint or a surfaced subsystem page that only lives in this block
    would otherwise vanish from the response entirely, since it is not in
    citations, which are drawn from the prose. The lean form (no per-hit
    key_symbols dump) keeps the prompt-cache cost the empty payload was
    protecting.
    """
    payload = {
        "answer": answer_text,
        "citations": citations,
        "confidence": confidence,
        "retrieval_quality": retrieval_quality,
        "fallback_targets": fallback_targets[:5],
        # The hedge is the priciest reply we send and the least likely to be
        # right, so it gets the graded low branch's excerpt budget rather than
        # one of its own. Safe to cut at build time, unlike most payload cuts:
        # `_cache_bypass_reason` refuses every hedged row, so a hedged reply is
        # always freshly built and no stored row can keep the wider shape.
        "retrieval": _serialize_hits(
            hits, limit=5, lean_symbols=True, excerpt_rows=_GATED_RETURN_HITS
        ),
        "note": (
            "Synthesis hedged: the LLM could not ground the question in "
            "the indexed wiki. Read one of fallback_targets to answer."
        ),
    }
    # Even on a hedge, hand over any question-named symbol bodies we resolved —
    # the agent can read the body directly instead of the fallback_targets file,
    # which is the whole point of anchoring.
    if symbol_bodies:
        payload["symbol_bodies"] = symbol_bodies
        if served_named_body:
            # The exact symbol the question named is inlined below as live
            # source. That is the answer; the hedge is about the surrounding
            # prose, not the body. Say so, and mark the response grounded so the
            # agent cites the body instead of re-reading the file.
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
    # The hedge often means the rationale isn't in the wiki at all — it's a code
    # comment. Mine the candidate source for it before sending the agent off to
    # Read. A comment already visible in symbol_bodies must not surface twice.
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

    Each branch quotes the measurement its own tier made and no other. The ratio
    is a valid justification only under ``"ratio"``: the gap tier exists BECAUSE
    the ratio is uninformative where both scores are strong (6.0 vs 5.4 reads as
    1.11x), and fusion compresses agreement pairs to about 1.02x, so quoting it
    under either would print a near tie as the reason for confidence.

    Only the dominance tiers and ``"symbol_body"`` reach *tail*, which is what
    tells the agent it need not re-read the source. ``"grounding"`` never does:
    it establishes the prose is not fabricated, which is not a claim about
    whether the page it describes is the right one.
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

    Confidence-conditional retrieval block: the block exists so the agent can
    ground when the answer alone isn't trustworthy. At high confidence the
    citations + answer suffice — carrying five enriched hits through the
    conversation cache buys nothing. At medium the agent verifies the top
    candidates: two truncated hits, no symbol enrichment for graph-expansion
    neighbors. Low keeps a grounding block, but lean: the top hits with snippets,
    symbols pipeable but stripped of docstrings/excerpts, since the full per-hit
    key_symbols dump was the largest block by volume and went mostly unused on a
    low-confidence answer.
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
        # Same value the hedged path already emits for the same situation: what
        # this answer rests on is the served body, not the ranking. It also has
        # to be set for `_apply_lean_high` to see it — that strips `symbol_bodies`
        # from a high-confidence payload unless `grounding` marks it as the
        # evidence, and the note below points the agent straight at the block it
        # would otherwise have removed.
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
        # The synthesised answer leaned on a mechanism term retrieval never
        # showed, so the real mechanism likely lives in code the wiki / decision
        # corpus never captured. Mine the candidate source for it — the same
        # lever the gated/hedged paths use — so the downgrade ships a lead, not
        # just a warning.
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
        # Note names the axis of doubt (what to be uncertain about), not the
        # check that triggered it — so a reader can tell which kind of doubt
        # this is without consulting the source code.
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
        # The ninth gate fired: the caller asked for a symbol by name and its
        # body did not fit. Without this the demotion would ship with no note at
        # all, since the high-confidence branch below is unreachable for it.
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
        # The rationale deliberately no longer cites "the answer is direct (no
        # hedging)". _SYSTEM_PROMPT instructs the model not to hedge, so scoring
        # the absence of hedging as evidence FOR confidence is circular: the
        # pipeline mandates directness and then reads its own mandate back as a
        # signal. Dominance is an independent measurement; directness is not.
        _tail = (
            "Cite this answer; do not re-read the source unless a specific "
            "detail is missing."
            if not any(b.get("truncated") for b in symbol_bodies)
            # Never tell the consumer to skip re-reading when the payload itself
            # admits it withheld part of a cited body. The withheld names are in
            # `symbol_bodies[].withheld_symbols`.
            else "Some cited bodies were truncated; see "
                 "symbol_bodies[].withheld_symbols for what was not served."
        )
        # Say which test earned the grade, and quote only the measurement that
        # test actually made. Writing one sentence for every high is how a
        # response came to quote "clearly dominates (dominance ratio 1.00x)" — a
        # tie — as its own justification, beside the caveat that no page
        # dominated. The gap and agreement tiers fire at ratios near 1.0 too, so
        # naming them without their own numbers would reproduce it exactly.
        payload["note"] = _high_confidence_note(grade, _tail)

    # Concept anchoring put a comment-justified file at the top, so synthesis may
    # now run high — but the agent asked a "why is X = <number>" question and the
    # literal rationale is the comment we already mined. Surface it so the win is
    # the answer AND the cited comment in one call (no re-read), unless a gate
    # above already attached code_rationale.
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

    A withheld entry whose name matches the body it was found in is the ENCLOSING
    symbol — served up to the cut and continuing past it — not a symbol that
    never arrived. Calling that "not served" is wrong about the payload directly
    above the note, and sends the caller to get_symbol for a body they already
    hold most of. The accurate pointer is the ``continuation`` the entry already
    carries, which fetches just the missing part and is a valid get_symbol id in
    its own right ("path.py:174-221").
    """
    _implicated = set(withheld_implicated)
    _continuing = [b for b in symbol_bodies if _is_enclosing_continuation(b, _implicated)]
    _continuing_names = {b["name"] for b in _continuing}
    _absent = [n for n in withheld_implicated if n not in _continuing_names]
    # Only advertise an id get_symbol can actually answer. The scanner that
    # produced these is a regex over source lines, so it can name something that
    # is not a symbol, and the id does not stay in a list of eight — it becomes
    # the next action the payload tells the agent to take. When none resolves the
    # names are still reported; only the dead pointer is withheld.
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
    # Qualify by path only when the same name was cut in more than one file, so
    # the common case stays readable and the ambiguous case does not ship two
    # sentences that look identical.
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
