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
from repowise.server.mcp_server._why_relevance import (
    clears_floor,
    question_terms,
    redirect_for,
    relevance,
    term_idf,
)


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
        targets: optional file paths to anchor the search, or to ask about on
            their own when there is no query.
        repo: usually omitted.
    """
    # --- repo="all": search decisions across ALL repos ---
    if repo == "all":
        if not query:
            return _unsupported_repo_all("get_why (health dashboard)")
        return await _why_workspace_search(query)

    # --- Mode 1: No query → the targets, or the health dashboard ---
    # Targets first: a caller who named files and asked nothing has asked about
    # those files. Reaching the dashboard from here returned the same bytes for
    # every target and for no arguments at all, so the answer never mentioned
    # what was asked about.
    if not query:
        if targets:
            return await _why_targets(list(targets), repo)
        return await _why_health_dashboard(repo)

    # --- Mode 2: Path → decisions, origin story, alignment ---
    if _is_path(query):
        return await _why_path(query, repo)

    # --- Mode 3: Natural language → target-aware search ---
    return await _why_search(query, targets, repo)


async def _why_workspace_search(query: str) -> dict:
    """repo="all": the question single-repo search answers, across the workspace.

    This used to be the whole tool as it stood before the relevance work: match
    any query word as a substring, append in whatever order the workspace
    resolved its stores, serve fifteen whole records. That is the shape the
    single-repo mode was rebuilt away from, still reachable one argument away and
    over several stores at once. It now loads its corpus the way ``_load_corpus``
    does, so dismissed tombstones and records anchored entirely in excluded
    paths stop being served here alone, and it ranks with the shared machinery, so
    a question the workspace cannot answer gets the redirect rather than fifteen
    records that happen to contain "the".

    **Each store is scored against its own corpus, never a pooled one**, and the
    consequence is asymmetric. The floor keeps the meaning it was swept for:
    ``relevance`` is a normalised share of the question's weight, so it survives
    a uniform rescale of the idf vector untouched. What a pooled corpus moves is
    the *ratio* between term weights, and with it which records clear 0.6.
    Scoring each store on its own statistics keeps that filter the one that was
    calibrated. The merge is where the cost lands, and it is a real one: the cut
    below is decided across stores whose scores come from different
    distributions, since a large store polarises toward 0 and 1 while a small one
    lands mid-range. So which store loses a slot is settled approximately, and
    that is a *selection*, not merely an order. It is the price of not silently
    re-scaling a constant nobody re-swept. The upgrade path, if workspace
    ranking ever needs to be exact, is to sweep a floor against a pooled corpus,
    not to pool the statistics underneath the floor that exists.
    """
    contexts = await _resolve_all_contexts()
    scored: list[tuple[tuple[float, float, int], str, str, Any, list[str]]] = []
    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            records = await _decision_corpus(session, repository.id, _get_exclude_spec(ctx.path))
        ranked = _score_keyword_matches(records, query, set())
        # Collapsed per store, not across the merge: ``_evidence_key`` is a
        # (source, commit) pair carrying no repo, so two stores sharing a commit
        # sha would fold into one and a repo would lose its record.
        key_by_id = {d.id: key for key, d in ranked}
        for d, folded in _collapse_restatements([d for _, d in ranked]):
            scored.append((key_by_id[d.id], ctx.alias, d.id, d, folded))

    if not scored:
        return {
            "mode": "search",
            "query": query,
            "workspace": True,
            "decisions": [],
            **redirect_for(query),
            "_meta": _build_meta(),
        }

    # The store's own key first, so relevance, occurrence count and status decide
    # as they do within one repo; alias and id only settle what those three leave
    # equal, and are here so the answer is the same on two runs.
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    decisions: list[dict] = []
    for _, alias, _id, d, folded in scored[:_MAX_WORKSPACE_DECISIONS]:
        entry = {
            "repo": alias,
            "id": d.id,
            "title": d.title,
            "status": d.status,
            "decision": _decision_body(d),
            "rationale": d.rationale,
            "source": d.source,
            "confidence": d.confidence,
        }
        if folded:
            entry["restates"] = folded
        decisions.append(entry)
    return {
        "mode": "search",
        "query": query,
        "workspace": True,
        "decisions": decisions,
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
                for d in stale[:_MAX_HEALTH_STALE]
            ],
            "proposed_awaiting_review": [
                {
                    "id": d.id,
                    "title": d.title,
                    "source": d.source,
                    "confidence": d.confidence,
                }
                for d in proposed[:_MAX_HEALTH_PROPOSED]
            ],
            "ungoverned_hotspots": ungoverned[:_MAX_HEALTH_UNGOVERNED],
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


# --- Search-mode caps -------------------------------------------------------
#
# Search mode had no caps at all: it served eight whole records with their file
# arrays inlined, measuring 27 640 - 34 917 chars over five probe questions
# against a 32 000 budget, so two of the five went over the ceiling outright.
# The cost was not only transport. On a question the store does answer ("why
# ruff check and not ruff format", held by two active records) it returned eight
# records, none of them those, three restating one unrelated decision. A long
# low-relevance answer teaches an agent that the tool is not worth calling,
# which is more expensive than a miss.
#
# So this mode gets few records served whole rather than many served thin: a
# padded answer is the failure mode, and thinning every record to keep eight of
# them is padding with extra steps.

#: Records kept by search mode. Three whole beats eight thinned: past the third
#: hit the ranking is not trustworthy enough to spend an agent's context on.
_MAX_SEARCH_DECISIONS = 3

#: Records kept by workspace search. Above the single-repo three because the
#: answer can genuinely live in more than one store and the repo is part of it;
#: nowhere near the fifteen whole records this path served before it was ranked.
_MAX_WORKSPACE_DECISIONS = 5

#: Nearest pages pulled for the *one* semantic lookup search mode makes. Both
#: lanes (decisions, documentation) are partitioned out of this single window,
#: so the depth is the old decision lane's rather than the sum of the two.
_SEMANTIC_WINDOW = 50

#: Records search mode ranks over. It used to be 200, against a store holding
#: 614: ``list_decisions`` sorts confirmed-then-confident, so the 414 records
#: below the cut were unreachable by any question, and the cap was a silent
#: recall ceiling rather than a cost control. It is not a cost control either —
#: ranking is a substring scan over short fields, measured at 2 ms for 182
#: records here, so the whole store costs single-digit milliseconds. Kept
#: bounded only so an unattended extractor cannot turn one call into an
#: unbounded scan.
_DECISION_CORPUS_LIMIT = 2000

#: Keyword candidates scored before restatements are collapsed. Wider than the
#: serving cap on purpose: the worst cluster here is fourteen phrasings of one
#: decision, so a pool cut to three first would serve one decision three times.
#: Bounded because the lineage walk after the collapse costs a query per record.
_KEYWORD_POOL = 24

#: Items per list in the health dashboard. This mode is an orientation call:
#: asked once, skimmed, acted on twice. It served 45 items to be read as a
#: verdict. Halved now that ``get_decision_health_summary`` ranks what it
#: returns: cutting an unranked list only makes a list nobody reads shorter,
#: cutting a ranked one keeps the part that is worth reading. The full sizes stay
#: legible in ``counts`` and in the summary line, so nothing is silently dropped.
_MAX_HEALTH_STALE = 5
_MAX_HEALTH_PROPOSED = 5
_MAX_HEALTH_UNGOVERNED = 8


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


def _governs_any(d: Any, targets: set[str]) -> bool:
    """Whether *d* names any of *targets* among its files or modules."""
    if not targets:
        return False
    affected = set(json.loads(d.affected_files_json))
    modules = json.loads(d.affected_modules_json)
    return any(t in affected or any(t.startswith(m + "/") for m in modules) for t in targets)


def _score_keyword_matches(
    all_decisions: list, query: str, target_set: set[str]
) -> list[tuple[tuple[float, float, int], Any]]:
    """``_rank_keyword_matches`` with each record's whole sort key kept beside it.

    Split out for workspace search, which ranks one store at a time and then has
    to merge the survivors. It is the *whole* key rather than the score because a
    merge that kept only the score would silently drop the other two rules: the
    occurrence-count tie-break and the status tie-break both exist because
    ``relevance`` is a share of one idf vector, so any two records covering the
    same set of question terms score bit-identically and something has to
    separate them. The floor and the ordering rules are documented on
    ``_rank_keyword_matches``; this returns the key that implements them.
    """
    terms = question_terms(query)
    texts = {id(d): _record_text(d) for d in all_decisions}
    idf = term_idf(terms, list(texts.values()))

    scored_decisions: list[tuple[float, float, int, Any]] = []
    for d in all_decisions:
        # A record governing a file the caller named is relevant by
        # construction: they pointed at it instead of describing it, so it owes
        # the question no vocabulary.
        governs = _governs_any(d, target_set)
        score = 1.0 if governs else relevance(texts[id(d)], idf)
        if not clears_floor(score):
            continue
        scored_decisions.append(
            (
                -score,
                -_score_decision(d, set(terms), target_set),
                _PATH_STATUS_ORDER.get(d.status, 4),
                d,
            )
        )
    scored_decisions.sort(key=lambda t: (t[0], t[1], t[2]))
    return [((t[0], t[1], t[2]), t[3]) for t in scored_decisions[:_KEYWORD_POOL]]


def _rank_keyword_matches(all_decisions: list, query: str, target_set: set[str]) -> list:
    """Records relevant enough to serve, best-first. Empty when none are.

    Ranks by how much of the question's *vocabulary* a record carries, rarer
    words weighing more, with the older occurrence count breaking ties and
    status breaking the ties after that. Ordering by the occurrence count alone
    scored by length and by how ordinary a word is: measured here, "why do we
    use ruff check instead of ruff format" was won by records matching "use",
    "check", "format" and "instead" while the two ``active`` records that answer
    it — one of them 48 characters long — did not place at all.

    Status stays a tie-break and never a gate. Ordering by it *first* was tried
    and measured worse, and the reason generalises: only 69 of this repo's 614
    records are active, so a hard status gate serves three weakly-matching
    confirmed records ahead of the only relevant ones. The four records that
    answer "why is entry-point candidacy decided at ingestion" are all proposed,
    and status-first ordering made them unreachable.

    Records below the floor are dropped rather than ranked last, so an empty
    return is the honest answer and the caller turns it into a redirect. The
    surviving pool is wider than the serving cap because restatements are
    collapsed downstream, and a pool cut to the cap first would let three
    phrasings of one decision fill every slot.
    """
    return [d for _, d in _score_keyword_matches(all_decisions, query, target_set)]


async def _fts_doc_results(ctx: Any, query: str) -> list:
    """Documentation hits from the lexical index.

    The keyless path, and the fallback whenever the vector store is present but
    unusable: a store that cannot rank gives the same answer as no store.
    """
    doc_results: list = []
    with contextlib.suppress(Exception):
        doc_results = await ctx.fts.search(query, limit=3)
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
    return decision_hits[:_MAX_SEARCH_DECISIONS], doc_hits[:3]


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


def _evidence_key(d: Any) -> tuple[str, str] | None:
    """The evidence a record cites, as a merge key, or ``None`` when it cites none.

    Re-extraction paraphrases a record's prose but not its provenance. The
    fourteen records on this repo that all restate one LIKE-escaping decision
    carry fourteen titles and fourteen ids and the same single
    ``evidence_commits`` entry; comment-sourced restatements repeat an
    ``evidence_file`` instead. Store-wide, 478 of 614 records sit in a cluster
    like that, which is why one query could return five phrasings of one
    decision.

    So the key is the cited evidence plus the extractor that read it, never the
    text. Normalising titles would need a tuned similarity threshold, and a
    wrong merge there hides a human-confirmed record. Keeping ``source`` in the
    key also means two extractors that independently found the same commit stay
    separate, which is the provenance-accretion case ``decision_evidence``
    already models.
    """
    commits = json.loads(getattr(d, "evidence_commits_json", None) or "[]")
    if commits:
        return (d.source or "", f"commit:{commits[0]}")
    if d.evidence_file:
        return (d.source or "", f"file:{d.evidence_file}")
    return None


def _collapse_restatements(records: list) -> list[tuple[Any, list[str]]]:
    """``(kept, folded_ids)`` per distinct decision, input order preserved.

    Runs on records rather than on the projected dicts so the collapse happens
    *before* the lineage walk, which costs a query per surviving record. Records
    citing no evidence at all cannot be compared this way and are always kept.
    """
    by_evidence: dict[tuple[str, str], int] = {}
    out: list[tuple[Any, list[str]]] = []
    for d in records:
        key = _evidence_key(d)
        if key is not None and key in by_evidence:
            out[by_evidence[key]][1].append(d.id)
            continue
        if key is not None:
            by_evidence[key] = len(out)
        out.append((d, []))
    return out


def _merge_decisions(
    keyword_matches: list[tuple[Any, list[str]]],
    decision_results: list,
    lineage_by_id: dict[str, list[dict]],
) -> list[dict]:
    """Project collapsed keyword hits, then append semantic hits not already in.

    *keyword_matches* arrives from :func:`_collapse_restatements`, so the folded
    ids ride along on ``restates``: nothing becomes unaddressable and the store
    is untouched.
    """
    seen_ids: set[str] = set()
    merged_decisions: list[dict] = []
    for d, folded in keyword_matches:
        if d.id in seen_ids:
            continue
        seen_ids.update([d.id, *folded])
        affected_files = json.loads(d.affected_files_json)
        entry = {
            "id": d.id,
            "title": d.title,
            "status": d.status,
            "decision": _decision_body(d),
            "rationale": d.rationale,
            "context": d.context,
            "consequences": json.loads(d.consequences_json),
            # Whole arrays reached 83 paths and 4 812 chars, 36% of the payload
            # across the eight records served. A head plus a total answers "how
            # wide is this decision" as well, and path mode already says so.
            "affected_files": affected_files[:_MAX_AFFECTED_FILES],
            "source": d.source,
            "confidence": d.confidence,
            "lineage": lineage_by_id.get(d.id, []),
        }
        if len(affected_files) > _MAX_AFFECTED_FILES:
            entry["affected_files_total"] = len(affected_files)
        if folded:
            entry["restates"] = folded
        merged_decisions.append(entry)

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


async def _why_no_match(
    query: str,
    targets: list[str] | None,
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
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
    if targets:
        result["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets
        )
        rationale = _mine_rationale(ctx.path, targets, query)
        if rationale:
            result["code_rationale"] = rationale
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    return result


async def _decision_corpus(session: Any, repository_id: str, exclude_spec: Any) -> list:
    """The rankable decision records of one repository.

    A record anchored entirely in excluded paths is noise for every mode, and a
    dismissed one is a tombstone, so both filters belong wherever a corpus is
    built. Takes a session rather than a context so the caller that also needs a
    repository row and git metadata still opens one. Shared with workspace search
    precisely because that path used to build its own corpus and got neither
    filter.
    """
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    records = await _list_decisions(
        session, repository_id, include_proposed=True, limit=_DECISION_CORPUS_LIMIT
    )
    return [d for d in records if not decision_is_excluded(d, exclude_spec)]


async def _load_corpus(repo: str | None, targets: list[str] | None) -> tuple:
    """Repo context, the rankable decision corpus, and git metadata for targets.

    The prologue both target-aware modes open with. Shared so the corpus is
    filtered once: a record anchored entirely in excluded paths is noise for
    every mode downstream, and a mode that skipped the filter would answer from
    a different store than its neighbour.
    """
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        all_decisions = await _decision_corpus(session, repository.id, _get_exclude_spec(ctx.path))
        # Load git metadata for targets (for origin context in results)
        target_git = await _load_target_git(session, repository.id, targets)
    return ctx, repository, all_decisions, target_git


async def _why_targets(targets: list[str], repo: str | None) -> dict:
    """Mode 2b: targets and no query — the paths themselves are the question.

    One target is path mode outright: its lineage walk, alignment score and
    origin story are the fullest answer this tool has about a file, and that
    content is why the mode exists. Several get the per-target card instead —
    the same evidence a target already earns in search mode — because running
    path mode once per target would mean a corpus scan and a currency
    subprocess each, for a shape no caller renders.
    """
    if len(targets) == 1:
        return await _why_path(targets[0], repo)

    ctx, repository, all_decisions, target_git = await _load_corpus(repo, targets)
    return {
        "mode": "path",
        "paths": targets,
        "target_context": await _build_target_context(
            ctx, repository, all_decisions, target_git, targets
        ),
        "_meta": _build_meta(repository=repository, targets=targets),
    }


async def _why_search(query: str, targets: list[str] | None, repo: str | None) -> dict:
    """Mode 3: natural-language, target-aware decision + documentation search."""
    ctx, repository, all_decisions, target_git = await _load_corpus(repo, targets)

    target_set = set(targets) if targets else set()
    # Rank wide, collapse restatements, then cap — so the cap spends its slots on
    # distinct decisions — and only walk lineage for what survives.
    ranked = _rank_keyword_matches(all_decisions, query, target_set)
    if not ranked:
        return await _why_no_match(
            query, targets, ctx, repository, all_decisions, target_git
        )
    collapsed = _collapse_restatements(ranked)[:_MAX_SEARCH_DECISIONS]
    decision_results, doc_results = await _semantic_lanes(ctx, query)
    lineage_by_id = await _lineage_for_matches(ctx, [d for d, _ in collapsed])
    merged_decisions = _merge_decisions(collapsed, decision_results, lineage_by_id)

    # No further slice: the cap is on *bodies*, applied to ``collapsed`` above.
    # Semantic hits append as id-plus-snippet at roughly 200 chars each, so
    # dropping them to fit a record count would cost the lane that carries a
    # calibrated relevance score for the sake of no measurable payload.
    result_data: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": merged_decisions,
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
        # The comment-mining fallback that used to sit here was gated on
        # ``not merged_decisions``, which nothing ever reached. It now lives in
        # ``_why_no_match``, behind the floor — the condition it always meant.

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


def _weighted_fields(d: Any) -> list[tuple[float, str]]:
    """The searchable text of a record, lowercased, by field weight.

    ``affected_files`` is deliberately absent. Joining a record's whole path
    list into one haystack and substring-matching a question against it scores
    by *breadth*: the record here governing 83 files matched almost every
    question asked, because ordinary query words ("index", "page", "format")
    occur somewhere in 83 paths, and it became the top hit for three of five
    probe questions including one about ruff. A scope is not question text.
    The legitimate use of that field — does this record govern the file the
    caller named — is the exact set membership in :func:`_governs_any` and in
    the target boost inside :func:`_score_decision`.
    """
    return [
        (3.0, d.title.lower()),
        (2.0, d.decision.lower()),
        (2.0, d.rationale.lower()),
        (1.5, d.context.lower()),
        (1.0, " ".join(json.loads(d.consequences_json)).lower()),
        (1.0, " ".join(json.loads(d.tags_json)).lower()),
        (1.0, (d.evidence_file or "").lower()),
    ]


def _record_text(d: Any) -> str:
    """The same text, unweighted, for term coverage and the relevance floor.

    Built from :func:`_weighted_fields` so a record cannot clear the floor on a
    field the tie-break cannot see, or the reverse.
    """
    return " ".join(text for _, text in _weighted_fields(d))


def _score_decision(
    d: Any,
    query_words: set[str],
    target_files: set[str],
) -> float:
    """Score a decision against query words with field weighting and target boosting."""
    if not query_words:
        return 1.0 if target_files else 0.0

    score = 0.0
    for weight, text in _weighted_fields(d):
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
