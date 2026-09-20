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


# How many files ``candidates`` names.
#
# Was 20, on the estimate that "twenty path lines cost roughly 800 characters
# against the ~10k a get_answer response already spends". Measured on the wire
# 2026-08-11 the block is **3,107-3,279 characters, up to 39.9% of a
# get_answer payload** — four times the estimate, because ``defines`` and the
# paths themselves are both longer than a bare path line. That estimate was
# also made before ``defines`` existed.
#
# Five, not zero. Rows 6-20 appear in no other block, but nor are rows 1-5
# redundant: on the repowise samples ``candidates`` is the only block naming
# files outside the top-two ``citations``, and dropping it whole (which the
# CLI projection does) pushes the agent into a Grep that costs more than the
# rows saved. The head keeps the ``defines`` budget, so the navigational value
# per character goes up.
_CANDIDATE_LIMIT = 5


def serialize_candidates(hits: list[dict], *, limit: int = _CANDIDATE_LIMIT) -> list[dict]:
    """The files retrieval ranked, one line each, ordered best first.

    Separate from ``retrieval`` on purpose, and deliberately not
    confidence-gated. ``retrieval`` is *evidence*: enriched hits an agent reads
    to check the prose, so it is right for it to shrink as the prose gets more
    trustworthy. This block is *navigation*: the shortlist of files worth
    opening next. Under the old shape a confident answer named zero files and a
    medium one named two, which is backwards. The more sure we are of a
    subsystem, the better placed we are to say which files it lives in.

    One entry per distinct path, ``{path, lines?}``. Line bounds are attached
    only where a hit already carries hydrated symbols; nothing is fetched to
    build this.

    ``path`` is always a **file** path, resolved through ``hit_file_path``. A
    ``symbol_spotlight`` hit's ``target_path`` is ``file.py::Symbol``, which is
    a page identifier, not something a consumer can open; two distinct symbols
    in one file are also one file to read, so they collapse to one entry here.

    Pages naming no file at all are skipped rather than emitted (finding A15).
    A module page's target_path is a structural group key that reads like a
    directory and an onboarding page's is a slot name, so every "does this look
    like a path" heuristic says yes and the agent that opens it gets an error.
    Scoring impact is about zero, measured; it is wrong on the same argument
    A14 was, which is that this field has one meaning and it is not "page id".

    ``defines`` carries what the file declares, as ``name:line`` pairs, when
    ``_hydrate_candidate_defines`` resolved any. That is the difference between
    a path the agent has to Grep and a line it can read: 434 of the 499 paths a
    get_answer response served on the 25 flow questions carried no content
    whatsoever, and the Layer B taxonomy judged 89% of the agent's post-answer
    searches to be exactly that expansion. **Line numbers here are as indexed
    and are not verified against the live file** (unlike ``get_symbol``); they
    are navigation, not a citation.

    **Which paths are emitted, and in what order, is not affected by any of
    this.** ``defines`` is attached to entries the existing loop already built,
    so an added or exhausted budget can never add, drop or reorder a path.
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

    Internal scoring fields (``_coverage``, ``_coverage_multiplier``,
    ``_confidence_score_factor``, ``_raw_score``, ``_sources``,
    ``_pagerank``, …) and ``page_id`` are ranking debug an agent can do
    nothing with; they were ~70% of a get_answer response by volume. Zero
    information loss for the consumer: path, title, summary, snippet,
    excerpt, score, and hydrated symbols all survive.

    ``summary_chars`` truncates summaries (medium-confidence diet);
    ``symbols_for_expanded=False`` drops symbol enrichment from hits that
    only entered via 1-hop graph expansion (they are routing material, not
    answer material). ``lean_symbols=True`` keeps each symbol pipeable
    (name/kind/signature/lines) but drops docstrings and excerpts — for the
    gated low-confidence path, where the hits are candidates to pick between,
    not answer material, and ``best_guesses`` + ``code_rationale`` already
    carry the choosing signal.

    ``excerpt_rows`` serves the page excerpt on the first N rows only. An
    excerpt is ~1,500 characters against ~300 for the whole rest of a row, so it
    is essentially the entire cost of this block; rows past the cut keep path,
    title, summary, snippet and score, which is a described candidate rather
    than a bare pointer. Deliberately a *field* cut and not a row cut: dropping
    rows takes paths out of the response, and a named path costs almost nothing.
    """
    out: list[dict] = []
    for idx, h in enumerate(hits[: limit if limit is not None else len(hits)]):
        target = h.get("target_path")
        entry: dict[str, Any] = {"path": target}
        # A symbol_spotlight page's target_path is ``file.py::Symbol``: a page
        # id, not a path a consumer can open. Keep it (callers pipe it into
        # get_symbol) and name the file too, so ``path`` never has to be
        # guessed at by anything downstream.
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

    Every consumer of a hit — the synthesis prompt and the pointer payload
    alike — otherwise sees only the page's one-line LLM summary or a 200-char
    opener, next to a symbol block carrying docstrings and source bodies. A
    consumer given names but no prose reconstructs rationale from the names,
    which is a confident wrong answer rather than a thin one.

    Returns the number of top hits left without page content. That count is
    the point: a hit reaching synthesis with no body used to be invisible,
    which is how this went unnoticed for as long as it did.
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
        # Never fail the answer over an excerpt fetch — but never lose the
        # fact either. Without this line the whole prompt silently degrades
        # to summaries and the answer still looks confident.
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
    """Re-rank hybrid hits by intent-bearing term coverage.

    Coverage includes the path/module identity as well as title and prose.
    Terms every candidate repeats carry less weight than discriminating terms,
    so a generic lexical overlap such as ``coverage`` cannot beat a page that
    also agrees on ``PR``, ``test impact``, and ``changed files``. Raw fused
    retrieval remains the base signal; this is a bounded multiplier, not a
    replacement score.

    This addresses a common BM25 failure mode where a hit that matches one
    constraint very strongly can outrank a hit that matches all constraints
    moderately — the latter is usually the better answer for multi-constraint
    questions.
    """
    return rerank_by_context_coverage(
        hits,
        question,
        score_key="score",
        floor=_COVERAGE_FLOOR,
        absolute_stopwords=_STOPWORDS,
    )
