"""Parent-concept surfacing: a subsystem-shaped question leads with its concept page."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from repowise.server.mcp_server._answer_pipeline.vectors import question_vector, vector_search
from repowise.server.mcp_server._helpers import vector_search_timeout_s

# ---------------------------------------------------------------------------
# Stage: parent-concept surfacing (subsystem-shaped questions)
# ---------------------------------------------------------------------------

# A subsystem-shaped question asks about a part of the system as a whole:
# "overview of X", "main parts of X", "what subsystem does Y belong to",
# "where would I add a Z". For these the best answer is the concept page that
# documents the whole subsystem, not one of its member files or a more specific
# child concept page. Retrieval ranks the specific children above the parent (a
# child's embedding matches the query's noun more tightly than the broader
# parent), so the parent concept/rollup page never surfaces even though it
# exists. This gate is purely on the query's natural-language shape — no
# repo-specific vocabulary — so it generalises across codebases.
# Kept deliberately high-precision: bare fragments like "parts of", "belongs to",
# "structure of", or "which module" appear constantly inside ordinary how/where
# questions ("what parts of the code touch this", "which module imports config"),
# so each phrase is anchored to an unambiguous subsystem-overview intent. Better
# to miss an oddly-worded subsystem question (it just keeps today's ranking) than
# to reorder an implementation question's file hits.
_SUBSYSTEM_QUERY_RE = re.compile(
    r"\boverview of\b|\bgive me an overview\b|\bhigh[- ]level\b|"
    r"\bmain parts of\b|\bwhat (?:are|is) the (?:main )?(?:parts|pieces|components) of\b|"
    r"\barchitecture of\b|\bwalk me through\b|"
    r"\bhow (?:is|are|does) .+ (?:organi[sz]ed|structured|laid out|put together)\b|"
    r"\bwhat subsystem\b|\bwhich subsystem\b|"
    r"\bwhere would i (?:add|put)\b|\bwhere do i (?:add|put)\b",
    re.IGNORECASE,
)

# How many of the strongest real (non-noise) hits to cluster when looking for
# their shared subsystem. Decision records and pages without a path are skipped
# so a query whose top slots are crowded by decision noise still clusters on its
# real member files.
_PARENT_EXPAND_TOP_N = 8
# A directory is a tight structural cluster when it is the immediate parent of at
# least this many surfaced hits. A lone surfaced file is not enough structural
# signal on its own; that case is left to the semantic concept lookup.
_PARENT_MIN_SHARE = 2
# The injected parent leads the surface (it is the answer) but only just: a
# small multiplier over the current top score keeps it first without
# manufacturing a dominant retrieval that would inflate confidence to "high".
_PARENT_EXPAND_BOOST = 1.02
# How deep to look in a concept-restricted vector search for the subsystem page
# a query is semantically about. The window is wide because concept pages are a
# small minority of the corpus, so the right one can sit below many file/symbol
# hits before this filter drops those away.
_CONCEPT_FETCH_LIMIT = 60


def is_subsystem_query(question: str) -> bool:
    """True when the question asks about a subsystem/module as a whole, so the
    concept page for that subsystem should lead rather than its member files."""
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
    """Target paths of the concept/layer pages a concept-restricted vector search
    ranks highest for the question, best-first.

    This is the "guaranteed concept-page candidate": when a subsystem query's
    file hits land in the wrong neighborhood (a UI consumer of the subsystem
    rather than the subsystem itself), the subsystem's own concept page still
    matches the query semantically and surfaces here even though it never
    entered the file-dominated main fetch. Best-effort: returns [] on any error
    so the caller degrades to the structural path.
    """
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
        if getattr(r, "page_type", "") in ("module_page", "layer_page"):
            tp = getattr(r, "target_path", "") or ""
            if not tp:
                pid = getattr(r, "page_id", "") or ""
                tp = pid.split(":", 1)[1] if ":" in pid else pid
            if tp and tp not in out:
                out.append(tp)
    return out


def _cluster_parents(top: list[dict]) -> tuple[dict[str, int], dict[str, set[str]]]:
    """Each directory's count of hits directly in it, and the hits under each ancestor."""
    # For each surfaced hit, count its immediate parent directory and credit
    # every strict ancestor with covering it. A dir that is the immediate parent
    # of two or more surfaced hits is a TIGHT cluster: the query's own member
    # files sit directly in it. Coverage by a distant ancestor (a broad root that
    # merely contains scattered hits) is deliberately not enough — that is what
    # separates a real subsystem from the repository root.
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
    """The page for the tightest structural cluster, rolled up over split siblings."""
    rollup = _common_ancestor(tight_pages)
    if len(tight_pages) > 1 and rollup in by_path and rollup in covers:
        return by_path[rollup]
    best_tp = max(tight_pages, key=lambda d: (imm_count[d], d.count("/")))
    return by_path[best_tp]


def _semantic_or_cover_page(
    semantic_paths: list[str], by_path: dict[str, Any], covers: dict[str, set[str]]
) -> Any:
    """The semantically-closest concept page, else the deepest covering ancestor."""
    # No tight cluster: the semantically-closest concept page (best-first),
    # then any weak cover ancestor as a last resort.
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

    # Already in the candidate set but ranked below its own children (they embed
    # tighter to the query noun than the broader parent), so the top-5 cap drops
    # it. Promote it to lead in place rather than adding a duplicate. The bump is
    # small (just past the top hit) so it never manufactures a dominant retrieval
    # that would read "high".
    for h in hits:
        if h.get("target_path") == best_tp:
            if h.get("score", 0.0) < lead_score:
                h["score"] = lead_score
                src = h.get("_sources")
                if isinstance(src, set):
                    src.add("parent_promote")
            hits.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return hits

    # Not retrieved at all: inject it as the leading hit.
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

    Two complementary signals pick that page, neither tuned to any repository:

    * Structural — when the surfaced file hits cluster under an ancestor
      directory that has a concept/rollup page, that ancestor IS the subsystem.
      Ancestors are found by walking each hit's target_path up the tree (the
      same relationship the generator uses to mint rollups). The tightest
      (deepest) ancestor covering >=2 hits wins, never a catch-all near the root.
    * Semantic — when the file hits land in the wrong neighborhood (a consumer
      of the subsystem, not the subsystem), the subsystem's own concept page
      still matches the query and is recovered by a concept-restricted vector
      search. Used only when no structural cluster is found, so a strong file
      cluster is never overridden by a semantic guess.

    The chosen page is promoted in place if already retrieved (its children
    out-embed it, so the cap drops it) or injected as the leading hit otherwise.
    A no-op on every non-subsystem question, so file/implementation queries keep
    today's ranking untouched.
    """
    if not hits or not is_subsystem_query(question):
        return hits
    # Cluster on the strongest real hits: skip decision records and any hit
    # without a path so decision noise crowding the top slots can't starve the
    # clustering of the member files that reveal the subsystem.
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

    # A tight structural cluster is the query's own files agreeing on a subsystem,
    # so it outranks the semantic guess. When several sibling dirs each cluster
    # (a subsystem split into subdirectories), roll up to their common ancestor
    # page so the answer is the subsystem, not one arbitrary half of it. With no
    # tight cluster the file hits landed in the wrong neighborhood, so the concept
    # page the query is semantically about wins instead.
    tight_pages = tight_clusters & by_path.keys()
    if tight_pages:
        winner = _tight_cluster_page(tight_pages, by_path, imm_count, covers)
    else:
        winner = _semantic_or_cover_page(semantic_paths, by_path, covers)
    if winner is None:
        return hits
    return _lead_with_parent(hits, winner)
