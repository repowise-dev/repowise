"""Re-ranking, domain penalty, intersection boost, and page-excerpt helpers.

These operate on the candidate hit list after the hybrid-retrieval stages in
``_answer_pipeline``. They tune the ranking (coverage rerank, domain penalty,
intersection boost), attach real page content to the top hits, and build the
candidate justifications the low-confidence return path hands back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server._retrieval_rank import rerank_by_context_coverage
from repowise.server.mcp_server.tool_answer.config import (
    _BACKEND_PATH_PREFIXES,
    _BACKEND_QUESTION_TOKENS,
    _COVERAGE_FLOOR,
    _DEFINES_CHAR_BUDGET,
    _DOMAIN_PENALTY,
    _GATED_EXCERPT_CHARS,
    _PAGE_EXCERPT_HITS,
    _RELATIONAL_CONNECTIVES,
    _STOPWORDS,
    _UI_PATH_PREFIXES,
    _UI_QUESTION_TOKENS,
)

_log = logging.getLogger("repowise.mcp.answer")


# How many files ``candidates`` names. Rows with ``defines`` are long, so a large
# cap made this block a big share of the payload. Five, not zero: it is often the
# only block naming files beyond the top ``citations``, and without it the agent
# runs a Grep that costs more than the rows.
_CANDIDATE_LIMIT = 5


def serialize_candidates(hits: list[dict], *, limit: int = _CANDIDATE_LIMIT) -> list[dict]:
    """The files retrieval ranked, one line each, ordered best first.

    Deliberately not confidence-gated: ``retrieval`` is *evidence* and shrinks
    as the prose gets trustworthy, while this block is *navigation*, the files
    worth opening next, and a confident answer is best placed to name them.

    One entry per distinct **file** path (via ``hit_file_path``), ``{path,
    lines?}``; line bounds come only from already-hydrated symbols. Pages that
    name no file (module group keys, onboarding slots) are skipped, since they
    look like paths but cannot be opened.

    ``defines`` lists what the file declares as ``name:line`` pairs, turning a
    path to Grep into a line to read. **Line numbers are as indexed, not
    verified** against the live file: navigation, not a citation. The
    ``defines`` budget never adds, drops or reorders a path.
    """
    out: list[dict] = []
    seen: set[str] = set()
    # Spent in rank order, so the best-ranked file is the one always described.
    budget = _DEFINES_CHAR_BUDGET
    for h in hits:
        path = hit_file_path(h)
        if not path or path in seen:
            continue
        seen.add(path)
        entry: dict[str, Any] = {"path": path}
        symbols = h.get("symbols") or []
        starts = [s["start_line"] for s in symbols if s.get("start_line")]
        ends = [s["end_line"] for s in symbols if s.get("end_line")]
        if starts and ends:
            entry["lines"] = f"{min(starts)}-{max(ends)}"
        if budget > 0 and h.get("_defines"):
            defines = ", ".join(f"{name}:{line}" for name, line in h["_defines"])
            if len(defines) <= budget:
                entry["defines"] = defines
                budget -= len(defines)
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def serialize_hits(
    hits: list[dict],
    *,
    limit: int | None = None,
    summary_chars: int | None = None,
    symbols_for_expanded: bool = True,
    lean_symbols: bool = False,
    excerpt_rows: int | None = None,
) -> list[dict]:
    """Agent-facing view of retrieval hits — content only, no plumbing.

    Internal scoring fields (``_coverage``, ``_raw_score``, ...) and
    ``page_id`` are ranking debug an agent cannot use, so they are dropped.

    ``summary_chars`` truncates summaries; ``symbols_for_expanded=False`` drops
    symbols from hits that only entered via graph expansion (routing material);
    ``lean_symbols=True`` keeps symbols pipeable but drops docstrings and
    excerpts, for the low-confidence path where hits are candidates to pick.

    ``excerpt_rows`` serves the page excerpt on the first N rows only: the
    excerpt is most of a row's cost. A *field* cut, not a row cut, because
    dropping rows takes paths out and a named path costs almost nothing.
    """
    out: list[dict] = []
    for idx, h in enumerate(hits[: limit if limit is not None else len(hits)]):
        target = h.get("target_path")
        entry: dict[str, Any] = {"path": target}
        # ``file.py::Symbol`` is a page id callers pipe into get_symbol; name
        # the openable file beside it.
        if target and "::" in target:
            entry["file"] = target.split("::", 1)[0]
        if h.get("title"):
            entry["title"] = h["title"]
        summary = h.get("summary") or ""
        if summary_chars is not None and len(summary) > summary_chars:
            summary = summary[: summary_chars - 1].rstrip() + "…"
        if summary:
            entry["summary"] = summary
        serve_excerpt = excerpt_rows is None or idx < excerpt_rows
        for key in ("snippet", "excerpt"):
            if h.get(key) and (key != "excerpt" or serve_excerpt):
                entry[key] = h[key]
        if h.get("score") is not None:
            entry["score"] = round(h["score"], 3)
        expanded = "graph_expand" in (h.get("_sources") or ())
        if h.get("symbols") and (symbols_for_expanded or not expanded):
            if lean_symbols:
                keep = ("name", "kind", "signature", "start_line", "end_line")
                entry["key_symbols"] = [
                    {k: s[k] for k in keep if s.get(k) is not None} for s in h["symbols"]
                ]
            else:
                entry["key_symbols"] = [
                    {k: v for k, v in s.items() if not k.startswith("_")} for s in h["symbols"]
                ]
        out.append(entry)
    return out


def _question_terms(question: str) -> list[str]:
    """Extract shared snake/camel-aware content terms for retrieval ranking."""
    return content_terms(question)


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


async def _attach_page_excerpts(hits: list[dict], ctx: Any = None) -> int:
    """Attach each top hit's real page content as ``excerpt``. Mutates `hits`.

    Without it a consumer sees only a one-line summary beside symbol names, and
    reconstructs rationale from the names: a confident wrong answer.

    Returns the number of top hits left without page content, so a hit
    reaching synthesis with no body is visible rather than silent.
    """
    if not hits:
        return 0
    top = hits[:_PAGE_EXCERPT_HITS]
    page_ids = [h["page_id"] for h in top if h.get("page_id")]
    if not page_ids:
        _log.warning(
            "get_answer: none of the %d top hits carry a page_id, so no page "
            "content can be attached — synthesis will read summaries only",
            len(top),
        )
        return len(top)
    try:
        async with get_session(ctx.session_factory) as session:
            res = await session.execute(select(Page.id, Page.content).where(Page.id.in_(page_ids)))
            content_by_id = {row[0]: (row[1] or "") for row in res.all()}
    except Exception:
        # Never fail the answer over an excerpt fetch, but never hide it either.
        _log.warning(
            "get_answer: page-content fetch failed for %d hits; synthesis "
            "will read one-line summaries instead of page prose",
            len(page_ids),
            exc_info=True,
        )
        return len(top)
    missing = 0
    for h in top:
        body = content_by_id.get(h.get("page_id"), "")
        if body:
            h["excerpt"] = body[:_GATED_EXCERPT_CHARS]
        else:
            missing += 1
    return missing


def _detect_question_domain(question: str) -> str | None:
    """Return ``"ui"``, ``"backend"``, or ``None`` when the question is ambiguous.

    Breaks ties where vocabulary overlaps across domains. Conservative: if both
    token sets fire, or neither, no penalty applies rather than miscategorise a
    cross-cutting question.
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
    bad_prefixes = _UI_PATH_PREFIXES if domain == "backend" else _BACKEND_PATH_PREFIXES
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

    Decision-shaped ("implements Y") rather than a bare path. Prefers the
    matched symbol, which is what tied this hit to the question.
    """
    syms = h.get("symbols") or []
    matched = next((s for s in syms if s.get("_matched")), None)
    if matched:
        name = matched.get("name") or matched.get("signature") or "matched symbol"
        kind = matched.get("kind") or "symbol"
        return f"Implements {kind} {name}."
    summary = (h.get("summary") or h.get("snippet") or "").strip()
    if summary:
        # First sentence only; the rest is mostly cache-write cost.
        first = summary.split(". ")[0]
        return (first[:160].rstrip() + ".") if first else ""
    title = h.get("title") or ""
    return title[:160]


def _rerank_by_coverage(hits: list[dict], question: str) -> list[dict]:
    """Re-rank hybrid hits by intent-bearing term coverage.

    Coverage includes the path/module identity as well as title and prose.
    Terms every candidate repeats carry less weight than discriminating terms,
    so a generic lexical overlap such as ``coverage`` cannot beat a page that
    also agrees on ``PR``, ``test impact``, and ``changed files``. Raw fused
    retrieval remains the base signal; this is a bounded multiplier, not a
    replacement score. Counters BM25 ranking one strongly-matched constraint
    above a hit that matches every constraint moderately.
    """
    return rerank_by_context_coverage(
        hits,
        question,
        score_key="score",
        floor=_COVERAGE_FLOOR,
        absolute_stopwords=_STOPWORDS,
    )
