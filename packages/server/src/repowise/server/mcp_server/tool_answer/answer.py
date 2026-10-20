"""MCP Tool: get_answer — RAG-style synthesis over the wiki layer.

Single-call retrieval + LLM synthesis. Replaces the agent's multi-turn
search → context → read loop with one tool call that returns:

    {
      "answer":            str   — 2–5 sentence synthesised answer
      "citations":         list  — file paths backing the answer
      "confidence":        str   — "high" | "medium" | "low"
      "fallback_targets":  list  — top retrieval hits the agent should Read
                                   to verify (always present)
      "retrieval":         list  — raw top-N hits with snippets
      "symbol_bodies":     list  — full live body of each question-named
                                   definition (collapses the get_symbol
                                   follow-up); present only when the answer
                                   names a function/method/class that was
                                   hydrated
      "episodes":          list  — at most one dated fact recorded about this
                                   checkout that bears on the question, served
                                   beside the answer and never in place of it;
                                   present only when its scope intersects the
                                   answer's and confidence is below high (see
                                   ``episodes``)
      "more_definitions":  list    only on an answer-by-union (homonym) reply
                                   whose bodies overflowed the char budget:
                                   {file, name, line, symbol_id, hint} entries
                                   the agent fetches with get_symbol, not Read
    }

Answer-by-union: when the question names a symbol with N>=2 definitions no
qualifier disambiguates (``_severity_for`` x 4), the tool returns the UNION of
their bodies in ``symbol_bodies`` (grounding="exact_symbol", confidence="high")
rather than a best_guesses pointer list (the pointer list is what triggers the
agent's get_symbol/get_context drill). A qualified miss (``Parent.leaf`` matching
no def) returns not-found instead of guessing a same-named symbol elsewhere.

When no LLM provider is configured, the tool degrades to retrieval-only
mode (returns ranked hits + snippets, confidence="low") so C1 / index-only
deployments still benefit from the structured single-call shortcut.

This module is the orchestrator only. The stages live in sibling modules and
each owns one decision: ``retrieval`` and ``symbols`` find the material,
``confidence`` grades it, ``bodies`` and ``evidence`` build what is served
beside the prose, ``payload`` / ``degraded`` shape the reply, ``cache`` decides
whether a stored one may be reused, and ``config`` holds every knob and flag.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json as _json  # noqa: F401  — re-exported: a test patches answer._json.dumps
import logging
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

from repowise.core.persistence.database import get_session
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._answer_context import (
    _MAX_CHARS_PER_HIT_EXCERPT,
)
from repowise.server.mcp_server._answer_context import (
    build_context_block as _build_context_block_v2,
)
from repowise.server.mcp_server._answer_context import (
    build_structured_prelude as _build_structured_prelude,
)
from repowise.server.mcp_server._answer_context import (
    fetch_relevant_decisions as _fetch_relevant_decisions,
)
from repowise.server.mcp_server._answer_context import (
    is_why_question as _is_why_question,
)
from repowise.server.mcp_server._answer_pipeline import (
    apply_pagerank_bias as _apply_pagerank_bias,
)
from repowise.server.mcp_server._answer_pipeline import (
    degraded_legs as _degraded_legs,
)
from repowise.server.mcp_server._answer_pipeline import (
    demote_noise_hits as _demote_noise_hits,
)
from repowise.server.mcp_server._answer_pipeline import (
    expand_via_graph as _expand_via_graph,
)
from repowise.server.mcp_server._answer_pipeline import (
    expand_via_parent_page as _expand_via_parent_page,
)
from repowise.server.mcp_server._answer_pipeline import (
    hybrid_retrieve as _hybrid_retrieve,
)
from repowise.server.mcp_server._answer_pipeline import hydrate_hits as _hydrate_hits
from repowise.server.mcp_server._answer_pipeline import (
    retrieval_legs as _retrieval_legs,
)
from repowise.server.mcp_server._answer_pipeline import (
    stamp_hybrid_rank as _stamp_hybrid_rank,
)
from repowise.server.mcp_server._entry_trace import (
    expand_via_entry_trace as _expand_via_entry_trace,
)
from repowise.server.mcp_server._flow_path import expand_via_flow_path as _expand_via_flow_path
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    filter_dicts_by_key,
)
from repowise.server.mcp_server._meta import NO_HITS_RECOVERY_HINT as _NO_HITS_RECOVERY_HINT
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._neighbor_rerank import (
    expand_via_neighbor_rerank as _expand_via_neighbor_rerank,
)
from repowise.server.mcp_server.tool_answer.bodies import (
    _build_symbol_bodies,
    _gather_body_candidates,
    build_quotes,
)
from repowise.server.mcp_server.tool_answer.cache import (
    _serve_cached_answer,
    _write_answer_cache,
)
from repowise.server.mcp_server.tool_answer.callers import (
    NO_EVIDENCE,
    attach_graph_callers,
    caller_evidence,
    caller_lines,
    is_caller_question,
    is_impact_question,
)
from repowise.server.mcp_server.tool_answer.citations import answer_files
from repowise.server.mcp_server.tool_answer.confidence import (
    _agreement_dominant,
    _grade_answer,
    _is_value_question,
    _retrieval_quality,
    dominance_reason,
    uncovered_terms,
)
from repowise.server.mcp_server.tool_answer.config import (  # noqa: F401
    _GATED_EXCERPT_CHARS,
    _LEAN_HIGH_DROP_KEYS,  # re-exported for tests that assert the key set
    _PAGE_EXCERPT_HITS,
    _SYSTEM_PROMPT,
    _USER_TEMPLATE,
    _agreement_confidence_enabled,
    _always_synthesize,
    _cache_disabled,
    _symbol_agreement_enabled,
)
from repowise.server.mcp_server.tool_answer.data_shape import (
    _is_data_shape_question,
    build_data_shape_payload,
    mine_data_shape,
)
from repowise.server.mcp_server.tool_answer.degraded import _degraded_payload
from repowise.server.mcp_server.tool_answer.episodes import (
    attach_episode as _attach_episode,
)
from repowise.server.mcp_server.tool_answer.evidence import (
    _first_resolvable_id,  # noqa: F401  — re-exported: imported from here by tests
    _is_readable_path,
    _repo_root,
)
from repowise.server.mcp_server.tool_answer.neighbors import (
    attach_graph_neighbors,
    lead_file,
    neighbor_evidence,
)
from repowise.server.mcp_server.tool_answer.payload import (
    _apply_lean_high,  # noqa: F401  — backward-compatible helper re-export
    _build_best_guesses,  # noqa: F401  — re-exported: imported from here by tests
    _drop_duplicated_guess_excerpts,  # noqa: F401  — re-exported for the same reason
    _no_answer_payload,
    _trim_served_payload,  # noqa: F401  — backward-compatible helper re-export
    _union_answer_payload,
    _with_candidates,
    build_abstain_payload,
    build_synthesized_payload,
    build_value_payload,
)
from repowise.server.mcp_server.tool_answer.projection import projected_answer
from repowise.server.mcp_server.tool_answer.retrieval import (
    _apply_domain_penalty,
    _attach_page_excerpts,
    _intersection_boost,
    _rerank_by_coverage,
)
from repowise.server.mcp_server.tool_answer.symbols import (
    _anchor_symbol_hits,
    _concept_anchor_hits,
    _extract_question_identifiers,
    _extract_value_answer,
    _hydrate_candidate_defines,
    _hydrate_symbols_for_hits,
    drop_ruled_out_files,
    lead_with_files,
    place_named_files,
)
from repowise.server.mcp_server.tool_answer.synthesis import (
    _hash_answer_identity,
    _hash_question,  # backward-compatible re-export for cache migrations/tests
    _normalize_scope,
    _resolve_provider_for_answer,
    _resolve_reasoning_for_answer,
    synthesize,
)
from repowise.server.mcp_server.tool_search_symbols import issue_files

_log = logging.getLogger("repowise.mcp.answer")

# The excerpt fetch and the prompt formatter each cap page content in different
# modules; checked at import here, the only module that sees both caps.
if _MAX_CHARS_PER_HIT_EXCERPT < _GATED_EXCERPT_CHARS:
    raise RuntimeError(
        "get_answer would truncate the page content it fetches: the prompt "
        f"formatter caps an excerpt at {_MAX_CHARS_PER_HIT_EXCERPT} chars "
        f"while the fetch asks for {_GATED_EXCERPT_CHARS}. Raise "
        "_MAX_CHARS_PER_HIT_EXCERPT or lower _GATED_EXCERPT_CHARS."
    )


async def _attach_neighbors(
    payload: dict, *, wanted: bool, ctx, repo_id: str, hits: list[dict], exclude_spec
) -> dict:
    """``graph_neighbors`` for the file *payload* leads with; a failed lookup leaves it as is."""
    if not wanted:
        return payload
    lead = lead_file(payload)
    try:
        async with get_session(ctx.session_factory) as session:
            rows = await neighbor_evidence(
                session, repo_id, lead, hits, exclude_spec, repo_root=_repo_root(ctx)
            )
    except Exception:
        _log.warning("get_answer: graph neighbor lookup failed", exc_info=True)
        return payload
    return attach_graph_neighbors(payload, rows, lead)


class _Retrieved(NamedTuple):
    """What retrieval resolved, before any decision about how to answer.

    ``hits`` is capped for the response payload; ``resolved_pool`` is the
    same ranking before the cap, which is what ``candidates`` is built from.
    """

    hits: list[dict]
    resolved_pool: list[dict]
    question_ids: set[str]
    homonyms: dict
    flow_paths: list[list[str]]


async def _run_retrieval_pipeline(
    question: str, ctx, *, scope: str | None, exclude_spec, repo_id
) -> _Retrieved:
    """Run every retrieval, ranking and enrichment stage, in order.

    Everything here is about FINDING the material; nothing decides the answer.
    Each expansion stage is best-effort and suppressed on its own, so one slow
    or broken backend costs its contribution and never the call.
    """
    hits = await _hybrid_retrieve(question, ctx)
    hits = await _hydrate_hits(hits, ctx, scope=scope)

    # Drop excluded files right after hydration (which attaches target_path) so
    # they never enter ranking, citations, or fallback_targets.
    hits = filter_dicts_by_key(hits, "target_path", exclude_spec)
    _stamp_hybrid_rank(hits)

    question_ids = _extract_question_identifiers(question)

    # Coverage re-rank before any graph-aware bias so conjunctive matches survive.
    hits = _rerank_by_coverage(hits, question)
    _apply_domain_penalty(hits, question)
    with contextlib.suppress(Exception):
        await _intersection_boost(question, hits, ctx)
    # Damped and normalised within the candidate set: a tie-breaker only.
    with contextlib.suppress(Exception):
        await _apply_pagerank_bias(hits, ctx)
    # 1-hop walk rescues "right module, wrong file" near-misses.
    with contextlib.suppress(Exception):
        hits = await _expand_via_graph(hits, ctx, repo_id)
    # Graph expansion can pull excluded neighbors back in; re-filter before the cap.
    hits = filter_dicts_by_key(hits, "target_path", exclude_spec)
    # Symbol anchoring: force a question-named symbol's defining file in as a
    # dominant hit, since fuzzy retrieval misses deep-path definitions.
    homonyms: dict = {"union": {}, "qualified_miss": []}
    if question_ids:
        with contextlib.suppress(Exception):
            _anchor_root = _repo_root(ctx)
            async with get_session(ctx.session_factory) as session:
                hits, homonyms = await _anchor_symbol_hits(
                    session,
                    repo_id,
                    question_ids,
                    hits,
                    repo_root=_anchor_root,
                    session_factory=ctx.session_factory,
                    question=question,
                )
    # Concept anchoring: a why/value question pinning a number to a behaviour
    # anchors the file whose source comment justifies that number.
    if _is_why_question(question) or _is_value_question(question):
        with contextlib.suppress(Exception):
            hits = await _concept_anchor_hits(getattr(ctx, "path", None), question, hits)
    # Flow-path expansion: when the question anchors 2+ endpoints, inject the
    # files on the path between them, reaching a far endpoint 1-hop expansion
    # cannot. This and the stages below run before the cap so an injected file
    # can take a top-5 slot.
    flow_paths: list[list[str]] = []
    # A sequence question ("what happens when ...") is answered by the call
    # order of the entry it names; when that resolves, it is the flow served.
    with contextlib.suppress(Exception):
        async with get_session(ctx.session_factory) as session:
            hits, flow_paths = await _expand_via_entry_trace(
                session, repo_id, hits, question, exclude_spec
            )
    traced = bool(flow_paths)
    if not traced:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                hits, flow_paths = await _expand_via_flow_path(
                    session, repo_id, hits, question, question_ids
                )
    # Neighborhood re-rank: for flow questions whose target file is never named,
    # re-rank the 1-2 hop neighborhood of the top hits. No-op on other shapes.
    with contextlib.suppress(Exception):
        async with get_session(ctx.session_factory) as session:
            hits = await _expand_via_neighbor_rerank(session, repo_id, hits, question, ctx)
    # Parent-concept surfacing: a subsystem-shaped question leads with the
    # rollup page for the subsystem. No-op on other shapes, and skipped once a
    # call trace answers the question: its entry file must keep the lead.
    if not traced:
        with contextlib.suppress(Exception):
            hits = await _expand_via_parent_page(hits, question, ctx)
    # Demote noise (decisions on non-why, test pages on non-test questions)
    # below real pages. Non-dropping; after anchoring, which never injects noise.
    hits = _demote_noise_hits(hits, question, is_why=_is_why_question(question))
    # Files on a pasted stack trace lead; files defining a named identifier
    # join just below the top three. No-op when the question has neither.
    try:
        issue = await issue_files(ctx, question)
    except Exception:
        _log.warning("get_answer: issue file lookup failed", exc_info=True)
    else:
        def _in_scope(paths: list[str]) -> list[str]:
            return [p for p in paths if not scope or p.startswith(scope)]

        if traced := _in_scope(issue.traced):
            hits = lead_with_files(hits, traced)
        if named := _in_scope(issue.named):
            hits = place_named_files(hits, named)
    hits = drop_ruled_out_files(hits, question)
    # The pre-cap ranking feeds ``candidates``: files below the synthesis cut
    # are still the best answer to "where do I look next".
    resolved_pool = list(hits)
    hits = hits[:5]

    # Question-aware symbol hydration: matching symbols get a body excerpt.
    if hits:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                await _hydrate_symbols_for_hits(
                    session, repo_id, hits, ctx, question_ids=question_ids, question=question
                )
                # Against `resolved_pool`: the point is the files below the cap.
                # A missing `_defines` costs a `defines` key, never an answer.
                await _hydrate_candidate_defines(
                    session, repo_id, resolved_pool, question_ids=question_ids
                )
    return _Retrieved(hits, resolved_pool, question_ids, homonyms, flow_paths)


@dataclass(frozen=True)
class _Ask:
    """One ``get_answer`` call once its repository is resolved."""

    question: str
    ctx: Any
    repository: Any
    repo_id: str
    exclude_spec: Any
    t0: float


def _empty_question_payload(t0: float) -> dict:
    return {
        "answer": "",
        "citations": [],
        "confidence": "low",
        "fallback_targets": [],
        "retrieval": [],
        "error": "question is required",
        "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
    }


async def _data_shape_answer(question: str, ctx, repository, t0: float) -> dict | None:
    """The data-shape reply, mined from live source, or ``None`` when not grounded.

    Runs before cache and retrieval: retrieval scatters across every consumer
    of the blob.
    """
    ds_ids = _extract_question_identifiers(question)
    if not _is_data_shape_question(question, ds_ids):
        return None
    grounded = await asyncio.to_thread(mine_data_shape, getattr(ctx, "path", None), ds_ids)
    if grounded is None:
        return None
    return build_data_shape_payload(grounded, t0, repository)


async def _graph_callers(ask: _Ask, retrieved: _Retrieved):
    """The graph's callers as evidence for a caller question; no query otherwise."""
    if not is_caller_question(ask.question, retrieved.question_ids):
        return NO_EVIDENCE
    try:
        async with get_session(ask.ctx.session_factory) as session:
            return await caller_evidence(
                session,
                ask.repo_id,
                ask.question,
                retrieved.question_ids,
                retrieved.hits,
                ask.exclude_spec,
                repo_root=_repo_root(ask.ctx),
            )
    except Exception:
        _log.warning("get_answer: graph caller lookup failed", exc_info=True)
        return NO_EVIDENCE


def _agreement(hits: list[dict]) -> bool:
    """Symbol-agreement dominance, read from the leg status rather than the capped hits."""
    if not _agreement_confidence_enabled():
        return False
    return _agreement_dominant(
        hits,
        vector_leg_keyless=(
            _symbol_agreement_enabled() and _retrieval_legs().get("vector") == "keyless"
        ),
    )


async def _pre_synthesis_reply(
    ask: _Ask, retrieved: _Retrieved, agreement_dominant: bool, graph_callers, neighbors
) -> dict | None:
    """The qualified-miss, answer-by-union or no-hits reply, before any page is read."""
    hits = retrieved.hits
    missed = retrieved.homonyms.get("qualified_miss")
    if missed:
        # ``Parent.leaf`` found only under OTHER parents: not-found, never a
        # confidently-wrong answer from a same-named symbol elsewhere.
        return _no_answer_payload(
            f"No indexed definition matches the qualified name(s) {missed}. "
            "The base name is defined elsewhere, but not under the "
            "class/module you named, so this is not returning a same-named "
            "symbol from another file, to avoid a confidently-wrong answer. "
            'Re-check the qualifier, or call search_codebase mode="symbol" '
            "on the base name to see every definition. The files retrieval "
            "ranked for this question are in candidate_files.",
            repository=ask.repository,
            t0=ask.t0,
        )
    union_payload = _union_answer_payload(
        ask.question,
        retrieved.question_ids,
        retrieved.homonyms,
        ask.ctx,
        ask.repository,
        ask.t0,
        _retrieval_quality(hits, agreement_dominant, ask.question),
        hits,
    )
    if union_payload is not None:
        return await neighbors(attach_graph_callers(union_payload, graph_callers))
    if not hits:
        return _no_answer_payload(
            "No wiki hits for this question. Rephrase around the code "
            "concept. " + _NO_HITS_RECOVERY_HINT,
            repository=ask.repository,
            t0=ask.t0,
        )
    return None


def _fallback_targets(hits: list[dict]) -> list[str]:
    """Readable paths of the top hits, for the agent to verify."""
    return [
        h["target_path"]
        for h in hits
        if h.get("target_path") and _is_readable_path(h["target_path"])
    ]


async def _attach_excerpts(hits: list[dict], ctx) -> None:
    """Page content for the top hits, before anything branches on retrieval quality.

    Gating it on dominance starves the confident answers of prose; the cost is
    one indexed SELECT over at most five rows.
    """
    missing = await _attach_page_excerpts(hits, ctx)
    if missing:
        _log.warning(
            "get_answer: %d of %d top hits have no page content; those hits "
            "reach synthesis as a one-line summary only",
            missing,
            min(len(hits), _PAGE_EXCERPT_HITS),
        )


async def _gated_reply(
    ask: _Ask,
    retrieved: _Retrieved,
    dominance,
    fallback_targets: list[str],
    graph_callers,
    neighbors,
) -> dict | None:
    """The abstain reply without dominance, or the verbatim value for a value question.

    Under always-synthesize, dominance only feeds the grade and the ratings. A
    value-shaped question with a question-matched constant in the top hits is
    answered by that assignment line, read live by the hydrator.
    """
    hits = retrieved.hits
    if not _always_synthesize() and dominance is None:
        abstain = await build_abstain_payload(
            question=ask.question,
            ctx=ask.ctx,
            hits=hits,
            fallback_targets=fallback_targets,
            repository=ask.repository,
            t0=ask.t0,
        )
        return await neighbors(attach_graph_callers(abstain, graph_callers))
    # No second gate on question shape: relational failures already show as low
    # dominance, and a shape gate over-fires on dominant relational questions.
    if _is_value_question(ask.question) and retrieved.question_ids:
        extraction = _extract_value_answer(hits, retrieved.question_ids, ask.question)
        if extraction is not None:
            return build_value_payload(
                extraction=extraction,
                hits=hits,
                fallback_targets=fallback_targets,
                repository=ask.repository,
                t0=ask.t0,
            )
    return None


async def _user_prompt(ask: _Ask, hits: list[dict], graph_callers) -> str:
    """The synthesis prompt; decisions (why questions) and prelude drop out when empty."""
    top_paths = [h["target_path"] for h in hits if h.get("target_path")]
    decisions: list[dict] = []
    if _is_why_question(ask.question) and top_paths:
        with contextlib.suppress(Exception):
            decisions = await _fetch_relevant_decisions(ask.ctx, ask.repo_id, top_paths)
    prelude = ""
    with contextlib.suppress(Exception):
        prelude = await _build_structured_prelude(hits, decisions, ask.ctx, ask.repo_id)
    if graph_callers.rows:
        graph_block = "Callers from the call graph:\n" + "\n".join(
            f"- {line}" for line in caller_lines(graph_callers.rows)
        )
        prelude = f"{prelude}\n\n{graph_block}" if prelude else graph_block
    return _USER_TEMPLATE.format(
        question=ask.question.strip(),
        n=len(hits),
        context=_build_context_block_v2(hits, prelude=prelude, decisions=decisions),
    )


async def _graded_payload(
    ask: _Ask,
    retrieved: _Retrieved,
    answer_text: str,
    fallback_targets: list[str],
    agreement_dominant: bool,
    dominance,
) -> tuple[dict, str, list[str]]:
    """Grade the synthesized answer and build its payload: ``(payload, confidence, citations)``."""
    hits = retrieved.hits
    cited = answer_files(
        answer_text, hits, retrieved.resolved_pool, fallback_targets, _repo_root(ask.ctx)
    )
    citations = cited.citations
    quotes = [q for q in build_quotes(hits, answer_text) if q["path"] not in cited.ruled_out]
    # ``served_named_body``: the question-named symbol's live body is inlined and
    # carries what was asked, so the response is content-grounded even when
    # synthesis hedges.
    symbol_bodies, served_named_body = _build_symbol_bodies(
        _gather_body_candidates(hits, answer_text),
        _repo_root(ask.ctx),
        question=ask.question,
        hits=hits,
    )
    # One coverage measurement for the grade and the retrieval rating.
    uncovered = uncovered_terms(ask.question, hits)
    grade = _grade_answer(
        question=ask.question,
        question_ids=retrieved.question_ids,
        answer_text=answer_text,
        hits=hits,
        citations=citations,
        symbol_bodies=symbol_bodies,
        served_named_body=served_named_body,
        dominance=dominance,
        uncovered=uncovered,
        lead_off_top=cited.drops_top_hit(hits, answer_text),
    )
    payload = await build_synthesized_payload(
        question=ask.question,
        answer_text=answer_text,
        citations=citations,
        grade=grade,
        retrieval_quality=_retrieval_quality(hits, agreement_dominant, ask.question, uncovered),
        hits=hits,
        fallback_targets=fallback_targets,
        symbol_bodies=symbol_bodies,
        served_named_body=served_named_body,
        quotes=quotes,
        dominant=dominance is not None,
        ctx=ask.ctx,
        repository=ask.repository,
        exclude_spec=ask.exclude_spec,
    )
    return payload, grade.confidence, citations


async def _cache_and_stamp(
    payload: dict,
    ask: _Ask,
    *,
    write_cache: bool,
    qhash: str,
    provider,
    confidence: str,
    targets: list[str],
) -> None:
    """Write the cache row, then stamp ``_meta`` and the episode on the live reply.

    Only the trust-relevant retrieval state is cached; the cache read rebuilds
    timing and freshness. The episode is attached after the write, so it never
    reaches the cache row.
    """
    degraded = _degraded_legs(_retrieval_legs())
    if degraded:
        payload["_retrieval_degraded"] = degraded
    if write_cache:
        await _write_answer_cache(
            payload,
            ctx=ask.ctx,
            question=ask.question,
            repository=ask.repository,
            repo_id=ask.repo_id,
            qhash=qhash,
            legacy_qhash=_hash_question(ask.question),
            provider=provider,
        )
    payload.pop("_retrieval_degraded", None)
    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - ask.t0) * 1000,
        hint=_answer_hint(confidence),
        repository=ask.repository,
        targets=targets,
    )
    # Legs are best-effort, so a lexical-only answer would otherwise look whole.
    if degraded:
        payload["_meta"]["retrieval_degraded"] = degraded
    await _attach_episode(
        payload,
        question=ask.question,
        repo_path=getattr(ask.ctx, "path", None),
        repo_name=getattr(ask.repository, "name", None),
    )


async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
    include: list[str] | None = None,
) -> dict:
    """Synthesised answer with citations and a calibrated trust signal.

    The single entry point for questions: "how does X work" / "where is Y" /
    "why is Z". It runs the full hybrid retrieval internally (no prior
    search_codebase call needed) and answers in one round-trip.
    confidence=high is content-grounded (value + citation-source + frame
    gates): cite it directly, no verification Read needed. A "why" answer
    whose named mechanism is absent from the retrieved source is downgraded
    to medium (the rationale may be conflated). Low confidence returns
    best_guesses with one-line justifications instead of an empty answer.
    confidence is the one trust signal: it also asks whether the top hit
    covers what was asked. retrieval_quality, the rating of the retrieval
    alone, is served only under ``include=["diagnostics"]``.
    When the answer names a function/method/class, ``symbol_bodies`` carries
    its full live body — read that instead of a follow-up get_symbol.
    ``episodes``, when present, is a dated fact recorded about this checkout
    that bears on the question — evidence beside the answer, not a correction
    of it. Weigh it against the answer; ``still_true`` says how current it is.

    Responses fit 24,000 serialized chars. Pass ``include=["evidence"]`` for
    the deduplicated expanded evidence projection (32,000 chars). Reductions
    carry counts and a recovery: repeat the call with that include.

    Args:
        question: developer question.
        scope: optional path-prefix filter (e.g. "src/pkg/").
        repo: usually omitted.
        include: optional ``["evidence"]`` expanded projection;
            ``["diagnostics"]`` adds ``retrieval_quality``.
    """
    if repo == "all":
        return _unsupported_repo_all("get_answer")

    t0 = time.perf_counter()
    ctx = await _resolve_repo_context(repo)
    exclude_spec = _get_exclude_spec(ctx.path)
    if not question or not question.strip():
        return _empty_question_payload(t0)

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id
    ask = _Ask(question, ctx, repository, repo_id, exclude_spec, t0)

    shaped = await _data_shape_answer(question, ctx, repository, t0)
    if shaped is not None:
        return shaped

    # Normalize once, then use the same value for retrieval and cache identity.
    # The versioned identity deliberately misses legacy question-only rows.
    normalized_scope = _normalize_scope(scope)
    qhash = _hash_answer_identity(question, normalized_scope)
    cache_disabled = _cache_disabled()
    if not cache_disabled:
        served = await _serve_cached_answer(
            ctx=ctx,
            question=question,
            repository=repository,
            repo_id=ask.repo_id,
            qhash=qhash,
            exclude_spec=exclude_spec,
            t0=t0,
        )
        if served is not None:
            return served

    retrieved = await _run_retrieval_pipeline(
        question, ctx, scope=normalized_scope, exclude_spec=exclude_spec, repo_id=ask.repo_id
    )
    hits = retrieved.hits
    resolved_pool = retrieved.resolved_pool
    graph_callers = await _graph_callers(ask, retrieved)
    # Impact questions the caller lookup left unanswered get the users of the
    # file the answer leads with, once that file is known.
    _neighbors = functools.partial(
        _attach_neighbors,
        wanted=not graph_callers.rows and is_impact_question(question, retrieved.question_ids),
        ctx=ctx,
        repo_id=ask.repo_id,
        hits=hits,
        exclude_spec=exclude_spec,
    )
    # Above the early returns: they rate retrieval too.
    agreement_dominant = _agreement(hits)

    # Every return from here on goes through ``_with_candidates`` (or the
    # degraded path, which does) so the ranked pool is never dropped.
    early = await _pre_synthesis_reply(
        ask, retrieved, agreement_dominant, graph_callers, _neighbors
    )
    if early is not None:
        return _with_candidates(early, resolved_pool)

    fallback_targets = _fallback_targets(hits)
    await _attach_excerpts(hits, ctx)
    # The grade takes the dominance TIER so its note quotes only the measurement made.
    dominance = dominance_reason(hits, agreement_dominant=agreement_dominant)
    gated = await _gated_reply(
        ask, retrieved, dominance, fallback_targets, graph_callers, _neighbors
    )
    if gated is not None:
        return _with_candidates(gated, resolved_pool)

    # Both ways synthesis can go missing share one payload; they differ only in
    # the reason and the note.
    async def _degrade(reason: str, note: str) -> dict:
        payload = await _degraded_payload(
            reason=reason,
            note=note,
            question=question,
            hits=hits,
            fallback_targets=fallback_targets,
            repository=repository,
            t0=t0,
            ctx=ctx,
            question_ids=retrieved.question_ids,
            exclude_spec=exclude_spec,
            agreement_dominant=agreement_dominant,
            resolved_pool=resolved_pool,
            graph_callers=graph_callers,
        )
        payload = await _neighbors(payload)
        degraded_legs = _degraded_legs(_retrieval_legs())
        if degraded_legs:
            payload.setdefault("_meta", {})["retrieval_degraded"] = degraded_legs
        return _with_candidates(payload, resolved_pool)

    provider = _resolve_provider_for_answer(getattr(ctx, "path", None))
    if provider is None:
        # Retrieval-only mode, logged loudly so nobody has to diff payload
        # shapes to notice synthesis is unplugged.
        _log.warning(
            "get_answer running WITHOUT synthesis: no LLM provider resolvable "
            "(set REPOWISE_PROVIDER + its API key, or any supported API key)."
        )
        return await _degrade(
            "no-llm-provider",
            "Synthesis is unavailable; local retrieval and source evidence remain usable.",
        )

    # Budgets its own timeout per provider: a remote API needs seconds, a CLI
    # subprocess or local model needs minutes.
    answer_text, failure_note = await synthesize(
        provider,
        _SYSTEM_PROMPT,
        await _user_prompt(ask, hits, graph_callers),
        reasoning=_resolve_reasoning_for_answer(getattr(ctx, "path", None)),
        session_factory=getattr(ctx, "session_factory", None),
        repo_id=ask.repo_id,
    )
    if failure_note is not None:
        return await _degrade("synthesis-failed", failure_note)

    payload, confidence, citations = await _graded_payload(
        ask, retrieved, answer_text, fallback_targets, agreement_dominant, dominance
    )
    attach_graph_callers(payload, graph_callers)
    await _neighbors(payload)
    if retrieved.flow_paths:
        payload["flow_path"] = [" -> ".join(p) for p in retrieved.flow_paths[:2]]
    # Navigation, not evidence: it survives the shrinking of ``retrieval``.
    _with_candidates(payload, resolved_pool)
    await _cache_and_stamp(
        payload,
        ask,
        write_cache=bool(answer_text) and not cache_disabled,
        qhash=qhash,
        provider=provider,
        confidence=confidence,
        targets=[*citations, *fallback_targets],
    )
    return payload


# Both definitions are literal ``get_answer``: source-shape tests audit the
# orchestrator's early returns, and CLI adapters check ``cr_code.co_qualname``.
_get_answer_raw = get_answer
_projected_get_answer = projected_answer(_get_answer_raw)


@mcp.tool(
    surface_order=10,
    artifact_type="answer",
    presentation="answer",
    recipes=(
        ToolRecipe(
            "answer_question",
            'get_answer(question="how does X work?")',
            ("get_answer",),
        ),
    ),
)
async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
    include: list[str] | None = None,
) -> dict:
    """Answer a how, where, or why question in one evidence-grounded call.

    High confidence is content-grounded and may be used directly. Medium
    confidence keeps the smallest verification evidence; low confidence leads
    with an actionable local conclusion and ranked evidence. ``candidate_files``
    ranks the files worth opening that the citations do not name. Provider keys and
    network access are optional: local source, symbols, FTS, rationale, and
    data-shape evidence remain usable when embeddings or synthesis fail.

    Responses fit 24,000 serialized characters. Pass ``include=["evidence"]``
    for the deduplicated expanded projection, capped at 32,000. Reductions carry
    totals, emitted counts, reasons, and a recovery: the same call with that include.

    Args:
        question: Developer question.
        scope: Optional repository-relative path prefix.
        repo: Usually omitted; a workspace alias when needed.
        include: Optional ``["evidence"]`` expanded projection;
            ``["diagnostics"]`` adds ``retrieval_quality``.
    """
    return await _projected_get_answer(
        question=question,
        scope=scope,
        repo=repo,
        include=include,
    )
