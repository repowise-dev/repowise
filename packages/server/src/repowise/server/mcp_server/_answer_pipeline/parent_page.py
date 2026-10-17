"""Parent-concept surfacing: a subsystem-shaped question leads with its concept page.

Retrieval ranks a subsystem's member files and child pages above the concept
page for the whole subsystem, because they match the question's noun more
tightly. Two signals pick the page, neither tuned to a repository: a tight
cluster of hits in one directory (structural), else a concept-restricted vector
search (semantic). Every other question keeps its ranking.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from repowise.server.mcp_server._answer_pipeline.vectors import question_vector, vector_search
from repowise.server.mcp_server._helpers import vector_search_timeout_s

# High precision on purpose: each phrase is anchored to an overview intent,
# because fragments like "parts of" or "which module" fill ordinary questions.
_SUBSYSTEM_QUERY_RE = re.compile(
    r"\boverview of\b|\bgive me an overview\b|\bhigh[- ]level\b|"
    r"\bmain parts of\b|\bwhat (?:are|is) the (?:main )?(?:parts|pieces|components) of\b|"
    r"\barchitecture of\b|\bwalk me through\b|"
    r"\bhow (?:is|are|does) .+ (?:organi[sz]ed|structured|laid out|put together)\b|"
    r"\bwhat subsystem\b|\bwhich subsystem\b|"
    r"\bwhere would i (?:add|put)\b|\bwhere do i (?:add|put)\b",
    re.IGNORECASE,
)

# Strongest real hits (no decision records, no pathless pages) to cluster.
_PARENT_EXPAND_TOP_N = 8
# Hits a directory must directly hold to count as a tight cluster.
_PARENT_MIN_SHARE = 2
# Just past the top score: leads without reading as a dominant retrieval.
_PARENT_EXPAND_BOOST = 1.02
# Wide, since concept pages are a small minority of the vector store.
_CONCEPT_FETCH_LIMIT = 60


def is_subsystem_query(question: str) -> bool:
    """True when the question asks about a subsystem as a whole."""
    return bool(_SUBSYSTEM_QUERY_RE.search(question or ""))


def _common_ancestor(paths: set[str]) -> str:
    """Longest shared directory prefix of the given paths, segment-wise."""
    split = [p.split("/") for p in paths]
    common: list[str] = []
    for segs in zip(*split, strict=False):
        if len(set(segs)) == 1:
            common.append(segs[0])
        else:
            break
    return "/".join(common)


async def _semantic_concept_paths(question: str, ctx: Any) -> list[str]:
    """Concept/layer page paths a concept-restricted vector search ranks first; [] on error."""
    vs = getattr(ctx, "vector_store", None)
    if vs is None:
        return []
    try:
        results = await asyncio.wait_for(
            vector_search(
                vs, question, _CONCEPT_FETCH_LIMIT, vector=await question_vector(ctx, question)
            ),
            timeout=vector_search_timeout_s(),
        )
    except Exception:
        return []
    out: list[str] = []
    for r in results:
        if getattr(r, "page_type", "") not in ("module_page", "layer_page"):
            continue
        tp = _result_target_path(r)
        if tp and tp not in out:
            out.append(tp)
    return out


def _result_target_path(result: Any) -> str:
    """A vector hit's target path, read from its page id when it carries none."""
    tp = getattr(result, "target_path", "") or ""
    if not tp:
        pid = getattr(result, "page_id", "") or ""
        tp = pid.split(":", 1)[1] if ":" in pid else pid
    return tp


def _cluster_parents(top: list[dict]) -> tuple[dict[str, int], dict[str, set[str]]]:
    """Hits directly in each directory, and the hits under each ancestor."""
    imm_count: dict[str, int] = {}
    covers: dict[str, set[str]] = {}
    for h in top:
        tp = h["target_path"].rstrip("/")
        parent = tp.rsplit("/", 1)[0] if "/" in tp else ""
        if parent:
            imm_count[parent] = imm_count.get(parent, 0) + 1
        anc = parent
        while anc:
            covers.setdefault(anc, set()).add(tp)
            anc = anc.rsplit("/", 1)[0] if "/" in anc else ""
    return imm_count, covers


async def _concept_pages(ctx: Any, candidates: set[str]) -> list[Any]:
    """Concept/layer page rows whose target path is one of *candidates*."""
    async with get_session(ctx.session_factory) as session:
        return (
            await session.execute(
                select(Page.target_path, Page.title, Page.summary, Page.page_type).where(
                    Page.target_path.in_(candidates),
                    Page.page_type.in_(("module_page", "layer_page")),
                )
            )
        ).all()


def _tight_cluster_page(
    tight_pages: set[str],
    by_path: dict[str, Any],
    imm_count: dict[str, int],
    covers: dict[str, set[str]],
) -> Any:
    """The tightest cluster's page; sibling clusters roll up to their common ancestor."""
    rollup = _common_ancestor(tight_pages)
    if len(tight_pages) > 1 and rollup in by_path and rollup in covers:
        return by_path[rollup]
    best_tp = max(tight_pages, key=lambda d: (imm_count[d], d.count("/")))
    return by_path[best_tp]


def _semantic_or_cover_page(
    semantic_paths: list[str], by_path: dict[str, Any], covers: dict[str, set[str]]
) -> Any:
    """The semantically closest concept page, else the deepest covering ancestor."""
    winner = next((by_path[tp] for tp in semantic_paths if tp in by_path), None)
    if winner is None:
        cov_pages = [a for a in covers if a in by_path]
        if cov_pages:
            winner = by_path[max(cov_pages, key=lambda a: (a.count("/"), len(covers[a])))]
    return winner


def _lead_with_parent(hits: list[dict], winner: Any) -> list[dict]:
    """Promote *winner* to lead if already retrieved, else inject it as the lead."""
    best_tp, best_title, best_summary, best_pt = winner
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0) or 1.0
    lead_score = top_score * _PARENT_EXPAND_BOOST

    retrieved = next((h for h in hits if h.get("target_path") == best_tp), None)
    if retrieved is not None:
        if retrieved.get("score", 0.0) < lead_score:
            retrieved["score"] = lead_score
            src = retrieved.get("_sources")
            if isinstance(src, set):
                src.add("parent_promote")
        hits.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return hits

    parent_hit = {
        "page_id": f"{best_pt}:{best_tp}",
        "target_path": best_tp,
        "title": best_title or f"Overview: {best_tp}",
        "summary": best_summary or "",
        "snippet": (best_summary or "")[:200],
        "page_type": best_pt or "module_page",
        "score": lead_score,
        "_sources": {"parent_expand"},
        "_expanded_from": "parent",
    }
    combined = [parent_hit, *hits]
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined


async def expand_via_parent_page(hits: list[dict], question: str, ctx: Any) -> list[dict]:
    """Lead a subsystem-shaped question with the concept page for its subsystem.

    A tight structural cluster wins over the semantic guess. The page is
    promoted in place when already retrieved, else injected as the lead.
    """
    if not hits or not is_subsystem_query(question):
        return hits
    top = [h for h in hits if h.get("target_path") and h.get("page_type") != "decision_record"][
        :_PARENT_EXPAND_TOP_N
    ]
    if not top:
        return hits

    imm_count, covers = _cluster_parents(top)
    tight_clusters = {d for d, c in imm_count.items() if c >= _PARENT_MIN_SHARE}
    semantic_paths = await _semantic_concept_paths(question, ctx)
    candidates = set(covers) | set(imm_count) | set(semantic_paths)
    if not candidates:
        return hits

    rows = await _concept_pages(ctx, candidates)
    if not rows:
        return hits
    by_path = {r[0]: r for r in rows}

    tight_pages = tight_clusters & by_path.keys()
    if tight_pages:
        winner = _tight_cluster_page(tight_pages, by_path, imm_count, covers)
    else:
        winner = _semantic_or_cover_page(semantic_paths, by_path, covers)
    if winner is None:
        return hits
    return _lead_with_parent(hits, winner)
