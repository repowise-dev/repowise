"""Workspace mode (``repo="all"``): ranked decision search across every store."""

from __future__ import annotations

from typing import Any

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    cap_collection,
)
from repowise.server.mcp_server._helpers import (
    _decision_body,
    _get_exclude_spec,
    _get_repo,
    _resolve_all_contexts,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._why_evidence import (
    annotate_response_evidence_async,
)
from repowise.server.mcp_server._why_relevance import (
    redirect_for,
)
from repowise.server.mcp_server.tool_why.caps import _MAX_WORKSPACE_DECISIONS
from repowise.server.mcp_server.tool_why.loading import _attach_decision_evidence, _decision_corpus
from repowise.server.mcp_server.tool_why.ranking import (
    _collapse_restatements,
    _score_keyword_matches,
)


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
    scored = await _score_workspace(query)
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
    scored.sort(key=lambda t: (t[0], t[1], t[3]))
    selected = scored
    collector = OmissionCollector(
        "get_why", repo_root=selected[0][2].path if selected else None
    )
    for selected_ctx in {entry[2].alias: entry[2] for entry in selected}.values():
        selected_records = [entry[4] for entry in selected if entry[2] is selected_ctx]
        async with get_session(selected_ctx.session_factory) as session:
            await _attach_decision_evidence(session, selected_records)
    decisions: list[dict] = []
    entries_by_alias: dict[str, list[dict[str, Any]]] = {}
    records_by_alias: dict[str, list[Any]] = {}
    contexts_by_alias = {entry[1]: entry[2] for entry in selected}
    for _, alias, _selected_ctx, _id, d, folded in selected:
        entry = _workspace_entry(alias, d, folded)
        entries_by_alias.setdefault(alias, []).append(entry)
        records_by_alias.setdefault(alias, []).append(d)
        decisions.append(entry)
    for alias, entries in entries_by_alias.items():
        await annotate_response_evidence_async(
            {"decisions": entries},
            alias,
            records_by_alias[alias],
            repo_root=contexts_by_alias[alias].path,
        )
    result = {
        "mode": "search",
        "query": query,
        "workspace": True,
        "decisions": decisions,
        "_meta": _build_meta(),
    }
    cap_collection(
        result,
        "decisions",
        decisions,
        _MAX_WORKSPACE_DECISIONS,
        collector,
        label=f"workspace decisions beyond cap={_MAX_WORKSPACE_DECISIONS}",
    )
    collector.attach(result)
    return result


async def _score_workspace(
    query: str,
) -> list[tuple[tuple[float, float, int], str, Any, str, Any, list[str]]]:
    """Every store's relevant records, collapsed per store, with their sort keys."""
    contexts = await _resolve_all_contexts()
    scored: list[tuple[tuple[float, float, int], str, Any, str, Any, list[str]]] = []
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
            scored.append((key_by_id[d.id], ctx.alias, ctx, d.id, d, folded))
    return scored


def _workspace_entry(alias: str, d: Any, folded: list[str]) -> dict[str, Any]:
    """One workspace search row, naming the store it came from."""
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
    return entry
