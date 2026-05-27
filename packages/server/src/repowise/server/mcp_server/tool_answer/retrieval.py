"""Re-ranking, domain penalty, intersection boost, and gated-excerpt helpers.

These operate on the candidate hit list after the hybrid-retrieval stages in
``_answer_pipeline``. They tune the ranking (coverage rerank, domain penalty,
intersection boost) and prepare the low-confidence return path
(gated excerpts, candidate justifications).
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from repowise.server.mcp_server.tool_answer.config import (
    _BACKEND_PATH_PREFIXES,
    _BACKEND_QUESTION_TOKENS,
    _COVERAGE_FLOOR,
    _DOMAIN_PENALTY,
    _GATED_EXCERPT_CHARS,
    _GATED_RETURN_HITS,
    _RELATIONAL_CONNECTIVES,
    _STOPWORDS,
    _UI_PATH_PREFIXES,
    _UI_QUESTION_TOKENS,
)


def _question_terms(question: str) -> list[str]:
    """Extract content terms from a question. Lowercase, alnum-tokenized,
    stopwords + length<3 dropped. Used by the term-coverage re-ranker."""
    import re

    raw = re.findall(r"[a-zA-Z0-9_]+", question.lower())
    return [t for t in raw if len(t) >= 3 and t not in _STOPWORDS]


def _split_relational(question: str) -> list[str] | None:
    """If the question is relational (contains a connective like 'and' or
    'between'), split it into two sub-queries on the FIRST matching
    connective. Returns [left, right] or None if not relational.

    Heuristic only — works on English grammar, not on code or repo terms.
    """
    q = " " + question.strip() + " "
    qlow = q.lower()
    for conn in _RELATIONAL_CONNECTIVES:
        idx = qlow.find(conn)
        if idx > 0:
            left = q[:idx].strip()
            right = q[idx + len(conn) :].strip()
            # Both sides must have at least 3 content terms to be a real
            # multi-entity question (not e.g. "what is X and how").
            if len(_question_terms(left)) >= 3 and len(_question_terms(right)) >= 3:
                return [left, right]
    return None


async def _intersection_boost(question: str, hits: list[dict], ctx: Any = None) -> None:
    """For relational questions, boost any hit that appears in both halves
    of a split-FTS retrieval. Mutates `hits` in place: adds a multiplicative
    bonus to `score` for hits that appear in both subset retrievals.

    Universal IR principle: pages at the intersection of two query halves
    are much more likely to answer relational questions than pages at the
    top of either half alone. Independent of repo or domain.
    """
    parts = _split_relational(question)
    if parts is None or ctx is None or ctx.fts is None:
        return
    sub_hit_ids: list[set] = []
    for sub_q in parts:
        try:
            sub = await asyncio.wait_for(ctx.fts.search(sub_q, limit=15), timeout=3.0)
            sub_hit_ids.append({h.page_id for h in sub})
        except Exception:
            return
    if len(sub_hit_ids) < 2:
        return
    intersection = sub_hit_ids[0] & sub_hit_ids[1]
    if not intersection:
        return
    # 2× boost for hits at the intersection — strong enough to overtake
    # a single-side top hit, not so strong that it ignores BM25 entirely.
    for h in hits:
        if h.get("page_id") in intersection:
            h["score"] = h.get("score", 0.0) * 2.0
            h["_intersection"] = True
    hits.sort(key=lambda h: h["score"], reverse=True)


async def _enrich_gated_excerpts(hits: list[dict], ctx: Any = None) -> None:
    """For the gated (low-confidence) return path, fetch real page content
    for top hits so the agent has substantive raw material instead of
    one-line summaries. Mutates `hits` in place — adds an `excerpt` field.

    Universal motivation: thin retrieval output forces consumers to fall
    back on priors instead of grounding in source. Symmetric with the
    enrichment we already do for synthesis.
    """
    if not hits:
        return
    page_ids = [h["page_id"] for h in hits[:_GATED_RETURN_HITS] if h.get("page_id")]
    if not page_ids:
        return
    try:
        async with get_session(ctx.session_factory) as session:
            res = await session.execute(
                select(Page.id, Page.content_md).where(Page.id.in_(page_ids))
            )
            content_by_id = {row[0]: (row[1] or "") for row in res.all()}
    except Exception:
        return
    for h in hits[:_GATED_RETURN_HITS]:
        body = content_by_id.get(h.get("page_id"), "")
        if body:
            h["excerpt"] = body[:_GATED_EXCERPT_CHARS]


def _detect_question_domain(question: str) -> str | None:
    """Return ``"ui"``, ``"backend"``, or ``None`` when the question is ambiguous.

    Used to break ties on retrievals where vocabulary overlaps across domains
    (e.g. "how does indexing work" could plausibly retrieve a UI status-pill
    component or the actual ingestion pipeline). The classifier is intentionally
    conservative: if both domain token sets fire, or neither does, we return
    ``None`` and apply no penalty — better to leave ranking alone than to
    miscategorise a cross-cutting question.
    """
    qlow = question.lower()
    has_ui = any(tok in qlow for tok in _UI_QUESTION_TOKENS)
    has_backend = any(tok in qlow for tok in _BACKEND_QUESTION_TOKENS)
    if has_ui and not has_backend:
        return "ui"
    if has_backend and not has_ui:
        return "backend"
    return None


def _apply_domain_penalty(hits: list[dict], question: str) -> None:
    """Multiplicatively penalise cross-domain hits in place.

    Mutates ``hits`` and re-sorts by adjusted score. No-op when the question
    domain is ambiguous (see ``_detect_question_domain``). Hits that take the
    penalty get a ``_domain_penalty`` marker so the gated-return path can
    surface the reason to the caller.
    """
    domain = _detect_question_domain(question)
    if domain is None or not hits:
        return
    if domain == "backend":
        bad_prefixes = _UI_PATH_PREFIXES
    else:
        bad_prefixes = _BACKEND_PATH_PREFIXES
    touched = False
    for h in hits:
        tp = h.get("target_path") or ""
        if tp and any(tp.startswith(p) for p in bad_prefixes):
            h["score"] = h.get("score", 0.0) * _DOMAIN_PENALTY
            h["_domain_penalty"] = f"{domain} question; cross-domain path"
            touched = True
    if touched:
        hits.sort(key=lambda h: h["score"], reverse=True)


def _candidate_justification(h: dict) -> str:
    """One-line reason this hit might answer the question.

    Used on the low-confidence return path so the agent sees something
    decision-shaped ("Read file X because it implements Y") instead of a
    flat list of paths it has to scan into. Prefers the matched-symbol name
    over the file summary because the matched symbol is what tied this hit
    to the question in the first place.
    """
    syms = h.get("symbols") or []
    matched = next((s for s in syms if s.get("_matched")), None)
    if matched:
        name = matched.get("name") or matched.get("signature") or "matched symbol"
        kind = matched.get("kind") or "symbol"
        return f"Implements {kind} {name}."
    summary = (h.get("summary") or h.get("snippet") or "").strip()
    if summary:
        # First sentence only; trailing prose is mostly cache-write cost on
        # the consumer side.
        first = summary.split(". ")[0]
        return (first[:160].rstrip() + ".") if first else ""
    title = h.get("title") or ""
    return title[:160]


def _rerank_by_coverage(hits: list[dict], question: str) -> list[dict]:
    """Re-rank FTS hits by term-coverage boost on top of BM25.

    For each hit, compute the fraction of distinct query terms present in
    (title + snippet + summary), then multiply the raw BM25 score by
    (FLOOR + (1-FLOOR)*coverage). Single-concept questions (coverage≈1.0
    across all hits) are unaffected; multi-constraint questions push hits
    that cover all the terms above hits that repeat just one term.

    This addresses a common BM25 failure mode where a hit that matches one
    constraint very strongly can outrank a hit that matches all constraints
    moderately — the latter is usually the better answer for multi-constraint
    questions.
    """
    terms = set(_question_terms(question))
    if not terms or not hits:
        return hits
    n_terms = len(terms)
    for h in hits:
        haystack = " ".join(
            [
                h.get("title", "") or "",
                h.get("snippet", "") or "",
                h.get("summary", "") or "",
            ]
        ).lower()
        # Count distinct terms present (substring match — FTS5 already handles
        # stemming upstream, so we keep this simple).
        present = sum(1 for t in terms if t in haystack)
        coverage = present / n_terms
        raw = h.get("score", 0.0)
        h["_coverage"] = coverage
        h["_raw_score"] = raw
        h["score"] = raw * (_COVERAGE_FLOOR + (1.0 - _COVERAGE_FLOOR) * coverage)
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits
