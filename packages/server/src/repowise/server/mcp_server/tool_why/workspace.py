"""Workspace mode (``repo="all"``): ranked decision search across every store."""

from __future__ import annotations

from typing import Any

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    cap_collection,
)
from repowise.server.mcp_server._federation import Federation, merge_ranked
from repowise.server.mcp_server._helpers import (
    _decision_body,
    _get_exclude_spec,
    _get_repo,
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
    fed = Federation()
    answered = await fed.run(lambda ctx: _score_repo(ctx, query))
    # The store's own key first; alias and id settle ties, for a stable order.
    selected = merge_ranked((rows for _, rows in answered), key=lambda t: (t[0], t[1], t[2]))
    if not selected:
        return fed.annotate(
            {
                "mode": "search",
                "query": query,
                "workspace": True,
                "decisions": [],
                **redirect_for(query),
                "_meta": _build_meta(),
            }
        )

    paths = {ctx.alias: ctx.path for ctx, _ in answered}
    collector = OmissionCollector("get_why", repo_root=paths[selected[0][1]])
    decisions = [entry for *_, entry in selected]
    result = {
        "mode": "search",
        "query": query,
        "workspace": True,
        "decisions": decisions,
        "_meta": _build_meta(),
    }
    fed.annotate(result, decisions)
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


async def _score_repo(
    ctx: Any, query: str
) -> list[tuple[tuple[float, float, int], str, str, dict[str, Any]]]:
    """One store's relevant records, collapsed, as ``(key, alias, id, entry)`` best first."""
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        records = await _decision_corpus(session, repository.id, _get_exclude_spec(ctx.path))
        ranked = _score_keyword_matches(records, query, set())
        # Collapsed per store: ``_evidence_key`` carries no repo, so stores
        # sharing a commit would fold into one across the merge.
        key_by_id = {d.id: key for key, d in ranked}
        collapsed = sorted(
            _collapse_restatements([d for _, d in ranked]),
            key=lambda pair: (key_by_id[pair[0].id], pair[0].id),
        )
        kept = [d for d, _ in collapsed]
        await _attach_decision_evidence(session, kept)
    entries = [_workspace_entry(ctx.alias, d, folded) for d, folded in collapsed]
    if entries:
        await annotate_response_evidence_async(
            {"decisions": entries}, ctx.alias, kept, repo_root=ctx.path
        )
    return [
        (key_by_id[d.id], ctx.alias, d.id, entry)
        for (d, _), entry in zip(collapsed, entries, strict=True)
    ]


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
