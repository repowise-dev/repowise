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
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json as _json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import AnswerCache, Page, WikiSymbol
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp

# How many top retrieval hits to enrich with WikiSymbol context. Enriching
# every hit produces large responses that bloat the cached prompt prefix on
# multi-turn agent sessions without changing the answer — the agent typically
# cites the top-1 file. Top-2 captures the primary navigation need with a
# bounded payload.
_ENRICH_TOP_N_HITS = 2
# How many symbols per enriched file. Bounded to keep the context block from
# growing unboundedly on dense files; the limit is sufficient to surface both
# foundational types and a representative function/method.
_MAX_SYMBOLS_PER_HIT = 4

# Sort priority by symbol kind. Classes first because "what does X do" /
# "which class inherits from Y" questions resolve at the class level. Then
# top-level functions, then methods (which usually only matter once the
# class context is established).
_KIND_PRIORITY = {"class": 0, "interface": 0, "function": 1, "method": 2}
# Per-symbol docstring truncation. Keeps the context block bounded — the
# first sentence is typically sufficient and trailing prose mostly contributes
# cache-write cost on follow-up turns.
_MAX_SYMBOL_DOC_CHARS = 120

# Confidence gate for synthesis. When the top retrieval hit is NOT clearly
# dominant relative to the second-best hit, skip LLM synthesis and return
# ranked snippets only. This forces the agent to ground in source rather than
# trust a possibly-wrong frame. Generic, repo-agnostic, no question parsing.
# Failure modes addressed:
#   (a) wrong-target retrieval where top-1 and top-2 are both plausible;
#   (b) synthesis hallucination on tangential top hits.
_DOMINANCE_RATIO = 1.2
_COVERAGE_THRESHOLD = 0.66
# The dominance ratio threshold (top_score / second_score >= 1.2x) separates
# reliable retrievals from ambiguous ones. This is a property of BM25-style
# retrieval with a coverage re-ranker on top, not of any particular repository;
# tune if a deployment shows systematic over- or under-gating.

# When the gate triggers and we drop synthesis, fetch this many chars of
# real page content per top hit so the agent has substantive raw material
# to ground in (vs. one-line summary that's too thin to act on).
_GATED_EXCERPT_CHARS = 600
_GATED_RETURN_HITS = 3

# Intersection-retrieval connectives. If a question contains any of these
# (case-insensitive whole-word), it's likely a relational/multi-entity
# question. We split the question on the connective, run two FTS passes,
# and boost any page that appears in BOTH result sets — the page at the
# intersection is much more likely to be the actual answer than a page
# at the top of either single-side query.
# This is grammar, not domain — the same list applies to any English-language
# code question, independent of the repository or codebase.
_RELATIONAL_CONNECTIVES = (
    " between ", " from ", " across ", " through ", " with ",
    " and ", " versus ", " vs ",
)

# Term-coverage re-ranker tuning. Multiplies BM25 by (FLOOR + (1-FLOOR)*coverage)
# where coverage = (# distinct query terms present in hit) / (# query terms).
# FLOOR=0.5 → single-concept questions (coverage≈1.0) are unaffected;
# multi-constraint questions where a hit covers 1/3 of terms get scored at 0.67
# of their raw BM25 (vs 1.0 for a hit covering 3/3). Conjunctive coverage
# becomes a tie-breaker rather than a hard filter.
_COVERAGE_FLOOR = 0.5
# English stopwords — minimal list, just enough to keep "what is the" from
# dominating coverage. Not language-specific, not repo-specific.
_STOPWORDS = frozenset({
    "a","an","the","is","are","was","were","be","been","being","of","to","in",
    "on","at","by","for","with","from","as","that","this","these","those","it",
    "its","and","or","but","not","no","do","does","did","done","have","has",
    "had","what","which","who","whom","whose","when","where","why","how","can",
    "could","should","would","may","might","will","shall","i","you","he","she",
    "we","they","me","him","her","us","them","my","your","his","their","our",
    "if","then","than","so","such","there","here","about","into","through",
    "between","across","over","under","up","down","out","off","via",
})
# Cap on bytes read from source per symbol when we recover a real signature
# from disk (multi-line def with type annotations). Anything longer than this
# gets truncated; the agent can call get_symbol for the full body.
_MAX_RICH_SIG_LINES = 4


def _hash_question(question: str) -> str:
    """Stable SHA-256 of the normalized question. Lowercase + strip + collapse ws."""
    norm = " ".join(question.lower().strip().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

_log = __import__("logging").getLogger("repowise.mcp.answer")

_SYSTEM_PROMPT = (
    "You are a code-aware retrieval assistant. Given a developer question and "
    "excerpts from a project wiki, answer in 2–5 sentences. Cite the source "
    "files by relative path inline like (path/to/file.py). If the excerpts do "
    "not contain enough information, say so explicitly and suggest which files "
    "the developer should inspect. Never invent file paths."
)

_USER_TEMPLATE = """\
Question: {question}

Project wiki excerpts (top {n} retrieval hits):

{context}

Answer in 2–5 sentences. Cite file paths inline. If the excerpts are not
sufficient, say so and list the most likely files to inspect.
"""


def _resolve_provider_for_answer():
    """Best-effort provider lookup mirroring cli/helpers.resolve_provider.

    Avoids the click dependency from the cli package. Returns a BaseProvider
    or None if no API key / provider is configured.
    """
    try:
        from repowise.core.providers.llm.registry import get_provider
    except Exception:
        _log.debug("Provider registry import failed", exc_info=True)
        return None

    name = os.environ.get("REPOWISE_PROVIDER")
    model = os.environ.get("REPOWISE_DOC_MODEL") or os.environ.get("REPOWISE_MODEL")

    def _try(provider_name: str, **kwargs: Any):
        try:
            return get_provider(provider_name, **kwargs)
        except Exception:
            _log.debug("get_provider(%s) failed", provider_name, exc_info=True)
            return None

    # Explicit selection wins.
    if name:
        kw: dict[str, Any] = {}
        if model:
            kw["model"] = model
        if name == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            kw["api_key"] = os.environ["ANTHROPIC_API_KEY"]
        elif name == "openai" and os.environ.get("OPENAI_API_KEY"):
            kw["api_key"] = os.environ["OPENAI_API_KEY"]
        elif name == "gemini" and (
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        ):
            kw["api_key"] = os.environ.get("GEMINI_API_KEY") or os.environ.get(
                "GOOGLE_API_KEY"
            )
        elif name == "ollama" and os.environ.get("OLLAMA_BASE_URL"):
            kw["base_url"] = os.environ["OLLAMA_BASE_URL"]
        return _try(name, **kw)

    # Auto-detect from API keys.
    if os.environ.get("ANTHROPIC_API_KEY"):
        kw = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
        if model:
            kw["model"] = model
        return _try("anthropic", **kw)
    if os.environ.get("OPENAI_API_KEY"):
        kw = {"api_key": os.environ["OPENAI_API_KEY"]}
        if model:
            kw["model"] = model
        return _try("openai", **kw)
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        kw = {
            "api_key": os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        }
        if model:
            kw["model"] = model
        return _try("gemini", **kw)
    if os.environ.get("OLLAMA_BASE_URL"):
        kw = {"base_url": os.environ["OLLAMA_BASE_URL"]}
        if model:
            kw["model"] = model
        return _try("ollama", **kw)
    return None


def _build_context_block(hits: list[dict], max_chars_per_hit: int = 800) -> str:
    """Format retrieval hits as a compact text block for the LLM.

    Each hit includes:
      * file path + title + retrieval score
      * file-level summary (Page.summary, capped at max_chars_per_hit)
      * up to _MAX_SYMBOLS_PER_HIT WikiSymbol entries (signature + truncated
        docstring) — the critical addition that turns get_answer from a
        navigator into a real synthesizer for symbol-level questions.
    """
    parts = []
    for i, h in enumerate(hits, start=1):
        body_src = h.get("summary") or h.get("snippet") or ""
        body = body_src[:max_chars_per_hit]
        block = [
            f"[{i}] {h['target_path']} (score={h['score']:.3f})",
            f"    title: {h['title']}",
            f"    summary: {body}",
        ]
        symbols = h.get("symbols") or []
        if symbols:
            block.append("    symbols:")
            for s in symbols[:_MAX_SYMBOLS_PER_HIT]:
                sig = s.get("signature") or s.get("name") or ""
                kind = s.get("kind") or "?"
                doc = (s.get("docstring") or "").strip()
                if doc:
                    doc_one_line = " ".join(doc.split())[:_MAX_SYMBOL_DOC_CHARS]
                    block.append(f"      - [{kind}] {sig}")
                    block.append(f"          {doc_one_line}")
                else:
                    block.append(f"      - [{kind}] {sig}")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _read_signature_from_source(
    repo_root: Path | None, file_path: str, start_line: int
) -> str | None:
    """Read the symbol's actual signature line from disk.

    Returns the def/class line (or its multi-line continuation) verbatim from
    the source file. Captures everything WikiSymbol.signature strips:
      * base classes for `class Foo(Bar, Baz):`
      * decorators (one line above the def)
      * full type annotations across line continuations

    None on any failure — caller falls back to the stored signature.
    """
    if repo_root is None:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        # Defense in depth: never read outside the repo root.
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or start_line < 1 or start_line > len(lines):
        return None
    # Walk forward up to _MAX_RICH_SIG_LINES until we close the parenthesis
    # group (Python signatures often span multiple lines for type hints).
    sig_lines: list[str] = []
    paren_depth = 0
    for i in range(start_line - 1, min(start_line - 1 + _MAX_RICH_SIG_LINES, len(lines))):
        line = lines[i]
        sig_lines.append(line.strip())
        paren_depth += line.count("(") - line.count(")")
        if line.rstrip().endswith(":") and paren_depth <= 0:
            break
    if not sig_lines:
        return None
    return " ".join(sig_lines)


async def _hydrate_symbols_for_hits(
    session, repo_id: str, hits: list[dict]
) -> None:
    """Mutate `hits` in place: attach `symbols` list to top-N file_page hits.

    Only the top _ENRICH_TOP_N_HITS hits get enriched — others would just bloat
    the cached prompt prefix on follow-up turns without changing the answer.

    For each enriched symbol we ALSO try to recover the real source-line
    signature from disk (`_read_signature_from_source`) so base classes,
    decorators, and full type annotations reach the LLM. WikiSymbol.signature
    strips these at parse time, so the on-disk read is what gives the LLM a
    faithful view of the symbol's interface.
    """
    # Identify the top file_page hits in retrieval-rank order. `hits` is
    # already sorted by descending score upstream.
    enrich_paths: list[str] = []
    for h in hits:
        if (
            h.get("target_path")
            and h.get("page_type") == "file_page"
            and len(enrich_paths) < _ENRICH_TOP_N_HITS
        ):
            enrich_paths.append(h["target_path"])
    if not enrich_paths:
        return

    res = await session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_(enrich_paths),
        )
        .order_by(WikiSymbol.file_path, WikiSymbol.start_line)
    )
    by_file: dict[str, list[dict]] = {}
    repo_root = Path(_state._repo_path) if _state._repo_path else None
    for row in res.scalars().all():
        rich_sig = _read_signature_from_source(
            repo_root, row.file_path, row.start_line
        )
        by_file.setdefault(row.file_path, []).append(
            {
                "name": row.name,
                "kind": row.kind,
                # Prefer the real source line (has bases / decorators / types)
                # falling back to the stripped WikiSymbol.signature on failure.
                "signature": rich_sig or row.signature,
                "docstring": row.docstring or "",
                "start_line": row.start_line,
            }
        )
    # Cap each list to _MAX_SYMBOLS_PER_HIT. Sort by start_line ASC —
    # natural document order is the most general default. Kind-priority
    # sorting (classes before functions before methods) is available via
    # _KIND_PRIORITY but is not applied here, since reordering symbols away
    # from source order can mislead the LLM about file structure.
    for path, syms in by_file.items():
        syms.sort(key=lambda s: s["start_line"])
        by_file[path] = syms[:_MAX_SYMBOLS_PER_HIT]
    for h in hits:
        if h.get("target_path") in by_file:
            h["symbols"] = by_file[h["target_path"]]


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
            right = q[idx + len(conn):].strip()
            # Both sides must have at least 3 content terms to be a real
            # multi-entity question (not e.g. "what is X and how").
            if len(_question_terms(left)) >= 3 and len(_question_terms(right)) >= 3:
                return [left, right]
    return None


async def _intersection_boost(question: str, hits: list[dict]) -> None:
    """For relational questions, boost any hit that appears in both halves
    of a split-FTS retrieval. Mutates `hits` in place: adds a multiplicative
    bonus to `score` for hits that appear in both subset retrievals.

    Universal IR principle: pages at the intersection of two query halves
    are much more likely to answer relational questions than pages at the
    top of either half alone. Independent of repo or domain.
    """
    parts = _split_relational(question)
    if parts is None or _state._fts is None:
        return
    sub_hit_ids: list[set] = []
    for sub_q in parts:
        try:
            sub = await asyncio.wait_for(
                _state._fts.search(sub_q, limit=15), timeout=3.0
            )
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


async def _enrich_gated_excerpts(hits: list[dict]) -> None:
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
        async with get_session(_state._session_factory) as session:
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


def _question_terms(question: str) -> list[str]:
    """Extract content terms from a question. Lowercase, alnum-tokenized,
    stopwords + length<3 dropped. Used by the term-coverage re-ranker."""
    import re
    raw = re.findall(r"[a-zA-Z0-9_]+", question.lower())
    return [t for t in raw if len(t) >= 3 and t not in _STOPWORDS]


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
        haystack = " ".join([
            h.get("title", "") or "",
            h.get("snippet", "") or "",
            h.get("summary", "") or "",
        ]).lower()
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


def _confidence_from_scores(scores: list[float]) -> str:
    """Map raw FTS scores to a coarse confidence label.

    The thresholds are intentionally generous on the low end — when retrieval
    finds *anything* we still let the agent see it, but mark it 'low' so the
    workflow forces verification.
    """
    if not scores:
        return "low"
    top = scores[0]
    gap = top - (scores[1] if len(scores) > 1 else 0.0)
    if top >= 1.0 and gap >= 0.2:
        return "high"
    if top >= 0.5:
        return "medium"
    return "low"


@mcp.tool()
async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
) -> dict:
    """One-call RAG: answer a code question. Always your first call.

    Returns {answer, citations, confidence, fallback_targets}. High-confidence
    answers name concrete files/symbols and can be used with less verification.
    For medium/low confidence, cross-reference with search_codebase + get_context.
    Always verify cited file paths exist before acting on them.

    Args:
        question: developer question.
        scope: optional path prefix to restrict retrieval (e.g. "src/pkg/").
        repo: repository identifier; usually omitted.
    """
    t0 = time.perf_counter()
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

    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)
        repo_id = repository.id

    # --- Cache lookup --------------------------------------------------------
    # Scope: ignore the (rare) `scope` argument in the cache key for now;
    # scoped queries are uncommon and including scope would balloon hit rate
    # variance. We hash on (repo_id, normalized_question) only.
    qhash = _hash_question(question)
    async with get_session(_state._session_factory) as session:
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
            payload["_meta"] = _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                cached=True,
                hint=_answer_hint(payload.get("confidence", "low"), len(payload.get("retrieval", []))),
            )
            return payload

    # --- Retrieval (FTS) ---------------------------------------------------
    raw_hits: list[Any] = []
    if _state._fts is not None:
        with contextlib.suppress(Exception):
            # Pull a wider candidate set so the term-coverage re-ranker has
            # room to push conjunctive matches up the list before we cap to 5.
            raw_hits = await asyncio.wait_for(
                _state._fts.search(question, limit=15), timeout=5.0
            )

    # Hydrate hits with target_path + summary from the Page table.
    hits: list[dict] = []
    if raw_hits:
        page_ids = [h.page_id for h in raw_hits]
        async with get_session(_state._session_factory) as session:
            res = await session.execute(
                select(
                    Page.id,
                    Page.target_path,
                    Page.summary,
                    Page.page_type,
                ).where(Page.id.in_(page_ids))
            )
            meta_by_id = {
                row[0]: {
                    "target_path": row[1],
                    "summary": row[2] or "",
                    "page_type": row[3],
                }
                for row in res.all()
            }
        for h in raw_hits:
            meta = meta_by_id.get(h.page_id, {})
            target_path = meta.get("target_path", "")
            if scope and target_path and not target_path.startswith(scope):
                continue
            hits.append(
                {
                    "page_id": h.page_id,
                    "title": h.title,
                    "target_path": target_path,
                    "page_type": meta.get("page_type", h.page_type),
                    "snippet": h.snippet,
                    "summary": meta.get("summary", ""),
                    "score": float(h.score or 0.0),
                }
            )

    # Term-coverage re-rank before the cap so conjunctive matches survive.
    hits = _rerank_by_coverage(hits, question)
    # Intersection-retrieval boost for relational questions (multi-entity).
    # Pages at the intersection of two split-FTS halves get a 2× bonus.
    with contextlib.suppress(Exception):
        await _intersection_boost(question, hits)
    # Always cap retrieval hits at 5 for the response payload.
    hits = hits[:5]

    # Enrich each file_page hit with its top-N WikiSymbol rows. This is the
    # critical fix for symbol-level questions — without it the LLM only sees
    # file-level summaries and consistently refuses to identify specific
    # classes/functions named in the question.
    if hits:
        with contextlib.suppress(Exception):
            async with get_session(_state._session_factory) as session:
                await _hydrate_symbols_for_hits(session, repo_id, hits)

    fallback_targets = [
        h["target_path"] for h in hits if h.get("target_path")
    ]

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
        dominant = (top_score / second_score) >= _DOMINANCE_RATIO
        if not dominant:
            # Enrich top hits with substantive excerpts so the agent has
            # real material to ground in (not one-line summaries).
            await _enrich_gated_excerpts(hits)
            return {
                "answer": "",
                "citations": [],
                "confidence": "low",
                "fallback_targets": fallback_targets,
                "retrieval": hits[:_GATED_RETURN_HITS],
                "note": (
                    "Multiple plausible candidates — synthesis skipped to "
                    "avoid anchoring on a wrong frame. Each retrieval entry "
                    "includes an excerpt from the page; read them and pick "
                    "the one that actually answers the question."
                ),
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("low", len(hits)),
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
    provider = _resolve_provider_for_answer()
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
            ),
        }

    user_prompt = _USER_TEMPLATE.format(
        question=question.strip(),
        n=len(hits),
        context=_build_context_block(hits),
    )

    answer_text = ""
    try:
        response = await asyncio.wait_for(
            provider.generate(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=512,
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
    if _ratio >= _DOMINANCE_RATIO:
        confidence = "high"
    else:
        confidence = "medium"

    payload = {
        "answer": answer_text,
        "citations": citations,
        "confidence": confidence,
        "fallback_targets": fallback_targets,
        "retrieval": hits,
    }
    # When confidence is high, document the signal strength for the consumer.
    if confidence == "high":
        payload["note"] = (
            "High confidence: top retrieval result clearly dominates "
            f"(dominance ratio {_ratio:.2f}x). This answer is likely accurate, "
            "but verify cited file paths exist before acting on them."
        )

    # Persist to cache. Best-effort: cache failures must NEVER block the
    # response (we already have the answer in hand).
    if answer_text:
        with contextlib.suppress(Exception):
            async with get_session(_state._session_factory) as session:
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
    )
    return payload
