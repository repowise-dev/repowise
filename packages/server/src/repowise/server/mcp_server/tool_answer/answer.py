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
from pathlib import Path

from sqlalchemy import delete, select

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
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    filter_dicts_by_key,
    is_excluded,
)
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_answer.confidence import (
    _answer_is_hedged,
    _is_value_question,
    _ungrounded_numbers,
)
from repowise.server.mcp_server.tool_answer.config import (
    _ANSWER_CACHE_TTL_DAYS,
    _ANSWER_SCHEMA_VERSION,
    _DOMINANCE_RATIO,
    _ENRICH_TOP_N_HITS,
    _GATED_RETURN_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _INLINE_BODY_MAX_LINES,
    _INLINE_BODY_MAX_SYMBOLS,
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
from repowise.server.mcp_server.tool_answer.retrieval import (
    serialize_hits as _serialize_hits,
)
from repowise.server.mcp_server.tool_answer.symbols import (
    _anchor_symbol_hits,
    _extract_question_identifiers,
    _extract_value_answer,
    _hydrate_symbols_for_hits,
    _read_symbol_source,
)
from repowise.server.mcp_server.tool_answer.synthesis import (
    _hash_question,
    _resolve_provider_for_answer,
)

_log = logging.getLogger("repowise.mcp.answer")


def _json_default(obj):
    """Serialize the non-JSON types retrieval hits carry (``_sources`` sets).

    Before this fallback existed, EVERY cache write failed on the sets the
    hybrid retriever attaches to hits — silently, under the old blanket
    suppress. The cache never stored a single post-hybrid-pipeline answer.
    """
    if isinstance(obj, (set, frozenset)):
        # str-key the sort: a serializer whose whole job is "never fail the
        # cache write" must not raise TypeError on a mixed-type set.
        return sorted(obj, key=str)
    return str(obj)


def _cache_entry_expired(created_at) -> bool:
    """True when an answer-cache row is older than the hard TTL."""
    if created_at is None:
        return False
    from datetime import UTC, datetime, timedelta

    ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts) > timedelta(days=_ANSWER_CACHE_TTL_DAYS)


def _gather_code_rationale(ctx, hits: list[dict], fallback_targets: list[str], question: str):
    """Mine in-code rationale comments for a low-confidence answer.

    The wiki/decision corpus failed to ground the question; the "why" may be a
    plain code comment instead (the unbiased A/B's one durable loss). Scan the
    already-relevant files — anchored/matched-symbol files lead, with a near-
    line boost on their definition, then fallback_targets fill the rest — for
    comment blocks carrying a rationale marker overlapping the question.
    Best-effort: returns [] on any failure, never raises into the tool path.
    """
    repo_root = getattr(ctx, "path", None)
    if not repo_root:
        return []
    candidates: list[str] = []
    near_lines: dict[str, int] = {}
    for h in hits or []:
        path = h.get("target_path")
        if not path:
            continue
        for s in (h.get("_anchor_symbols") or []) + [
            s for s in (h.get("symbols") or []) if s.get("_matched")
        ]:
            candidates.append(path)
            sl = s.get("start_line")
            if sl and path not in near_lines:
                near_lines[path] = sl
    candidates.extend(p for p in (fallback_targets or []) if p)
    try:
        return _mine_rationale(repo_root, candidates, question, near_lines=near_lines)
    except Exception:  # best-effort enrichment, never break the response
        return []


def _drop_already_surfaced(rationale: list[dict], *surfaced: list[dict]) -> list[dict]:
    """Drop mined rationale comments already shown elsewhere in the response.

    Track B harvests rationale comments into ``code_comment`` decision records at
    index time; Track A mines them live here. Once both ship, the same comment
    can appear twice — once as material already in the payload (a ``symbol_bodies``
    block whose body contains the comment, a quote, or a line-ranged citation /
    decision) and once as a ``code_rationale`` entry. Suppress the duplicate:
    drop any mined comment whose ``(path, line-range)`` overlaps an entry already
    surfaced. Entries without a ``(path, lines)`` pair are ignored.
    """
    occupied: list[tuple[str, int, int]] = []
    for entries in surfaced:
        for e in entries or []:
            path = e.get("path")
            lines = e.get("lines")
            if path and isinstance(lines, (list, tuple)) and len(lines) == 2:
                occupied.append((path, lines[0], lines[1]))
    if not occupied:
        return rationale
    kept: list[dict] = []
    for r in rationale:
        path = r.get("path")
        lines = r.get("lines")
        if (
            path
            and isinstance(lines, (list, tuple))
            and len(lines) == 2
            and any(
                p == path and not (lines[1] < s or lines[0] > e)
                for p, s, e in occupied
            )
        ):
            continue
        kept.append(r)
    return kept


@mcp.tool()
async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
) -> dict:
    """Synthesised answer with citations and a calibrated trust signal.

    First call for "how does X work" / "where is Y" / "why is Z" questions.
    confidence=high is content-grounded (value + citation-source gates):
    cite it directly, no verification Read needed. Low confidence returns
    best_guesses with one-line justifications instead of an empty answer.
    retrieval_quality separately rates the retrieval that fed synthesis.
    When the answer names a function/method/class, ``symbol_bodies`` carries
    its full live body — read that instead of a follow-up get_symbol.

    Args:
        question: developer question.
        scope: optional path-prefix filter (e.g. "src/pkg/").
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_answer")

    t0 = time.perf_counter()
    ctx = await _resolve_repo_context(repo)
    exclude_spec = _get_exclude_spec(ctx.path)

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
            # A row cached before exclude_patterns changed may reference a
            # now-excluded file — in its fields or its prose. Re-synthesize
            # rather than scrub the fields and leave the prose dangling.
            cached_paths = [
                *(payload.get("citations") or []),
                *(payload.get("fallback_targets") or []),
                # "path" is the serialized key; "target_path" survives in
                # rows cached before the clean retrieval view existed.
                *(h.get("path") or h.get("target_path") for h in (payload.get("retrieval") or [])),
                *(g.get("file") for g in (payload.get("best_guesses") or [])),
            ]
            excluded_cache = any(is_excluded(p, exclude_spec) for p in cached_paths)
            # Freshness: a row synthesised against a previous index may cite
            # moved code or stale values. The write path stamps the repo's
            # head commit into the persisted payload; a mismatch (or a row
            # past the hard TTL, for pre-stamping rows and gitless repos)
            # forces re-synthesis.
            current_commit = getattr(repository, "head_commit", None)
            cached_commit = payload.get("_indexed_commit")
            stale_commit = bool(
                cached_commit and current_commit and cached_commit != current_commit
            )
            expired = _cache_entry_expired(cached.created_at)
            if schema_stale:
                _log.info(
                    "Bypassing cache entry at schema v%s (current v%s)",
                    cached_version,
                    _ANSWER_SCHEMA_VERSION,
                )
            elif hedged_cache:
                _log.info("Bypassing hedged cache entry for re-synthesis")
            elif excluded_cache:
                _log.info("Bypassing cache entry referencing a now-excluded path")
            elif stale_commit:
                _log.info(
                    "Bypassing cache entry from commit %s (repo now at %s)",
                    cached_commit,
                    current_commit,
                )
            elif expired:
                _log.info("Bypassing cache entry past the %d-day TTL", _ANSWER_CACHE_TTL_DAYS)
            else:
                # Cache-internal fields never reach the consumer (response
                # keys must not start with "_" except _meta).
                payload.pop("_indexed_commit", None)
                payload.pop("_schema_version", None)
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

    # Drop excluded files right after hydration (which attaches target_path) so
    # they never enter ranking, citations, or fallback_targets.
    hits = filter_dicts_by_key(hits, "target_path", exclude_spec)

    # Identifiers the question names explicitly — drives symbol anchoring
    # (below) and question-aware symbol promotion (during hydration).
    question_ids = _extract_question_identifiers(question)

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
    # Re-filter: graph expansion can pull excluded neighbors back in (before the
    # cap, so an excluded neighbor can't occupy a top-5 slot).
    hits = filter_dicts_by_key(hits, "target_path", exclude_spec)
    # Symbol anchoring: when the question names an indexed function / method /
    # class, force its defining file into the candidate set as a dominant hit.
    # Fuzzy retrieval misses deep-path definitions even when the symbol is
    # indexed; this makes "explain X" one-shot-complete instead of degrading
    # to best_guesses on plausible-but-wrong neighbors.
    if question_ids:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                hits = await _anchor_symbol_hits(session, repo_id, question_ids, hits)
    # Always cap retrieval hits at 5 for the response payload.
    hits = hits[:5]

    # Enrich each file_page hit with its top-N WikiSymbol rows. Question-
    # aware: identifiers extracted from the question promote matching
    # symbols and attach a source-body excerpt — the difference between a
    # hedged answer on a specific-method question and a grounded one.
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
            # Mine source comments for rationale the wiki/decision corpus
            # missed — turns "go Read these 5 files" into a cited why.
            code_rationale = _gather_code_rationale(ctx, hits, fallback_targets, question)
            gated: dict = {
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
                "retrieval": _serialize_hits(hits, limit=_GATED_RETURN_HITS),
                "note": (
                    "Multiple plausible candidates — synthesis skipped to "
                    "avoid anchoring on a wrong frame. Each best_guess entry "
                    "names why that file is in the running."
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
                hint=_answer_hint("low", len(hits)),
                repository=repository,
            )
            return gated

    # Confidence is the only axis we gate on. We deliberately do NOT add a
    # second gate keyed on question shape (e.g. relational questions
    # containing connectives like "between", "and", "from"). Relational vs
    # non-relational is the wrong axis to gate on: the hard relational
    # failures already surface as low-dominance retrievals and are caught
    # by the gate above, while a shape-based gate over-fires on confidently
    # dominant relational questions and pushes cost back onto the agent's
    # own reasoning loop.

    # --- Value-extraction fast path ----------------------------------------
    # Value-shaped question + a question-matched constant in the top hits →
    # the verbatim assignment line (read live by the hydrator) IS the
    # answer. Today this class of question costs a multi-call drill-down
    # chain and synthesis sometimes invents the number; the fast path is one
    # call, zero LLM cost, and cannot hallucinate. Not cached: extraction is
    # cheap and must always reflect the current source.
    if _is_value_question(question) and question_ids:
        extraction = _extract_value_answer(hits, question_ids)
        if extraction is not None:
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
                    "Read needed."
                ),
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("high", len(hits)),
                    repository=repository,
                ),
            }

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
            "retrieval": _serialize_hits(hits),
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
            "retrieval": _serialize_hits(hits),
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

    # Line-grounded quotes: for symbols the answer actually names, attach the
    # verbatim source line(s) the hydrator read live from disk. An agent can
    # publish a cited claim backed by a quote without any verification Read —
    # the quote IS the verification.
    quotes: list[dict] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        for s in h.get("symbols") or []:
            name = s.get("name")
            # Require a name long enough that substring containment is
            # meaningful — a 1-2 char constant (``T``, ``e``) would "appear"
            # in almost any answer and attach an irrelevant quote.
            if not name or len(name) < 3 or name not in answer_text:
                continue
            src = s.get("source_excerpt") or s.get("signature") or ""
            if not src:
                continue
            quote_lines = src.splitlines()[:3]
            start = s.get("start_line") or 0
            quotes.append(
                {
                    "path": h.get("target_path"),
                    "lines": [start, start + len(quote_lines) - 1],
                    "quote": "\n".join(quote_lines),
                }
            )
            if len(quotes) >= 5:
                break
        if len(quotes) >= 5:
            break

    # Inline symbol bodies: for the multi-line definitions (function / method
    # / class) the answer actually names, surface the full body the hydrator
    # already read live for synthesis. This collapses the get_answer ->
    # get_symbol drill-down — the agent that asked "how does X work" gets X's
    # body in the same call instead of a follow-up read. Constants stay in
    # `quotes` (their body IS the one-line assignment); only definitions with
    # a real body earn a block. `source` is the live body sliced at the
    # indexed bounds; it is NOT bounds-verified, so the field stays distinct
    # from get_symbol's `verified` contract. When the indexed body is longer
    # than the hydrator's line cap, a `continuation` names the exact range
    # read for the remainder (mirrors get_symbol).
    # Gather eligible definitions across the top hits, ranked so the most
    # relevant body leads. Tier 0 = the exact symbol the question named, as
    # resolved by symbol anchoring (survives the fuzzy hydration cap that a
    # parent class name otherwise floods). Tier 1 = question-matched hydrated
    # symbols. Within a tier, a function/method outranks a class container, so
    # "explain the extract_all method of DecisionExtractor" serves extract_all,
    # not the 1,300-line class head. Then document order.
    _body_candidates: list[tuple[int, int, int, str, dict]] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        path = h.get("target_path")
        if not path:
            continue
        for s in h.get("_anchor_symbols") or []:
            name = s.get("name")
            if not name or name not in answer_text:
                continue
            kind = s.get("kind")
            kind_rank = 0 if kind in ("function", "method") else 1
            _body_candidates.append((0, kind_rank, s.get("start_line") or 0, path, s))
        for s in h.get("symbols") or []:
            name = s.get("name")
            if not name or len(name) < 3 or not s.get("_matched"):
                continue
            if name not in answer_text:
                continue
            kind = s.get("kind")
            if kind not in ("function", "method", "class", "interface"):
                continue
            kind_rank = 0 if kind in ("function", "method") else 1
            _body_candidates.append((1, kind_rank, s.get("start_line") or 0, path, s))
    _body_candidates.sort(key=lambda t: (t[0], t[1], t[2]))

    symbol_bodies: list[dict] = []
    _seen_bodies: set[tuple[str, str]] = set()
    repo_root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
    for _tier, _kind_rank, start, path, s in _body_candidates:
        if len(symbol_bodies) >= _INLINE_BODY_MAX_SYMBOLS:
            break
        name = s["name"]
        if (path, name) in _seen_bodies:
            continue
        sym_end = s.get("end_line") or 0
        # Re-read a fuller body than the synthesis excerpt: this block is for
        # the agent, so a docstring-heavy def shouldn't spend its whole window
        # on docstring and truncate the logic the question asked about. Falls
        # back to the hydrator's excerpt if the re-read fails.
        body = _read_symbol_source(
            repo_root, path, start, sym_end, max_lines=_INLINE_BODY_MAX_LINES
        ) or s.get("source_excerpt")
        if not body:
            continue
        served = body.count("\n") + 1
        end_served = start + served - 1
        sym_end = sym_end or end_served
        entry: dict = {
            "path": path,
            "name": name,
            "lines": [start, end_served],
            "source": body,
        }
        if sym_end > end_served:
            entry["truncated"] = True
            entry["continuation"] = f"{path}:{end_served + 1}-{sym_end}"
        symbol_bodies.append(entry)
        _seen_bodies.add((path, name))

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

    # Fourth gate — value grounding: on value-shaped questions (default /
    # threshold / limit / how many), every number the answer asserts must
    # appear somewhere in the material retrieval actually contained. A
    # number synthesis produced from thin air is a factual error delivered
    # with authority — the single worst calibration failure, because the
    # consumer was told not to verify. Cap at low and say why.
    ungrounded_values: list[str] = []
    if not hedged and _is_value_question(question):
        ungrounded_values = _ungrounded_numbers(answer_text, hits)
        if ungrounded_values:
            confidence = "low"

    # Fifth gate — citation-source gate: a high-confidence answer must cite
    # at least one page that contributed actual source material (hydrated
    # symbols with signatures/bodies), not just file summaries. Summary-only
    # grounding is how plausible-but-wrong syntheses get through.
    if confidence == "high":
        cited = set(citations)
        if not any(h.get("symbols") for h in hits if h.get("target_path") in cited):
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
        # Even on a hedge, hand over any question-named symbol bodies we
        # resolved — the agent can read the body directly instead of the
        # fallback_targets file, which is the whole point of anchoring.
        if symbol_bodies:
            payload["symbol_bodies"] = symbol_bodies
            payload["note"] = (
                "Synthesis hedged, but symbol_bodies carries the live body of "
                "the symbol(s) you named — read that to answer."
            )
        # The hedge often means the rationale isn't in the wiki at all — it's a
        # code comment. Mine the candidate source for it before sending the
        # agent off to Read.
        code_rationale = _gather_code_rationale(ctx, hits, fallback_targets, question)
        # A comment already visible in symbol_bodies must not surface twice.
        code_rationale = _drop_already_surfaced(code_rationale, symbol_bodies)
        if code_rationale:
            payload["code_rationale"] = code_rationale
            payload["note"] += (
                " code_rationale carries rationale comments mined from the "
                "cited source — they may already answer the question."
            )
    else:
        # Confidence-conditional retrieval block: the block exists so the
        # agent can ground when the answer alone isn't trustworthy. At high
        # confidence the citations + answer suffice — carrying five enriched
        # hits through the conversation cache buys nothing. At medium the
        # agent verifies the top candidates: two truncated hits, no symbol
        # enrichment for graph-expansion neighbors. Low keeps the full
        # block — that's when routing material earns its bytes.
        if confidence == "high":
            retrieval_view: list[dict] = []
        elif confidence == "medium":
            retrieval_view = _serialize_hits(
                hits, limit=2, summary_chars=160, symbols_for_expanded=False
            )
        else:
            retrieval_view = _serialize_hits(hits)
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
        if ungrounded_values:
            payload["note"] = (
                f"Value-grounding gate: the answer asserts {ungrounded_values} "
                "but none of these appear in any retrieved excerpt — the "
                "value(s) may be synthesised. Read "
                f"{fallback_targets[0] if fallback_targets else 'the cited file'} "
                "to confirm before citing a number."
            )
            if fallback_targets:
                payload["next_action_hint"] = (
                    f"Read {fallback_targets[0]} and verify the asserted value(s) "
                    f"{ungrounded_values} against the live source."
                )
        elif confidence == "high":
            payload["note"] = (
                "High confidence: top retrieval result clearly dominates "
                f"(dominance ratio {_ratio:.2f}x, top score {_top_score:.2f}) "
                "AND the synthesised answer is direct (no hedging). Cite this "
                "answer; do not re-read the source unless a specific detail "
                "is missing."
            )

    # Persist to cache (upsert). Best-effort: cache failures must never block
    # the response — but they must be LOGGED, not suppressed. A plain INSERT
    # under a blanket suppress violated uq_answer_cache_q on every
    # bypass-and-resynthesize round and failed silently, so hedged/stale rows
    # were never upgraded. Delete-then-insert in one transaction is the
    # dialect-agnostic upsert; the stamped _indexed_commit drives the
    # read-side freshness check.
    if answer_text:
        cache_payload = dict(payload)
        cache_payload["_schema_version"] = _ANSWER_SCHEMA_VERSION
        commit_now = getattr(repository, "head_commit", None)
        if commit_now:
            cache_payload["_indexed_commit"] = commit_now
        try:
            async with get_session(ctx.session_factory) as session:
                await session.execute(
                    delete(AnswerCache).where(
                        AnswerCache.repository_id == repo_id,
                        AnswerCache.question_hash == qhash,
                    )
                )
                row = AnswerCache(
                    repository_id=repo_id,
                    question_hash=qhash,
                    question=question.strip(),
                    payload_json=_json.dumps(cache_payload, default=_json_default),
                    provider_name=getattr(provider, "provider_name", "") or "",
                    model_name=getattr(provider, "model_name", "") or "",
                )
                session.add(row)
                await session.commit()
        except Exception as exc:
            _log.warning("get_answer cache write failed: %s", exc)

    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=_answer_hint(confidence, len(hits)),
        repository=repository,
    )
    return payload
