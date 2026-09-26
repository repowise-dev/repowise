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

    These were two awaits embedding the same string back to back, so every
    search-mode call paid two network round trips to ask one question. One
    ``embed_texts`` plus ``search_by_vector`` is the documented way to spend one
    (see ``vector_store._base``), and ``search`` remains the fallback for a
    backend that cannot search by raw vector.

    Partitioning one window also fixes a quieter bug: the doc lane took the
    nearest three pages *of any kind*, so decision records were being served
    back as "related documentation" beside the decisions list they came from.
    Splitting by namespace gives each lane only what belongs to it.

    The decision lane stays empty on a keyless index, deliberately: there is no
    lexical fallback for it, because a window of arbitrary decisions is worse
    than none for a tool whose whole job is explaining one specific thing.
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

    A target card is built from a path, so without this it answers the same
    bytes to every question asked about that file. Measured on this repo, two
    unrelated questions about ``changed_lines.py`` returned byte-identical
    ``candidate_decisions``, ``origin`` and ``git_archaeology``, together 13,397
    of a 20,000-char response, while the mined comment that answered both sat
    below them.

    Only when a query is present. A call with targets and no query *is* the
    dashboard for those files, and there is nothing to be relevant to.

    Each block reduces to a count and the call that recovers it in full, never
    to silence: "no history mentions what you asked" and "no history" are
    different answers and the reader has to be able to tell them apart.
    """
    recall = f"get_why(targets={json.dumps(targets)})"
    # One scorer for the whole response, built from the decision corpus — the
    # same vocabulary the ranked lane is judged against, so one question gets
    # one set of term weights everywhere in the answer.
    #
    # Deriving rarity from the card's own handful of strings was tried first and
    # is the degeneracy ``term_idf`` warns about: over three short rows, a word
    # none of them happens to contain outweighs the two that identify the
    # answer, and "why is JWT used for authentication" scored the row titled
    # "Use JWT for authentication" at 0.489 — under the floor, on the strength
    # of the word "used".
    score = query_scorer(query, corpus)
    for entry in target_context.values():
        if not isinstance(entry, dict):
            continue

        # ``governing_decisions`` is exempt. An accepted decision binds this
        # file whatever the question was, so a reader asking anything about it
        # is owed the rules — suppressing one for sharing no vocabulary with the
        # question would hide a ruling from the person about to edit the file.
        # A candidate binds nothing, so it has to earn its place like any other
        # unranked text.
        _keep_relevant(entry, "candidate_decisions", score, recall)

        # Only the origin story's decision lane, not the story. Reducing the
        # whole block was tried and was the wrong cut: it saved ~700 of the
        # 13,397 chars at issue while costing ``primary_author`` and the first
        # commit — facts a reader wants whatever they asked, and cheap. The
        # lane inside it is the query-blind part, because it is decisions again.
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

    Filtered rather than dropped, unlike the decision lanes. A commit message is
    prose somebody wrote about this file, so "which of these commits mention
    what I asked about" is a question it can actually answer — and on the branch
    this block is reached from, no decision cleared the floor, which makes these
    commits the best evidence left.
    """
    for lane in ("file_commits", "cross_references", "git_log"):
        _keep_relevant(arch, lane, score, recall)


def _keep_relevant(block: dict[str, Any], key: str, score: Any, recall: str) -> None:
    """Keep the rows of ``block[key]`` that clear the floor for *score*.

    When none do, the lane is replaced by ``<key>_omitted``: a count and the
    call that recovers it, so an emptied lane still reads differently from a
    lane that never had anything in it.
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

    Returns early rather than serving the closest three anyway, and returns
    *before* the semantic lookup: a nearest-neighbour search over a 614-record
    store always returns three records, and on the questions this store cannot
    answer those were "14-language AST support" for "where is the episode store"
    and "Escape LIKE patterns" for "why is entry-point candidacy decided at
    ingestion". Serving them beside a redirect would be the padding the redirect
    exists to stop, and skipping the lookup is also the latency this branch
    saves. Episodes are held back for the same reason — they are what made the
    unanswerable questions the *largest* responses in the measured set.

    Named targets are the exception, and both blocks they carry are kept. A
    caller who passes them has handed over a concrete handle, so this file's
    git archaeology and this file's rationale comments are evidence about the
    thing asked rather than the nearest guess at it — the same reason path mode
    serves them. That is also the branch the redirect is *least* useful on,
    since `get_why` on a path is the tool the caller already reached for.
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
            ctx, repository, all_decisions, target_git, targets, collector, accepted
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
    # The redirect is stapled on before the target lanes are built, because
    # without targets there is nothing else this branch can serve. With them
    # there often is, and pointing away from an answer it is holding is the
    # padding-by-another-name the redirect exists to prevent.
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
    # Rank wide, collapse restatements, and project the whole surviving pool.
    # The decisions cap comes last, at the bottom of this function, and what it
    # sheds is banked whole: the omission document is the projected rows, so
    # building only the three that are served would leave recovery with nothing
    # to hand back. Measured on this repo, a
    # realistic question collapses to a median of 7 records and up to 33, and
    # capping first saves 4-21ms of a call whose cost is dominated by a fixed
    # annotation floor. The recovery is worth more than the milliseconds.
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

    # Semantic hits append as id-plus-snippet at roughly 200 chars each, so
    # dropping them to fit a record count would cost the lane that carries a
    # calibrated relevance score for the sake of no measurable payload.
    result_data: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": merged_decisions,
        "related_documentation": _related_documentation(doc_results),
    }

    # If targets provided, include target context
    if targets:
        result_data["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, collector, accepted
        )
        # The comment-mining fallback that used to sit here was gated on
        # ``not merged_decisions``, which nothing ever reached. It now lives in
        # ``_why_no_match``, behind the floor — the condition it always meant.

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
    # Episode overflow is banked here because it is produced before the shared
    # final budget pass. The middleware owns the complete search-mode shed
    # order after trust metadata is attached; routing this shape through the
    # path-only fitter would discard its primary decision lane prematurely.
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
    # ``episode_evidence`` takes *either* a scope or a query, and a scope
    # wins, so the targeted lane never saw the question: the same three
    # episodes came back for every question asked about a file. Scoping is
    # still the right retrieval — these are the episodes bound to the file
    # asked about — but what survives has to bear on what was asked.
    score = query_scorer(query, [_record_text(d) for d in all_decisions])
    kept = [e for e in population if clears_floor(score(json.dumps(e, default=str)))]
    if len(kept) == len(population):
        return episodes, pending
    population[:] = kept
    return [e for e in episodes if e in kept], [t for t in pending if t[0] in kept]
