"""/api/repos/{repo_id}/overview-summary — one-call repo overview payload.

Replaces the dashboard's N-call waterfall with a single lightweight
aggregate: repo meta, stat strip (+ deltas vs the previous health
snapshot), server-built attention items, language distribution (from
graph_nodes, NOT a full graph export), top-hotspots slice, recent
decisions slice, savings headline, and health KPIs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.forges import read_remote_url, strip_credentials
from repowise.core.persistence import crud
from repowise.core.persistence.models import GitMetadata
from repowise.server.deps import cross_repo_cut_inputs, get_db_session, verify_api_key
from repowise.server.routers.git import _hotspot_from_row
from repowise.server.services import overview_payload as sections
from repowise.server.services.overview_counts import explore_counts

router = APIRouter(
    prefix="/api/repos",
    tags=["overview"],
    dependencies=[Depends(verify_api_key)],
)

#: How many snapshots the health card's trend line plots. Also the width of the
#: scalar window this route reads, so the read and the chart cannot drift.
HEALTH_HISTORY_POINTS = 12


def _remote_url(stored_url: str | None, local_path: str | None) -> str | None:
    """Best-effort git remote for a repo, with any credentials removed.

    ``repositories.url`` is empty when the last sync found no remote, so fall
    back to reading one added since out of ``.git/config``. Parsed rather than
    shelled out to: this runs on a page load, and ``git remote
    get-url`` would cost a process spawn per request for a string sitting in a
    file we can read.

    Used only to resolve a repo avatar, so every failure path returns ``None``
    and the UI falls back to initials. The value is sent to the browser, so it
    passes through ``strip_credentials`` first.
    """
    url = stored_url or (read_remote_url(local_path) if local_path else None)
    return strip_credentials(url) if url else None


async def _top_hotspots(session: AsyncSession, repo_id: str) -> list[dict[str, Any]]:
    # Here rather than in the service: rows are shaped by the git router's helper.
    rows = (
        (
            await session.execute(
                select(GitMetadata)
                .where(GitMetadata.repository_id == repo_id, GitMetadata.is_hotspot.is_(True))
                .order_by(
                    GitMetadata.temporal_hotspot_score.desc().nulls_last(),
                    GitMetadata.churn_percentile.desc(),
                )
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    return [_hotspot_from_row(r).model_dump(mode="json") for r in rows]


@router.get("/{repo_id}/overview-summary")
async def overview_summary(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Everything the Overview page needs above the fold, in one call."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    stats = await sections.stat_strip(session, repo_id)
    mix = await sections.language_mix(session, repo_id)
    # Loaded once and shared by the KPI rollup, the defect-accuracy stat and
    # ``total_nloc``.
    health_metrics = await crud.get_health_metrics(session, repo_id)
    # Three scopes, three reads. This payload wants the *count* of retained
    # snapshots, the newest ``HEALTH_HISTORY_POINTS`` rows' scalars for the
    # sparkline, and the newest two rows' per-file maps for the file-count
    # delta. Loading the history as entities pulled every row's
    # ``per_file_scores_json`` (MBs per page load); capping the read at two rows
    # would flatten the sparkline and report a snapshot count of 2.
    snapshot = await crud.get_health_snapshot_headline(
        session, repo_id, recent=HEALTH_HISTORY_POINTS
    )

    # The same indexed commit as /api/repos and /health/overview: state.json
    # and the row reconciled, the row's own value when the two diverge.
    from repowise.server.mcp_server._meta import resolve_indexed_commit

    indexed_commit = resolve_indexed_commit(repo.head_commit, repo.local_path) or repo.head_commit

    return {
        "repo": {
            "id": repo.id,
            "name": repo.name,
            "local_path": repo.local_path,
            "default_branch": repo.default_branch,
            "head_commit": indexed_commit,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
            "remote_url": _remote_url(repo.url, repo.local_path),
        },
        "stats": {
            **stats,
            "silo_count": await sections.module_silo_count(session, repo_id),
            "module_count": mix["module_count"],
            # Summed from the health metrics already loaded, so it costs
            # nothing. Overview's only use for the whole Stats "By the Numbers"
            # endpoint was this one figure, and that endpoint runs its own
            # commit-history pass. Matches the field hosted's backend serves.
            "total_nloc": sum(int(m.nloc or 0) for m in health_metrics),
            "deltas": await sections.snapshot_deltas(session, repo_id, snapshot),
        },
        "health": await sections.health_block(session, repo_id, health_metrics, snapshot, repo),
        "languages": mix["languages"],
        "docs_config_languages": mix["docs_config_languages"],
        **await sections.attention_block(
            session, repo_id, cross_repo_cut_inputs(request, repo_id)
        ),
        "top_hotspots": await _top_hotspots(session, repo_id),
        "recent_decisions": await sections.recent_decisions(session, repo_id),
        "savings": await sections.savings_block(repo.local_path),
        # The figures the explore cards and inventory share with their pages.
        "explore": await explore_counts(session, repo_id),
        "sync": await sections.sync_block(
            session, repo_id, repo, page_count=stats["doc_page_count"]
        ),
    }
