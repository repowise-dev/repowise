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
    }

When no LLM provider is configured, the tool degrades to retrieval-only
mode (returns ranked hits + snippets, confidence="low") so C1 / index-only
deployments still benefit from the structured single-call shortcut.

This module is the orchestrator only: the retrieval re-rankers, symbol
hydration, provider resolution, confidence predicate, tuning constants, and
prompts live in sibling modules (``retrieval``, ``symbols``, ``synthesis``,
``confidence``, ``config``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
import time

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import AnswerCache
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._answer_context import (
    build_context_block as _build_context_block_v2,
)
from repowise.server.mcp_server._answer_context import (
    build_structured_prelude as _build_structured_prelude,
)
from repowise.server.mcp_server._answer_context import (
    fetch_relevant_decisions as _fetch_relevant_decisions,
)
from repowise.server.mcp_server._answer_context import is_why_question as _is_why_question
from repowise.server.mcp_server._answer_pipeline import (
    apply_pagerank_bias as _apply_pagerank_bias,
)
from repowise.server.mcp_server._answer_pipeline import (
    expand_via_graph as _expand_via_graph,
)
from repowise.server.mcp_server._answer_pipeline import (
    hybrid_retrieve as _hybrid_retrieve,
)
from repowise.server.mcp_server._answer_pipeline import hydrate_hits as _hydrate_hits
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_answer.confidence import _answer_is_hedged
from repowise.server.mcp_server.tool_answer.config import (
    _ANSWER_SCHEMA_VERSION,
    _DOMINANCE_RATIO,
    _ENRICH_TOP_N_HITS,
    _GATED_RETURN_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _SYSTEM_PROMPT,
    _USER_TEMPLATE,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    _apply_domain_penalty,
    _candidate_justification,
    _enrich_gated_excerpts,
    _intersection_boost,
    _rerank_by_coverage,
)
from repowise.server.mcp_server.tool_answer.symbols import (
    _extract_question_identifiers,
    _hydrate_symbols_for_hits,
)
from repowise.server.mcp_server.tool_answer.synthesis import (
    _hash_question,
    _resolve_provider_for_answer,
)

_log = logging.getLogger("repowise.mcp.answer")


@mcp.tool()
async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
) -> dict:
    """Synthesised answer to a code question with verified citations and a calibrated trust signal.

    The only tool that pairs RAG retrieval over the wiki with an LLM-written
    answer plus a separately-reported retrieval_quality. Use it as the first
    call on "how does X work" / "where is Y" / "why is Z structured this way"
    questions — it eliminates the search → context → read loop when retrieval
    is dominant. On low confidence it returns a structured ``best_guesses``
    list (one-line justifications per candidate) instead of an empty answer,
    so the caller always has somewhere concrete to Read next.

    Returns ``{answer, citations, confidence, retrieval_quality,
    fallback_targets, best_guesses?, next_action_hint?}``. Always verify cited
    paths exist if you intend to act on them.

    Args:
        question: developer question.
        scope: optional path prefix to restrict retrieval (e.g. "src/pkg/").
        repo: repository identifier; usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_answer")

    t0 = time.perf_counter()
    ctx = await _resolve_repo_context(repo)

    if not question or not question.strip():
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": [],
            "retrieval": [],
            "error": "question is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

    # --- Cache lookup --------------------------------------------------------
    # Scope: ignore the (rare) `scope` argument in the cache key for now;
    # scoped queries are uncommon and including scope would balloon hit rate
    # variance. We hash on (repo_id, normalized_question) only.
    qhash = _hash_question(question)
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(AnswerCache).where(
                AnswerCache.repository_id == repo_id,
                AnswerCache.question_hash == qhash,
            )
        )
        cached = res.scalar_one_or_none()
    if cached is not None:
        with contextlib.suppress(Exception):
            payload = _json.loads(cached.payload_json)
            # Schema bypass: payloads from a pre-rework code path don't carry
            # the fields the current consumer expects (retrieval_quality,
            # best_guesses, calibrated confidence). Returning them masks every
            # subsequent improvement until the cache happens to expire. Bypass
            # silently so the next write upgrades the row.
            cached_version = payload.get("_schema_version", 1)
            schema_stale = cached_version < _ANSWER_SCHEMA_VERSION
            # Bypass-on-hedged: if the cached answer hedged, the retrieval +
            # symbol pipeline has since been upgraded (question-aware symbol
            # promotion, source-body excerpts). Give synthesis another shot
            # with the new context rather than pinning the bad answer.
            hedged_cache = _answer_is_hedged(payload.get("answer", ""))
            if schema_stale:
                _log.info(
                    "Bypassing cache entry at schema v%s (current v%s)",
                    cached_version,
                    _ANSWER_SCHEMA_VERSION,
                )
            elif hedged_cache:
                _log.info("Bypassing hedged cache entry for re-synthesis")
            else:
                payload["_meta"] = _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    cached=True,
                    hint=_answer_hint(
                        payload.get("confidence", "low"),
                        len(payload.get("retrieval", [])),
                    ),
                    repository=repository,
                )
                return payload

    # --- Retrieval pipeline ------------------------------------------------
    # Stages live in ``_answer_pipeline`` so each can evolve without
    # rereading the orchestrator: hybrid retrieval (FTS + vector + RRF) →
    # hydration → coverage rerank → domain penalty → intersection boost →
    # PageRank bias → 1-hop graph expansion. The orchestrator only sequences
    # them and decides when to stop (cap at 5 for the response payload).
    hits = await _hybrid_retrieve(question, ctx)
    hits = await _hydrate_hits(hits, ctx, scope=scope)

    # Term-coverage re-rank before any graph-aware bias so conjunctive
    # matches survive the merge.
    hits = _rerank_by_coverage(hits, question)
    # Domain heuristic: down-weight cross-domain hits (e.g. UI files for a
    # clearly backend question). Cheap tie-breaker, never a hard filter.
    _apply_domain_penalty(hits, question)
    # Intersection-retrieval boost for relational questions (multi-entity).
    # Pages at the intersection of two split-FTS halves get a 2× bonus.
    with contextlib.suppress(Exception):
        await _intersection_boost(question, hits, ctx)
    # PageRank bias: nudge architecturally central files above peripheral
    # ones at the same retrieval score. Damped + normalised within the
    # candidate set so it's a tie-breaker, not a wholesale reordering.
    with contextlib.suppress(Exception):
        await _apply_pagerank_bias(hits, ctx)
    # Graph expansion: 1-hop walk from the top hits to rescue near-misses
    # where retrieval landed in the right module but on the wrong file
    # (consumer instead of orchestrator). Adds up to 3 neighbors with a
    # damped score, then re-sorts.
    with contextlib.suppress(Exception):
        hits = await _expand_via_graph(hits, ctx)
    # Always cap retrieval hits at 5 for the response payload.
    hits = hits[:5]

    # Enrich each file_page hit with its top-N WikiSymbol rows. Question-
    # aware: identifiers extracted from the question promote matching
    # symbols and attach a source-body excerpt — the difference between a
    # hedged answer on a specific-method question and a grounded one.
    question_ids = _extract_question_identifiers(question)
    if hits:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                await _hydrate_symbols_for_hits(
                    session, repo_id, hits, ctx, question_ids=question_ids
                )

    fallback_targets = [h["target_path"] for h in hits if h.get("target_path")]

    if not hits:
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": [],
            "retrieval": [],
            "note": (
                "No wiki hits for this question. Fall back to "
                "search_codebase or Grep to locate candidate files."
            ),
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                hint=_answer_hint("low", 0),
                repository=repository,
            ),
        }

    # --- Confidence gate ---------------------------------------------------
    # Skip synthesis when retrieval is NOT clearly dominant. The dominance
    # ratio (top score / second score) is the sole gating criterion: above
    # the threshold the top hit is reliably the right answer; below it the
    # top-1 / top-2 ambiguity is large enough that we hand the agent ranked
    # excerpts and let it ground in source.
    #
    # Coverage (fraction of query terms present in the top hit) is also
    # available via the re-ranker and is used to bias score-based ranking,
    # but is intentionally NOT used as a hard gate here. Natural-language
    # questions rarely have all their content terms co-occurring in a single
    # page (typical coverage is 0.15–0.25), so a coverage threshold over-
    # fires on confidently-dominant retrievals and degrades the cheap path.
    if len(hits) >= 2:
        top_score = hits[0].get("score", 0.0)
        second_score = hits[1].get("score", 0.0) or 1e-9

        # Two-tier gating: at high retrieval quality (both scores
        # excellent), close ratios are expected and normal — use an
        # absolute gap instead.  At lower quality, the ratio-based
        # gate prevents synthesis on genuinely ambiguous retrievals.
        if top_score >= 3.0:
            dominant = (top_score - second_score) >= 0.5
        else:
            dominant = (top_score / second_score) >= _DOMINANCE_RATIO

        if not dominant:
            # Enrich top hits with substantive excerpts so the agent has
            # real material to ground in (not one-line summaries).
            await _enrich_gated_excerpts(hits, ctx)
            # Structured candidate set: a decision-shaped list with a
            # one-line justification per file. Beats the prior flat
            # ``fallback_targets`` list because the agent can pick ONE file
            # to Read first instead of skimming five.
            best_guesses = [
                {
                    "file": h.get("target_path"),
                    "why_relevant": _candidate_justification(h),
                    "score": round(h.get("score", 0.0), 3),
                    "domain_penalty": h.get("_domain_penalty"),
                }
                for h in hits[:_GATED_RETURN_HITS]
                if h.get("target_path")
            ]
            return {
                "answer": "",
                "citations": [],
                "confidence": "low",
                "retrieval_quality": "weak",
                "best_guesses": best_guesses,
                "next_action_hint": (
                    f"Read {best_guesses[0]['file']} first — it scored highest "
                    "but retrieval was ambiguous, so verify before answering."
                    if best_guesses
                    else "Fall back to search_codebase or Grep."
                ),
                "fallback_targets": fallback_targets,
                "retrieval": hits[:_GATED_RETURN_HITS],
                "note": (
                    "Multiple plausible candidates — synthesis skipped to "
                    "avoid anchoring on a wrong frame. Each best_guess entry "
                    "names why that file is in the running."
                ),
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("low", len(hits)),
                    repository=repository,
                ),
            }

    # Confidence is the only axis we gate on. We deliberately do NOT add a
    # second gate keyed on question shape (e.g. relational questions
    # containing connectives like "between", "and", "from"). Relational vs
    # non-relational is the wrong axis to gate on: the hard relational
    # failures already surface as low-dominance retrievals and are caught
    # by the gate above, while a shape-based gate over-fires on confidently
    # dominant relational questions and pushes cost back onto the agent's
    # own reasoning loop.

    # --- Synthesis (LLM) ---------------------------------------------------
    provider = _resolve_provider_for_answer(getattr(ctx, "path", None))
    if provider is None:
        # Retrieval-only mode (no provider). Return the hits so the agent can
        # at least skip the search_codebase step.
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": fallback_targets,
            "retrieval": hits,
            "note": (
                "No LLM provider configured (set REPOWISE_PROVIDER + API key). "
                "Returning retrieval hits only — Read the listed files to answer."
            ),
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                hint=_answer_hint("low", len(hits)),
                repository=repository,
            ),
        }

    # Decision fusion (why-shaped questions only) + structured prelude. Both
    # layers are gated on signal: no ADRs for the top hits → no decisions
    # block, no symbols / commits / decisions → no prelude. Empty layers are
    # dropped before formatting, so the prompt never carries hollow scaffolding.
    top_paths = [h["target_path"] for h in hits if h.get("target_path")]
    decisions: list[dict] = []
    if _is_why_question(question) and top_paths:
        with contextlib.suppress(Exception):
            decisions = await _fetch_relevant_decisions(ctx, repo_id, top_paths)
    prelude = ""
    with contextlib.suppress(Exception):
        prelude = await _build_structured_prelude(hits, decisions, ctx, repo_id)

    user_prompt = _USER_TEMPLATE.format(
        question=question.strip(),
        n=len(hits),
        context=_build_context_block_v2(hits, prelude=prelude, decisions=decisions),
    )

    answer_text = ""
    try:
        response = await asyncio.wait_for(
            provider.generate(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.2,
            ),
            timeout=30.0,
        )
        answer_text = (response.content or "").strip()
    except Exception as exc:
        _log.warning("get_answer LLM call failed: %s", exc)
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": fallback_targets,
            "retrieval": hits,
            "note": f"LLM synthesis failed ({type(exc).__name__}). Read the listed files to answer.",
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                hint=_answer_hint("low", len(hits)),
                repository=repository,
            ),
        }

    citations = [
        h["target_path"] for h in hits if h["target_path"] and h["target_path"] in answer_text
    ]
    if not citations:
        # Fall back to top-2 retrieval paths so the agent always has something to verify.
        citations = fallback_targets[:2]

    # Compute confidence from the dominance ratio (top hit vs second hit).
    # The dominance ratio is a more reliable separator than absolute BM25
    # thresholds, which tend to label most retrievals "high" indiscriminately.
    if len(hits) >= 2:
        _top = hits[0].get("score", 0.0)
        _second = hits[1].get("score", 0.0) or 1e-9
        _ratio = _top / _second
    else:
        _ratio = float("inf") if hits else 0.0
    _top_score = hits[0].get("score", 0.0) if hits else 0.0
    if _ratio >= _DOMINANCE_RATIO and _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        confidence = "high"
    elif _ratio >= _DOMINANCE_RATIO:
        # Dominant but weak — the right file relative to its siblings, but
        # the signal isn't strong enough to trust the synthesised answer
        # without verification. Downgrade so the consumer Reads the source.
        confidence = "medium"
    else:
        confidence = "medium"

    # Second gate: downgrade when the LLM's own answer admits insufficiency.
    # Retrieval dominance only tells us we indexed the right file; it does
    # not mean the synthesized text is usable. Shipping a hedged answer with
    # confidence="high" misleads the consumer AND drags the full retrieval
    # payload (~10k chars) through the conversation cache for no benefit.
    hedged = _answer_is_hedged(answer_text)
    if hedged:
        confidence = "low"

    # Third gate — identifier-citation gate: when the question explicitly
    # names identifiers (classes / methods / snake_case / CamelCase) and
    # NONE of the top retrieval hits contain any of those identifiers as a
    # hydrated symbol, retrieval may be pointing at plausible-but-wrong
    # files (same module family, similar vocabulary). Downgrade high→medium
    # so the consumer Reads the `fallback_targets`. Only applies when the
    # question actually names identifiers — mechanism-descriptive questions
    # (no symbol names) are unaffected.
    if confidence == "high" and question_ids:
        top_n = [h for h in hits[:_ENRICH_TOP_N_HITS] if h.get("symbols")]
        has_match = any(s.get("_matched") for h in top_n for s in (h.get("symbols") or []))
        if not has_match:
            confidence = "medium"

    # retrieval_quality is a separate signal from confidence. Where confidence
    # says "how much should you trust the synthesised text", retrieval_quality
    # says "how good was the retrieval that fed it". The agent uses confidence
    # to decide whether to re-read; retrieval_quality to decide whether to
    # call search_codebase again with a refined query.
    if _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR and _ratio >= _DOMINANCE_RATIO:
        retrieval_quality = "high"
    elif _ratio >= _DOMINANCE_RATIO:
        retrieval_quality = "partial"
    else:
        retrieval_quality = "weak"

    if hedged:
        # Hedged answers: drop the retrieval payload. The consumer has been
        # told to read the source — the symbol-docstring blob that helped
        # synthesis doesn't help them, and keeping it in the response bloats
        # every follow-up turn's prompt cache.
        payload = {
            "_schema_version": _ANSWER_SCHEMA_VERSION,
            "answer": answer_text,
            "citations": citations,
            "confidence": "low",
            "retrieval_quality": retrieval_quality,
            "fallback_targets": fallback_targets[:3],
            "retrieval": [],
            "note": (
                "Synthesis hedged: the LLM could not ground the question in "
                "the indexed wiki. Read one of fallback_targets to answer."
            ),
        }
    else:
        payload = {
            "_schema_version": _ANSWER_SCHEMA_VERSION,
            "answer": answer_text,
            "citations": citations,
            "confidence": confidence,
            "retrieval_quality": retrieval_quality,
            "fallback_targets": fallback_targets,
            "retrieval": hits,
        }
        if confidence == "high":
            payload["note"] = (
                "High confidence: top retrieval result clearly dominates "
                f"(dominance ratio {_ratio:.2f}x, top score {_top_score:.2f}) "
                "AND the synthesised answer is direct (no hedging). Cite this "
                "answer; do not re-read the source unless a specific detail "
                "is missing."
            )

    # Persist to cache. Best-effort: cache failures must NEVER block the
    # response (we already have the answer in hand).
    if answer_text:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                row = AnswerCache(
                    repository_id=repo_id,
                    question_hash=qhash,
                    question=question.strip(),
                    payload_json=_json.dumps(payload),
                    provider_name=getattr(provider, "provider_name", "") or "",
                    model_name=getattr(provider, "model_name", "") or "",
                )
                session.add(row)
                await session.commit()

    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=_answer_hint(confidence, len(hits)),
        repository=repository,
    )
    return payload
