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

    Same corpus filters and ranking as single-repo search, so an unanswerable
    question gets the redirect.

    Each store is scored against its own corpus, never a pooled one: pooling
    would change term-weight ratios and so which records clear the calibrated
    floor. The cost is at the merge, where scores from different distributions
    decide which store loses a slot, approximately. If that ever needs to be
    exact, sweep a floor against a pooled corpus rather than pool under this one.
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

    # The store's own key first; alias and id settle ties, for a stable order.
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
        # Collapsed per store: ``_evidence_key`` carries no repo, so stores
        # sharing a commit would fold into one across the merge.
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
