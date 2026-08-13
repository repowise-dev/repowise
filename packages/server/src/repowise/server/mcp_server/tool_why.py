"""MCP Tool 4: get_why — intent archaeology and decision search."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
)
from repowise.core.precedent.currency import describe_decision_currency
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector, effective_char_budget
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._episodes import bank_overflow, episode_evidence
from repowise.server.mcp_server._helpers import (
    _build_origin_story,
    _compute_alignment,
    _decision_body,
    _get_exclude_spec,
    _get_repo,
    _is_path,
    _resolve_all_contexts,
    _resolve_repo_context,
    _unsupported_repo_all,
    decision_is_excluded,
    filter_path_list,
    is_excluded,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta


@mcp.tool()
async def get_why(
    query: str | None = None,
    targets: list[str] | None = None,
    repo: str | None = None,
) -> dict:
    """Why this code is shaped this way — decision records + evidence commits.

    Call before refactors or pattern divergences. Query modes: a question
    ("why is auth using JWT?"), a file path (governing decisions + origin
    story + alignment score), a question anchored to targets, or no query
    (decision health dashboard). Falls back to git archaeology when no
    decisions exist for a path — never empty.

    Args:
        query: question, file/module path, or omit for the dashboard.
        targets: optional file paths to anchor the search.
        repo: usually omitted.
    """
    # --- repo="all": search decisions across ALL repos ---
    if repo == "all":
        if not query:
            return _unsupported_repo_all("get_why (health dashboard)")
        return await _why_workspace_search(query)

    # --- Mode 1: No query → health dashboard ---
    if not query:
        return await _why_health_dashboard(repo)

    # --- Mode 2: Path → decisions, origin story, alignment ---
    if _is_path(query):
        return await _why_path(query, repo)

    # --- Mode 3: Natural language → target-aware search ---
    return await _why_search(query, targets, repo)


async def _why_workspace_search(query: str) -> dict:
    """repo="all": keyword-search decisions across every repo in the workspace."""
    contexts = await _resolve_all_contexts()
    merged: list[dict] = []
    query_words = query.lower().split()
    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            res = await session.execute(
                select(DecisionRecord).where(
                    DecisionRecord.repository_id == repository.id,
                )
            )
            for d in res.scalars().all():
                text = f"{d.title} {d.decision} {d.rationale} {d.context}".lower()
                if any(w in text for w in query_words):
                    merged.append(
                        {
                            "repo": ctx.alias,
                            "id": d.id,
                            "title": d.title,
                            "status": d.status,
                            "decision": _decision_body(d),
                            "rationale": d.rationale,
                            "source": d.source,
                            "confidence": d.confidence,
                        }
                    )
    return {
        "mode": "search",
        "query": query,
        "workspace": True,
        "decisions": merged[:15],
        "_meta": _build_meta(),
    }


async def _why_health_dashboard(repo: str | None) -> dict:
    """Mode 1: no query — return the decision health dashboard."""
    from repowise.core.persistence.crud import get_decision_health_summary

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        health = await get_decision_health_summary(session, repository.id)

        stale = health["stale_decisions"]
        proposed = health["proposed_awaiting_review"]
        ungoverned = health["ungoverned_hotspots"]

        return {
            "mode": "health",
            "summary": (
                f"{health['summary'].get('active', 0)} active · "
                f"{health['summary'].get('stale', 0)} stale · "
                f"{len(proposed)} proposed · "
                f"{len(ungoverned)} ungoverned hotspots"
            ),
            "counts": health["summary"],
            "stale_decisions": [
                {
                    "id": d.id,
                    "title": d.title,
                    "staleness_score": d.staleness_score,
                    "affected_files": filter_path_list(
                        json.loads(d.affected_files_json), _get_exclude_spec(ctx.path)
                    )[:5],
                }
                for d in stale[:10]
            ],
            "proposed_awaiting_review": [
                {
                    "id": d.id,
                    "title": d.title,
                    "source": d.source,
                    "confidence": d.confidence,
                }
                for d in proposed[:10]
            ],
            "ungoverned_hotspots": ungoverned[:15],
            "conflicts": health.get("conflicts", [])[:10],
            "_meta": _build_meta(repository=repository),
        }


# --- Path-mode cap and projection -------------------------------------------
#
# Path mode used to return every governing record whole: on ``persist.py`` that
# was 15 records inlining 241 file paths between them, plus an origin story
# carrying full commit bodies — 81 854 chars, which the MCP host rejects
# outright (see ``_budget.budgeter``: over the cap is an isError, not a
# truncation). The mode that matters most, "what governs this file right before
# I edit it", hard-failed on exactly the bug-magnet files it exists for.
#
# So: rank, project, then enforce. The caps below are the projection; the
# budget pass after them is the guarantee, since no fixed cap can bound a
# response whose fields are free text.

#: Governing records kept, best-first. Past ~8 the tail is review-queue noise.
_MAX_PATH_DECISIONS = 8

#: Paths kept per record. The array answers "how wide is this decision", which
#: a head plus a total answers as well as 241 paths do.
_MAX_AFFECTED_FILES = 10

#: Commit *bodies* are the weight in an origin story: the subject is already
#: capped at 200 chars at ingest, but ``body`` is kept up to 1 KB per
#: significant commit (``git_indexer/file_history.py``) and up to 10 of those
#: ride along in ``key_commits``. An origin story reads the intent, not the
#: whole message.
_MAX_COMMIT_TEXT_CHARS = 320

#: Headroom left under the budget for ``OmissionCollector.attach``, which adds
#: ``omission_marker`` + ``_meta.omitted`` *after* the last size check.
_COLLECTOR_HEADROOM_CHARS = 600

#: Sort order for governing records: what governs beats what was proposed
#: beats what is retired; then confidence; then freshness.
_PATH_STATUS_ORDER = {"active": 0, "proposed": 1, "deprecated": 2, "superseded": 3}


def _path_decision_sort_key(d: Any) -> tuple[int, float, float]:
    return (
        _PATH_STATUS_ORDER.get(d.status, 4),
        -(d.confidence or 0.0),
        d.staleness_score or 0.0,
    )


def _governing_decision_entry(d: Any, affected_files: list, lineage: list[dict]) -> dict:
    """Serialize a decision that governs a path, including its lineage chain."""
    entry = {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "context": d.context,
        "decision": _decision_body(d),
        "rationale": d.rationale,
        "alternatives": json.loads(d.alternatives_json),
        "consequences": json.loads(d.consequences_json),
        "affected_files": affected_files[:_MAX_AFFECTED_FILES],
        "source": d.source,
        "confidence": d.confidence,
        "staleness_score": d.staleness_score,
        "lineage": lineage if len(lineage) > 1 else [],
    }
    if len(affected_files) > _MAX_AFFECTED_FILES:
        entry["affected_files_total"] = len(affected_files)
    return entry


def _trim_commit_text(origin_story: dict) -> None:
    """Cap commit prose in place, wherever the origin story inlines it.

    ``message`` and ``body`` both, because which one carries the weight depends
    on the repo: a squash-merge repo puts the whole rationale in ``body`` and
    the ingest keeps it up to 1 KB, while ``message`` is already capped at 200.
    Capping both means this does not quietly become a no-op if that changes.
    """

    def _trim(commits: Any) -> None:
        if not isinstance(commits, list):
            return
        for c in commits:
            if not isinstance(c, dict):
                continue
            for field in ("message", "body"):
                text = c.get(field)
                if isinstance(text, str) and len(text) > _MAX_COMMIT_TEXT_CHARS:
                    c[field] = text[:_MAX_COMMIT_TEXT_CHARS] + "…"

    _trim(origin_story.get("key_commits"))
    for linked in origin_story.get("linked_decisions") or []:
        if isinstance(linked, dict):
            _trim(linked.get("evidence_commits"))


def _fit_path_response(
    result_data: dict, repo_root: Any, collector: OmissionCollector | None = None
) -> dict:
    """Shrink a path response until it fits the transport budget.

    The projection above bounds the structured fields; this bounds the free
    text ones, which no fixed cap can — a single record's ``rationale`` is
    unbounded, and the ungoverned-file branch returns git archaeology instead
    of decisions and so is not capped by any of them.

    Stages, cheapest loss first:

    1. Drop ``origin_story.linked_decisions``, which re-inlines each record's
       title, rationale and matched commits — all of it already in
       ``decisions``.
    2. Drop governing records from the tail, all the way to none if it comes
       to that. They are sorted best-first, so the tail is review-queue noise,
       and an empty list plus a marker beats a rejected response.
    3. Drop the fallback blocks the ungoverned branch adds
       (``code_rationale``, then ``git_archaeology``), then ``origin_story``
       whole. What survives — mode, path, alignment, ``_meta`` — is bounded.

    Every drop goes to the omission store, so the agent gets a
    ``[repowise#<ref>]`` marker it can expand rather than a silently shortened
    response. Call after ``_meta`` is set: the collector writes into it.

    *collector* is the one a caller already started — the episode block caps
    long bodies and banks the overflow before this runs. It must be reused
    rather than joined by a second, because ``attach`` overwrites
    ``_meta.omitted`` with its own refs and the loser's markers would then
    point at content the response no longer advertises. It is also why the
    under-budget path still attaches: a response that fits can still carry a
    capped body whose remainder needs advertising.
    """
    # Reserved so the marker the collector appends after the last check cannot
    # itself push the response back over the host cap.
    budget = effective_char_budget() - _COLLECTOR_HEADROOM_CHARS

    def _over() -> bool:
        return len(json.dumps(result_data, separators=(",", ":"), default=str)) > budget

    if not _over():
        if collector is not None:
            collector.attach(result_data)
        return result_data

    if collector is None:
        collector = OmissionCollector("get_why", repo_root=repo_root)

    def _drop_block(key: str, container: dict) -> None:
        if _over() and container.get(key):
            collector.add(key, container.pop(key))
            result_data["truncated"] = True

    origin_story = result_data.get("origin_story")
    if isinstance(origin_story, dict):
        _drop_block("linked_decisions", origin_story)

    # Before the governing records, not after them: episodes are the newest
    # evidence kind here and must only ever spend slack. Dropping them later in
    # the sequence meant the decisions loop ran with the episode block still
    # inflating the response, and a governing record was evicted to make room
    # for an episode that then survived — measured, not theorised.
    _drop_block("episodes", result_data)

    decisions: list = result_data.get("decisions") or []
    while decisions and _over():
        dropped = decisions.pop()
        collector.add(f"dropped governing decision {dropped.get('title', '')}", dropped)
        result_data["truncated"] = True
        result_data.setdefault("dropped_decisions", []).append(dropped.get("id", ""))

    for key in ("code_rationale", "git_archaeology", "origin_story"):
        _drop_block(key, result_data)

    collector.attach(result_data)
    return result_data


async def _why_path(query: str, repo: str | None) -> dict:
    """Mode 2: query is a path — governing decisions, origin story, alignment."""
    ctx = await _resolve_repo_context(repo)
    if is_excluded(query, _get_exclude_spec(ctx.path)):
        return {"query": query, "error": f"'{query}' is excluded by exclude_patterns."}
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        res = await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repository.id,
            )
        )
        all_decisions = res.scalars().all()

        # Load git metadata for origin story
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
                GitMetadata.file_path == query,
            )
        )
        git_meta = git_res.scalar_one_or_none()

        # Pre-load all git metadata for cross-file search (used by fallback)
        all_git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git_meta = all_git_res.scalars().all()

        from repowise.core.persistence.decision_graph import build_lineage_chain

        matched = [
            d
            for d in all_decisions
            if query in json.loads(d.affected_files_json)
            or query in json.loads(d.affected_modules_json)
        ]
        # Rank before capping, so the 8 that survive are the 8 that govern —
        # not whichever 8 the table scan happened to yield first.
        matched.sort(key=_path_decision_sort_key)
        governing = []
        for rank, d in enumerate(matched[:_MAX_PATH_DECISIONS]):
            # Walk supersedes/refines back to roots so the answer is a
            # lineage chain (sessions → JWT → OAuth2), not a flat list.
            lineage = await build_lineage_chain(session, d.id)
            entry = _governing_decision_entry(d, json.loads(d.affected_files_json), lineage)
            # Ask git whether the top record still holds — and only the top
            # one. The query is ~60 ms, which is affordable once inside an MCP
            # call and is not affordable eight times; the record ranked first
            # is the one a reader acts on. Everything below it keeps the
            # stored proportion, which needed no subprocess to compute.
            if rank == 0:
                sentence = await asyncio.to_thread(
                    describe_decision_currency,
                    ctx.path,
                    created_at=d.created_at,
                    nodes=json.loads(d.affected_files_json or "[]"),
                )
                if sentence:
                    entry["still_true"] = sentence
            governing.append(entry)

        origin_story = _build_origin_story(query, git_meta, governing)
        _trim_commit_text(origin_story)

        result_data: dict[str, Any] = {
            "mode": "path",
            "path": query,
            "decisions": governing,
            "origin_story": origin_story,
            # Alignment is scored over every matching record, not just the
            # ones that survived the cap — it is a coverage number, and
            # capping its input would make a well-governed hotspot look thin.
            # It reads only status/staleness/title, so the cheap projection is
            # the whole of what it needs.
            "alignment": _compute_alignment(
                query,
                [
                    {
                        "title": d.title,
                        "status": d.status,
                        "staleness_score": d.staleness_score,
                    }
                    for d in matched
                ],
                all_decisions,
            ),
        }
        if len(matched) > _MAX_PATH_DECISIONS:
            result_data["decisions_total"] = len(matched)

        # --- Fallback: git archaeology when no decisions found ---
        if not governing:
            result_data["git_archaeology"] = await _git_archaeology_fallback(
                query,
                git_meta,
                all_git_meta,
                repository,
            )
            # Decisions and git history both silent → the "why" may live in a
            # code comment. Mine this file's rationale comments directly.
            rationale = _mine_rationale(ctx.path, [query], None)
            if rationale:
                result_data["code_rationale"] = rationale

        # Episodes are additive rather than a fallback, unlike the two blocks
        # above. A well-governed file still has a history, and "what happened
        # here, dated" is the question this mode is asked; gating it on the
        # absence of decisions would hide it exactly where there is most to say.
        episodes, pending = await asyncio.to_thread(episode_evidence, ctx.path, paths=[query])
        if episodes:
            result_data["episodes"] = episodes
        # Banked here, not in the thread above: the omission store is a
        # sqlite3 connection bound to its creating thread, and this collector
        # is finalised below on this one.
        collector = bank_overflow(pending, tool="get_why", repo_root=ctx.path)

        result_data["_meta"] = _build_meta(repository=repository)
        return _fit_path_response(result_data, ctx.path, collector=collector)


# Stop words removed before keyword matching for better signal.
_QUERY_STOP_WORDS = {
    "why",
    "was",
    "is",
    "the",
    "a",
    "an",
    "this",
    "that",
    "how",
    "what",
    "when",
    "where",
    "for",
    "to",
    "of",
    "in",
    "it",
    "be",
}


async def _load_target_git(
    session: Any, repository_id: Any, targets: list[str] | None
) -> dict[str, Any]:
    """Load per-target git metadata keyed by file path (only present ones)."""
    target_git: dict[str, Any] = {}
    if not targets:
        return target_git
    for t in targets:
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository_id,
                GitMetadata.file_path == t,
            )
        )
        meta = git_res.scalar_one_or_none()
        if meta:
            target_git[t] = meta
    return target_git


def _rank_keyword_matches(all_decisions: list, query: str, target_set: set[str]) -> list:
    """Score decisions by weighted keyword overlap and return the top 8."""
    query_words = set(query.lower().split()) - _QUERY_STOP_WORDS
    scored_decisions: list[tuple[float, Any]] = []
    for d in all_decisions:
        score = _score_decision(d, query_words, target_set)
        if score > 0:
            scored_decisions.append((score, d))
    scored_decisions.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in scored_decisions[:8]]


async def _semantic_decision_results(ctx: Any, query: str) -> list:
    """Semantic search of the page store, filtered to the decision: namespace.

    Empty on a keyless index: there is no lexical fallback here, and a window of
    arbitrary decisions is worse than none for a tool whose whole job is
    explaining why a specific thing is the way it is.
    """
    decision_results: list = []
    with contextlib.suppress(Exception):
        if ctx.vector_store is not None and store_has_semantic_vectors(ctx.vector_store):
            _raw = await ctx.vector_store.search(query, limit=50)
            decision_results = [
                r for r in _raw if getattr(r, "page_id", "").startswith(DECISION_VECTOR_PREFIX)
            ][:5]
    return decision_results


async def _semantic_doc_results(ctx: Any, query: str) -> list:
    """Semantic search over documentation, falling back to FTS.

    A keyless index takes the FTS path directly rather than going through a
    vector store that cannot rank, which is the same answer the ``except``
    branch already produces for an unusable store.
    """
    if not store_has_semantic_vectors(getattr(ctx, "vector_store", None)):
        doc_results: list = []
        with contextlib.suppress(Exception):
            doc_results = await ctx.fts.search(query, limit=3)
        return doc_results
    try:
        return await ctx.vector_store.search(query, limit=3)
    except Exception:
        doc_results = []
        with contextlib.suppress(Exception):
            doc_results = await ctx.fts.search(query, limit=3)
        return doc_results


async def _lineage_for_matches(ctx: Any, keyword_matches: list) -> dict[str, list[dict]]:
    """Walk lineage chains for the keyword matches; keep only multi-node chains."""
    from repowise.core.persistence.decision_graph import build_lineage_chain

    lineage_by_id: dict[str, list[dict]] = {}
    if keyword_matches:
        async with get_session(ctx.session_factory) as session3:
            for d in keyword_matches:
                chain = await build_lineage_chain(session3, d.id)
                if len(chain) > 1:
                    lineage_by_id[d.id] = chain
    return lineage_by_id


def _merge_decisions(
    keyword_matches: list,
    decision_results: list,
    lineage_by_id: dict[str, list[dict]],
) -> list[dict]:
    """Merge keyword and semantic decision hits, deduplicated by id."""
    seen_ids: set[str] = set()
    merged_decisions: list[dict] = []
    for d in keyword_matches:
        if d.id in seen_ids:
            continue
        seen_ids.add(d.id)
        merged_decisions.append(
            {
                "id": d.id,
                "title": d.title,
                "status": d.status,
                "decision": _decision_body(d),
                "rationale": d.rationale,
                "context": d.context,
                "consequences": json.loads(d.consequences_json),
                "affected_files": json.loads(d.affected_files_json),
                "source": d.source,
                "confidence": d.confidence,
                "lineage": lineage_by_id.get(d.id, []),
            }
        )

    for r in decision_results:
        # Strip the "decision:" prefix so the returned id matches the SQL primary key.
        real_id = r.page_id[len(DECISION_VECTOR_PREFIX) :]
        if real_id in seen_ids:
            continue
        seen_ids.add(real_id)
        merged_decisions.append(
            {
                "id": real_id,
                "title": r.title,
                "snippet": r.snippet,
                "relevance_score": r.score,
            }
        )
    return merged_decisions


async def _build_target_context(
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    """Per-target governing decisions + origin story, with archaeology fallback."""
    async with get_session(ctx.session_factory) as session2:
        # Load all git metadata for cross-file search
        all_git_res = await session2.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git_meta_list = all_git_res.scalars().all()

        target_context: dict[str, Any] = {}
        for t in targets:
            t_governing = []
            for d in all_decisions:
                affected = json.loads(d.affected_files_json)
                affected_mods = json.loads(d.affected_modules_json)
                if t in affected or any(t.startswith(m + "/") for m in affected_mods):
                    t_governing.append({"title": d.title, "status": d.status})
            git_m = target_git.get(t)
            ctx_entry: dict[str, Any] = {
                "governing_decisions": t_governing,
                "origin": _build_origin_story(t, git_m, t_governing)
                if git_m
                else {
                    "available": False,
                    "summary": f"No git history for {t}.",
                },
            }
            # Git archaeology fallback when no decisions found
            if not t_governing:
                ctx_entry["git_archaeology"] = await _git_archaeology_fallback(
                    t,
                    git_m,
                    all_git_meta_list,
                    repository,
                )
            target_context[t] = ctx_entry
        return target_context


async def _why_search(query: str, targets: list[str] | None, repo: str | None) -> dict:
    """Mode 3: natural-language, target-aware decision + documentation search."""
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        all_decisions = await _list_decisions(
            session, repository.id, include_proposed=True, limit=200
        )
        # Records anchored entirely in excluded paths (vendored venvs mined
        # before exclude rules changed) are noise for every mode downstream.
        _spec = _get_exclude_spec(ctx.path)
        all_decisions = [d for d in all_decisions if not decision_is_excluded(d, _spec)]
        # Load git metadata for targets (for origin context in results)
        target_git = await _load_target_git(session, repository.id, targets)

    target_set = set(targets) if targets else set()
    keyword_matches = _rank_keyword_matches(all_decisions, query, target_set)
    decision_results = await _semantic_decision_results(ctx, query)
    doc_results = await _semantic_doc_results(ctx, query)
    lineage_by_id = await _lineage_for_matches(ctx, keyword_matches)
    merged_decisions = _merge_decisions(keyword_matches, decision_results, lineage_by_id)

    result_data: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": merged_decisions[:8],
        "related_documentation": [
            {
                "page_id": r.page_id,
                "title": r.title,
                "page_type": r.page_type,
                "snippet": r.snippet,
                "relevance_score": r.score,
            }
            for r in doc_results[:3]
        ],
    }

    # If targets provided, include target context
    if targets:
        result_data["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets
        )
        # When the decision corpus is thin, the rationale for the anchored
        # files may be in their comments — mine them against the question.
        if not merged_decisions:
            rationale = _mine_rationale(ctx.path, targets, query)
            if rationale:
                result_data["code_rationale"] = rationale

    # Targets resolve through the node index; without them the question itself
    # is the only handle, so it is ranked against the bodies.
    episodes, pending = await asyncio.to_thread(
        episode_evidence,
        ctx.path,
        paths=targets or None,
        query=None if targets else query,
    )
    if episodes:
        result_data["episodes"] = episodes

    result_data["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    # Deliberately *not* routed through `_fit_path_response`. This mode has no
    # budget pass and predates this block, but that function is written for the
    # path response's shape: search mode keeps `origin_story` and
    # `git_archaeology` inside `target_context`, so the two blocks it would
    # drop whole are no-ops here and the only thing it can actually shed is
    # this mode's primary payload. Measured on a realistic six-target
    # response, it emptied `decisions` entirely and was still over budget —
    # strictly worse than leaving it alone. What this block adds is bounded by
    # construction (three episodes, each body capped), which is the obligation
    # it owes; giving the whole mode a budget pass is a separate change with
    # its own drop order to design.
    collector = bank_overflow(pending, tool="get_why", repo_root=ctx.path)
    if collector is not None:
        collector.attach(result_data)
    return result_data


def _score_decision(
    d: Any,
    query_words: set[str],
    target_files: set[str],
) -> float:
    """Score a decision against query words with field weighting and target boosting."""
    if not query_words:
        return 1.0 if target_files else 0.0

    # Build weighted text fields
    fields = [
        (3.0, d.title.lower()),
        (2.0, d.decision.lower()),
        (2.0, d.rationale.lower()),
        (1.5, d.context.lower()),
        (1.0, " ".join(json.loads(d.consequences_json)).lower()),
        (1.0, " ".join(json.loads(d.tags_json)).lower()),
        (1.5, " ".join(json.loads(d.affected_files_json)).lower()),
        (1.0, (d.evidence_file or "").lower()),
    ]

    score = 0.0
    for weight, text in fields:
        for word in query_words:
            if word in text:
                score += weight

    # Target file boosting: decisions governing target files get a bonus
    if target_files:
        affected = set(json.loads(d.affected_files_json))
        affected_mods = json.loads(d.affected_modules_json)
        for t in target_files:
            if t in affected:
                score += 5.0  # Strong boost for exact file match
            elif any(t.startswith(m + "/") for m in affected_mods):
                score += 3.0  # Module-level match

    return score


async def _git_archaeology_fallback(
    file_path: str,
    git_meta: Any | None,
    all_git_meta: list,
    repository: Any,
) -> dict:
    """When no decisions govern a file, mine git history for intent signals."""
    result: dict[str, Any] = {"triggered": True}

    # --- Layer 1: File's own significant commits ---
    file_commits = []
    if git_meta and git_meta.significant_commits_json:
        commits = json.loads(git_meta.significant_commits_json)
        file_commits = [
            {
                "sha": c.get("sha", ""),
                "message": c.get("message", ""),
                "author": c.get("author", ""),
                "date": c.get("date", ""),
            }
            for c in commits
        ]
    result["file_commits"] = file_commits[:10]  # Cap to keep response bounded

    # --- Layer 2: Cross-file search — other files' commits mentioning this file ---
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    # Convert snake_case/kebab to searchable terms: auth_cache_service -> {"auth", "cache", "service"}
    search_terms = set(re.split(r"[_\-/.]", stem.lower()))
    search_terms.discard("")
    # Also search for the full basename
    search_terms.add(basename.lower())

    cross_references = []
    for gm in all_git_meta:
        if gm.file_path == file_path:
            continue
        commits = json.loads(gm.significant_commits_json) if gm.significant_commits_json else []
        for c in commits:
            msg_lower = c.get("message", "").lower()
            # Match if the commit message mentions the file basename or 2+ stem terms
            matched_terms = [t for t in search_terms if t in msg_lower]
            if basename.lower() in msg_lower or len(matched_terms) >= 2:
                cross_references.append(
                    {
                        "source_file": gm.file_path,
                        "sha": c.get("sha", ""),
                        "message": c.get("message", ""),
                        "author": c.get("author", ""),
                        "date": c.get("date", ""),
                        "matched_terms": matched_terms,
                    }
                )
    # Deduplicate by SHA and sort by date descending
    seen_shas: set[str] = set()
    unique_refs = []
    for cr in cross_references:
        if cr["sha"] not in seen_shas:
            seen_shas.add(cr["sha"])
            unique_refs.append(cr)
    unique_refs.sort(key=lambda x: x.get("date", ""), reverse=True)
    result["cross_references"] = unique_refs[:10]

    # --- Layer 3: Live git log (when local repo exists) ---
    git_log_results = []
    local_path = getattr(repository, "local_path", None)
    if local_path and (Path(local_path) / ".git").is_dir():
        git_log_results = await _run_git_log(local_path, file_path, stem)
    result["git_log"] = git_log_results

    # --- Summary ---
    total = len(file_commits) + len(unique_refs) + len(git_log_results)
    if total > 0:
        result["summary"] = (
            f"No architectural decisions found for {file_path}, but git archaeology "
            f"recovered {len(file_commits)} direct commit(s), "
            f"{len(unique_refs)} cross-reference(s), and "
            f"{len(git_log_results)} git log result(s). "
            "Review these to understand the intent behind this code."
        )
    else:
        result["summary"] = (
            f"No architectural decisions or git history found for {file_path}. "
            "This file may be new or not yet indexed."
        )

    return result


async def _run_git_log(
    repo_path: str,
    file_path: str,
    stem: str,
) -> list[dict]:
    """Run git log against the local repo for deeper history. Best-effort."""
    import asyncio
    import subprocess

    def _sync_git_log() -> list[dict]:
        import re

        results: list[dict] = []
        # Sanitize stem to prevent argument injection via --grep
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-.]", "", stem) if stem else ""
        try:
            proc = subprocess.run(
                ["git", "log", "--follow", "--format=%H\t%an\t%ai\t%s", "-20", "--", file_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                # ``%s`` is the commit subject and ``%an`` the author name, both
                # utf-8 from git. text=True alone decodes with the locale codec,
                # which is cp1252 on a default Windows install.
                encoding="utf-8",
                errors="replace",
                timeout=10,
                # See commits_since() in core/precedent/currency.py: a git child
                # that inherits this server's JSON-RPC stdin can wedge the
                # session, and the timeout above is not a reliable ceiling.
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                for line in proc.stdout.strip().splitlines():
                    parts = line.split("\t", 3)
                    if len(parts) == 4:
                        results.append(
                            {
                                "sha": parts[0][:12],
                                "author": parts[1],
                                "date": parts[2][:10],
                                "message": parts[3],
                                "source": "git_log_follow",
                            }
                        )

            if safe_stem and len(safe_stem) >= 3:
                proc2 = subprocess.run(
                    [
                        "git",
                        "log",
                        "--all",
                        "--grep",
                        safe_stem,
                        "--format=%H\t%an\t%ai\t%s",
                        "-10",
                        "--",  # end of options — prevent argument injection
                    ],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",  # see above
                    errors="replace",
                    timeout=10,
                    stdin=subprocess.DEVNULL,  # see above
                )
                if proc2.returncode == 0:
                    seen = {r["sha"] for r in results}
                    for line in proc2.stdout.strip().splitlines():
                        parts = line.split("\t", 3)
                        if len(parts) == 4 and parts[0][:12] not in seen:
                            seen.add(parts[0][:12])
                            results.append(
                                {
                                    "sha": parts[0][:12],
                                    "author": parts[1],
                                    "date": parts[2][:10],
                                    "message": parts[3],
                                    "source": "git_log_grep",
                                }
                            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return results[:20]

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync_git_log), timeout=15)
    except TimeoutError:
        return []
