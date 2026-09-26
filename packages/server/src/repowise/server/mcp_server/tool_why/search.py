"""Search mode: a natural-language question, optionally anchored to targets."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._episodes import episode_evidence
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._why_evidence import annotate_response_evidence_async
from repowise.server.mcp_server._why_relevance import (
    clears_floor,
    query_scorer,
    recovery_note,
    redirect_for,
    redirect_when_served,
)
from repowise.server.mcp_server.tool_why.basis import _has_archaeology
from repowise.server.mcp_server.tool_why.caps import (
    _MAX_SEARCH_DECISIONS,
    _SEMANTIC_WINDOW,
    _cap_episodes,
    _cap_supporting_lanes,
    _cap_target_context,
    _serve_episodes,
)
from repowise.server.mcp_server.tool_why.lineage import _lineage_for_matches
from repowise.server.mcp_server.tool_why.loading import (
    _hydrate_response_decision_evidence,
    _load_corpus,
)
from repowise.server.mcp_server.tool_why.path_mode import _build_target_context
from repowise.server.mcp_server.tool_why.projection import _merge_decisions
from repowise.server.mcp_server.tool_why.ranking import (
    _collapse_restatements,
    _rank_keyword_matches,
    _record_text,
)


async def _fts_doc_results(ctx: Any, query: str) -> list:
    """Documentation hits from the lexical index.

    The keyless path, and the fallback whenever the vector store is present but
    unusable: a store that cannot rank gives the same answer as no store.
    """
    doc_results: list = []
    with contextlib.suppress(Exception):
        doc_results = await ctx.fts.search(query, limit=_SEMANTIC_WINDOW)
    return doc_results


async def _semantic_lanes(ctx: Any, query: str) -> tuple[list, list]:
    """``(decision_hits, doc_hits)`` from **one** embedding of *query*.

    One ``embed_texts`` plus ``search_by_vector`` (see ``vector_store._base``),
    with ``search`` as the fallback for a backend without raw-vector search. The
    window is split by namespace, so decisions never come back as documentation.

    The decision lane stays empty on a keyless index, deliberately: a window of
    arbitrary decisions is worse than none.
    """
    if not store_has_semantic_vectors(getattr(ctx, "vector_store", None)):
        return [], await _fts_doc_results(ctx, query)

    raw: list | None = None
    with contextlib.suppress(Exception):
        vectors = await ctx.vector_store.embed_texts([query])
        if vectors:
            raw = await ctx.vector_store.search_by_vector(vectors[0], limit=_SEMANTIC_WINDOW)
        if raw is None:
            # Backend holds no embedder, or cannot search by raw vector.
            raw = await ctx.vector_store.search(query, limit=_SEMANTIC_WINDOW)
    if raw is None:
        return [], await _fts_doc_results(ctx, query)

    decision_hits, doc_hits = [], []
    for r in raw:
        page_id = getattr(r, "page_id", "")
        if page_id.startswith(DECISION_VECTOR_PREFIX):
            decision_hits.append(r)
        else:
            doc_hits.append(r)
    return decision_hits, doc_hits


def _focus_target_context(
    target_context: dict[str, Any],
    query: str,
    targets: list[str],
    corpus: list[str],
) -> None:
    """Drop from each target card whatever does not bear on *query*, in place.

    A card is built from a path, so without this it would answer every
    question about a file with the same bytes. Only when a query is present.

    Each emptied block becomes a count plus the call that recovers it, so "no
    history mentions what you asked" reads apart from "no history".
    """
    recall = f"get_why(targets={json.dumps(targets)})"
    # Term weights from the whole decision corpus: a card's few rows are too
    # small a sample for ``term_idf`` (see its warning).
    score = query_scorer(query, corpus)
    for entry in target_context.values():
        if not isinstance(entry, dict):
            continue

        # ``governing_decisions`` is exempt: an accepted rule binds whatever the
        # question. A candidate binds nothing, so it must earn its place.
        _keep_relevant(entry, "candidate_decisions", score, recall)

        # Only the origin's decision lane; the author and first commit are
        # cheap and wanted whatever the question.
        origin = entry.get("origin")
        if isinstance(origin, dict):
            _keep_relevant(origin, "linked_decisions", score, recall)

        arch = entry.get("git_archaeology")
        if isinstance(arch, dict):
            _focus_archaeology(arch, score, recall)


def _focus_archaeology(
    arch: dict[str, Any], score: Any, recall: str
) -> None:
    """Keep the commits that carry the question's terms, count the rest.

    Here no decision cleared the floor, so these commits are the best evidence
    left.
    """
    for lane in ("file_commits", "cross_references", "git_log"):
        _keep_relevant(arch, lane, score, recall)


def _keep_relevant(block: dict[str, Any], key: str, score: Any, recall: str) -> None:
    """Keep the rows of ``block[key]`` that clear the floor for *score*.

    When none do, the lane becomes ``<key>_omitted``: a count and the recall.
    """
    rows = block.get(key)
    if not isinstance(rows, list) or not rows:
        return
    kept = [r for r in rows if clears_floor(score(json.dumps(r, default=str)))]
    if kept:
        block[key] = kept
    else:
        block.pop(key, None)
        block[f"{key}_omitted"] = recovery_note(len(rows), recall)


async def _why_no_match(
    query: str,
    targets: list[str] | None,
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
    accepted: set[str] | None = None,
) -> dict[str, Any]:
    """The whole response when no record clears the relevance floor.

    Returns before the semantic lookup and episodes: nearest neighbours always
    return something, and serving them beside a redirect is padding.

    Named targets are the exception: their git archaeology and rationale
    comments are evidence about the thing asked, as in path mode.
    """
    result: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": [],
        **redirect_for(query),
    }
    collector: OmissionCollector | None = None
    if targets:
        collector = OmissionCollector("get_why", repo_root=ctx.path)
        result["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, accepted
        )
        rationale = _mine_rationale(
            ctx.path, targets, query, max_results=1000, truncate_blocks=False
        )
        if rationale:
            result["code_rationale"] = rationale
    await _hydrate_response_decision_evidence(ctx, result, all_decisions)
    await annotate_response_evidence_async(
        result, ctx.alias, all_decisions, repo_root=ctx.path
    )
    if collector is not None:
        _focus_target_context(
            result["target_context"],
            query,
            list(targets or []),
            [_record_text(d) for d in all_decisions],
        )
        _cap_supporting_lanes(result, collector, label=query)
        _cap_target_context(result["target_context"], collector)
    # Don't redirect away from evidence this response is already serving.
    served = _served_lanes(result)
    if served:
        result.pop("try_instead", None)
        result.update(
            redirect_when_served(served, f"get_why(targets={json.dumps(targets)})")
        )
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    if collector is not None:
        collector.attach(result)
    return result


def _served_lanes(result: dict[str, Any]) -> list[str]:
    """Labels of the evidence lanes *result* carries, top level or on a target card."""
    cards = (result.get("target_context") or {}).values()
    return [
        label
        for key, label in (
            ("code_rationale", "rationale comments"),
            ("git_archaeology", "commit history"),
        )
        if result.get(key)
        or any(isinstance(e, dict) and _has_archaeology(e.get(key)) for e in cards)
    ]


async def _why_search(query: str, targets: list[str] | None, repo: str | None) -> dict:
    """Mode 3: natural-language, target-aware decision + documentation search."""
    ctx, repository, all_decisions, target_git, accepted = await _load_corpus(
        repo, targets
    )

    target_set = set(targets) if targets else set()
    # Rank wide, collapse, and project the whole pool; the cap comes last so
    # the rows it sheds are banked whole for recovery.
    ranked = _rank_keyword_matches(all_decisions, query, target_set)
    if not ranked:
        return await _why_no_match(
            query, targets, ctx, repository, all_decisions, target_git, accepted
        )
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    collapsed = _collapse_restatements(ranked)
    decision_results, doc_results = await _semantic_lanes(ctx, query)
    lineage_by_id = await _lineage_for_matches(
        ctx, [d for d, _ in collapsed], all_decisions
    )
    merged_decisions = _merge_decisions(
        collapsed, decision_results, lineage_by_id, collector, accepted
    )

    # Semantic hits are small id-plus-snippet rows, not worth dropping.
    result_data: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": merged_decisions,
        "related_documentation": _related_documentation(doc_results),
    }

    if targets:
        result_data["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, accepted
        )

    # Targets resolve through the node index; without them the question itself
    # is the only handle, so it is ranked against the bodies.
    episode_population: list[dict] = []
    episodes, pending = await asyncio.to_thread(
        episode_evidence,
        ctx.path,
        paths=targets or None,
        query=None if targets else query,
        full_population=episode_population,
    )
    if episodes and targets:
        episodes, pending = _focus_target_episodes(
            query, all_decisions, episodes, episode_population, pending
        )
    collector = _serve_episodes(
        result_data, episodes, episode_population, pending, collector, ctx.path
    )

    await _hydrate_response_decision_evidence(ctx, result_data, all_decisions)
    await annotate_response_evidence_async(
        result_data, ctx.alias, all_decisions, repo_root=ctx.path
    )
    if targets:
        _focus_target_context(
            result_data["target_context"],
            query,
            list(targets),
            [_record_text(d) for d in all_decisions],
        )
    _cap_supporting_lanes(result_data, collector, label=query)
    if targets:
        _cap_target_context(result_data["target_context"], collector)
    _cap_episodes(result_data, episodes, collector)
    result_data["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    # The middleware owns the search-mode shed order; the path-only fitter would
    # drop the primary decision lane too early.
    cap_collection(
        result_data,
        "decisions",
        result_data["decisions"],
        _MAX_SEARCH_DECISIONS,
        collector,
        label=f"search decisions beyond cap={_MAX_SEARCH_DECISIONS}",
    )
    cap_collection(
        result_data,
        "related_documentation",
        result_data["related_documentation"],
        3,
        collector,
        label="related_documentation beyond cap=3",
    )
    collector.attach(result_data)
    return result_data


def _related_documentation(doc_results: list) -> list[dict[str, Any]]:
    """Documentation hits as served rows."""
    return [
        {
            "page_id": r.page_id,
            "title": r.title,
            "page_type": r.page_type,
            "snippet": r.snippet,
            "relevance_score": r.score,
        }
        for r in doc_results
    ]


def _focus_target_episodes(
    query: str,
    all_decisions: list,
    episodes: list[dict],
    population: list[dict],
    pending: list[tuple[dict, str, str]],
) -> tuple[list[dict], list[tuple[dict, str, str]]]:
    """Narrow target-scoped episodes to those bearing on *query*.

    Trims *population* in place and returns the matching ``(episodes,
    pending)``, unchanged when every episode clears the floor.
    """
    # ``episode_evidence`` ignores the query when scoped, so filter by it here.
    score = query_scorer(query, [_record_text(d) for d in all_decisions])
    kept = [e for e in population if clears_floor(score(json.dumps(e, default=str)))]
    if len(kept) == len(population):
        return episodes, pending
    population[:] = kept
    return [e for e in episodes if e in kept], [t for t in pending if t[0] in kept]
