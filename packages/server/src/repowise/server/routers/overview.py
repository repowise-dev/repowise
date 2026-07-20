"""/api/repos/{repo_id}/overview-summary — one-call repo overview payload.

Replaces the dashboard's N-call waterfall with a single lightweight
aggregate: repo meta, stat strip (+ deltas vs the previous health
snapshot), server-built attention items, language distribution (from
graph_nodes, NOT a full graph export), top-hotspots slice, recent
decisions slice, savings headline, and health KPIs.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GenerationJob,
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers.git import _hotspot_from_row
from repowise.server.services.knowledge_map import compute_knowledge_map

router = APIRouter(
    prefix="/api/repos",
    tags=["overview"],
    dependencies=[Depends(verify_api_key)],
)


def _index_storage_bytes(repowise_dir: Path) -> int:
    """Total on-disk size of a repo's ``.repowise/`` directory."""
    if not repowise_dir.is_dir():
        return 0
    total = 0
    for path in repowise_dir.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


def _decision_slim(d: Any) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "source": d.source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "staleness_score": round(float(d.staleness_score or 0.0), 3),
    }


async def _savings_headline(repo_local_path: str | None) -> dict:
    """Distill + MCP savings totals from the omission-store sidecar.

    Headline numbers only — no per-day rollups, no transcript scan (the
    missed-savings scan reads agent transcripts and is too slow for an
    overview payload). Mirrors /distill-savings semantics otherwise.
    """
    if not repo_local_path:
        return {"available": False}
    db_path = Path(repo_local_path) / ".repowise" / "omissions" / "omissions.db"
    if not db_path.is_file():
        return {"available": False}

    from repowise.core.distill import tracking
    from repowise.core.distill.session_model import resolve_session_model
    from repowise.core.generation.cost_tracker import get_model_pricing

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return {"available": False}
    try:
        summary = tracking.distill_summary(conn, since=None)
        mcp = tracking.mcp_savings_summary(conn, since=None)
    except sqlite3.Error:
        return {"available": False}
    finally:
        conn.close()

    resolved = resolve_session_model(Path(repo_local_path))
    rate = get_model_pricing(resolved.model)["input"]
    total_saved = summary["saved_tokens"] + mcp["tokens"]
    return {
        "available": True,
        "saved_tokens": summary["saved_tokens"],
        "mcp_tokens": mcp["tokens"],
        "total_saved_tokens": total_saved,
        "estimated_usd_saved": total_saved * rate / 1_000_000,
        "pricing_model": resolved.model,
    }


def _build_attention_items(
    decision_health: dict,
    knowledge_silos: list[dict],
    dead_safe: list[Any],
) -> list[dict]:
    """Flat, severity-tagged attention list — the server-side twin of the
    AttentionPanel item builder that used to live in the overview page."""
    items: list[dict] = []
    for d in decision_health.get("stale_decisions", []):
        items.append(
            {
                "id": f"stale-{d.id}",
                "type": "stale_decision",
                "title": d.title,
                "description": "Active decision drifting from the code it governs",
                "severity": "high",
                "target_id": d.id,
            }
        )
    for d in decision_health.get("proposed_awaiting_review", []):
        items.append(
            {
                "id": f"proposed-{d.id}",
                "type": "proposed_decision",
                "title": d.title,
                "description": "Auto-proposed decision awaiting review",
                "severity": "medium",
                "target_id": d.id,
            }
        )
    for fp in decision_health.get("ungoverned_hotspots", [])[:10]:
        items.append(
            {
                "id": f"ungoverned-{fp}",
                "type": "ungoverned_hotspot",
                "title": fp,
                "description": "High-churn file with no governing decision",
                "severity": "medium",
                "target_id": fp,
            }
        )
    for s in knowledge_silos[:10]:
        items.append(
            {
                "id": f"silo-{s['file_path']}",
                "type": "knowledge_silo",
                "title": s["file_path"],
                "description": (f"{round(s['owner_pct'] * 100)}% single-owner concentration"),
                "severity": "medium",
                "target_id": s["file_path"],
            }
        )
    for f in dead_safe[:10]:
        label = f.symbol_name or f.file_path
        items.append(
            {
                "id": f"dead-{f.id}",
                "type": "dead_code",
                "title": label,
                "description": f"Safe to delete ({f.lines} lines)",
                "severity": "low",
                "target_id": f.file_path,
            }
        )
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: severity_rank.get(i["severity"], 3))
    return items


@router.get("/{repo_id}/overview-summary")
async def overview_summary(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Everything the Overview page needs above the fold, in one call."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # --- Stat strip ------------------------------------------------------
    file_count = (
        await session.scalar(
            select(func.count(GraphNode.id)).where(
                GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
            )
        )
        or 0
    )
    symbol_count = int(
        await session.scalar(
            select(func.sum(GraphNode.symbol_count)).where(GraphNode.repository_id == repo_id)
        )
        or 0
    )
    entry_point_count = (
        await session.scalar(
            select(func.count(GraphNode.id)).where(
                GraphNode.repository_id == repo_id,
                GraphNode.is_entry_point.is_(True),
            )
        )
        or 0
    )
    avg_confidence = float(
        await session.scalar(select(func.avg(Page.confidence)).where(Page.repository_id == repo_id))
        or 0.0
    )
    doc_coverage_pct = avg_confidence * 100
    total_pages = (
        await session.scalar(select(func.count(Page.id)).where(Page.repository_id == repo_id)) or 0
    )
    # Template-provider pages are the deterministic coverage tail (zero LLM);
    # everything else came out of a model. Same discriminator the page schema
    # uses for the "Auto" badge.
    auto_pages = (
        await session.scalar(
            select(func.count(Page.id)).where(
                Page.repository_id == repo_id, Page.provider_name == "template"
            )
        )
        or 0
    )
    fresh_pages = (
        await session.scalar(
            select(func.count(Page.id)).where(
                Page.repository_id == repo_id, Page.freshness_status == "fresh"
            )
        )
        or 0
    )
    freshness_score = (fresh_pages / total_pages * 100) if total_pages > 0 else doc_coverage_pct
    dead_export_count = (
        await session.scalar(
            select(func.count(DeadCodeFinding.id)).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.kind == "unused_export",
                DeadCodeFinding.status == "open",
            )
        )
        or 0
    )
    hotspot_count = (
        await session.scalar(
            select(func.count(GitMetadata.id)).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.is_hotspot.is_(True),
            )
        )
        or 0
    )

    # Module-level silo counts (top-level-directory grouping — mirrors the
    # /ownership?granularity=module aggregation the page used to fetch).
    owner_rows = await session.execute(
        select(GitMetadata.file_path, GitMetadata.primary_owner_name).where(
            GitMetadata.repository_id == repo_id
        )
    )
    module_owner_files: dict[str, dict[str, int]] = {}
    module_file_totals: dict[str, int] = {}
    for fp, owner in owner_rows:
        parts = fp.split("/")
        module = parts[0] if len(parts) > 1 else "root"
        module_file_totals[module] = module_file_totals.get(module, 0) + 1
        if owner:
            bucket = module_owner_files.setdefault(module, {})
            bucket[owner] = bucket.get(owner, 0) + 1
    module_count = len(module_file_totals)
    silo_count = 0
    for module, owners in module_owner_files.items():
        top = max(owners.values(), default=0)
        if module_file_totals.get(module) and top / module_file_totals[module] > 0.8:
            silo_count += 1

    # --- Language distribution (server-side; replaces the full graph export)
    lang_rows = await session.execute(
        select(GraphNode.language, func.count(GraphNode.id))
        .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
        .group_by(GraphNode.language)
    )
    languages = sorted(
        ({"language": lang or "other", "file_count": n} for lang, n in lang_rows),
        key=lambda r: -r["file_count"],
    )

    # --- Health KPIs + deltas vs previous snapshot ------------------------
    health_summary = await crud.get_health_summary(session, repo_id)
    snapshots = await crud.list_health_snapshots(session, repo_id)
    hotspot_health: float | None = None
    last_indexed_at: str | None = None
    deltas: dict[str, float | None] = {
        "average_health": None,
        "hotspot_health": None,
        "file_count": None,
    }
    if snapshots:
        latest = snapshots[-1]
        hotspot_health = round(float(latest.hotspot_health), 2)
        last_indexed_at = latest.taken_at.isoformat() if latest.taken_at else None
    if len(snapshots) >= 2:
        prev, cur = snapshots[-2], snapshots[-1]
        deltas["average_health"] = round(float(cur.average_health) - float(prev.average_health), 2)
        deltas["hotspot_health"] = round(float(cur.hotspot_health) - float(prev.hotspot_health), 2)
        try:
            prev_files = len(json.loads(prev.per_file_scores_json or "{}"))
            cur_files = len(json.loads(cur.per_file_scores_json or "{}"))
            if prev_files and cur_files:
                deltas["file_count"] = cur_files - prev_files
        except Exception:
            pass

    findings = await crud.get_health_findings(session, repo_id)
    severity_breakdown = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        s = (f.severity or "").lower()
        if s in severity_breakdown:
            severity_breakdown[s] += 1

    # --- Attention items + onboarding targets -----------------------------
    decision_health = await crud.get_decision_health_summary(session, repo_id)
    knowledge = await compute_knowledge_map(session, repo_id)
    dead_safe = [
        f
        for f in await crud.get_dead_code_findings(session, repo_id, status="open")
        if f.safe_to_delete
    ]
    attention = _build_attention_items(
        decision_health, knowledge.get("knowledge_silos", []), dead_safe
    )

    # --- Top hotspots + recent decisions slices ---------------------------
    hotspot_rows = (
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
    top_hotspots = [_hotspot_from_row(r).model_dump(mode="json") for r in hotspot_rows]

    decisions = await crud.list_decisions(session, repo_id, limit=8)
    recent_decisions = [_decision_slim(d) for d in decisions]

    # --- Sync status (last completed jobs + any active one) ---------------
    job_rows = (
        (
            await session.execute(
                select(GenerationJob)
                .where(GenerationJob.repository_id == repo_id)
                .order_by(GenerationJob.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    last_sync_at: str | None = None
    last_resync_at: str | None = None
    last_resync_dt = None
    last_sync_model: str | None = None
    active_job_id: str | None = None
    for j in job_rows:
        try:
            mode = json.loads(j.config_json or "{}").get("mode")
        except Exception:
            mode = None
        if j.status in ("pending", "running") and active_job_id is None:
            active_job_id = j.id
        if j.status == "completed" and j.finished_at:
            if mode == "full_resync" and last_resync_at is None:
                last_resync_at = j.finished_at.isoformat()
                last_resync_dt = j.finished_at
            elif mode != "full_resync" and last_sync_at is None:
                last_sync_at = j.finished_at.isoformat()
                last_sync_model = j.model_name or None

    # Fall back to the repository's own ``updated_at`` when no completed
    # server-side sync job exists. CLI / git-hook auto-syncs (``repowise
    # update``) refresh the index and bump ``repositories.updated_at`` via
    # ``upsert_repository``, but never create a GenerationJob row, so a
    # job-only derivation reports "never synced" even though the index is
    # current. Only adopt it when it is newer than the last full re-index so a
    # re-index is not relabelled as a sync.
    if (
        last_sync_at is None
        and repo.updated_at is not None
        and (last_resync_dt is None or repo.updated_at > last_resync_dt)
    ):
        last_sync_at = repo.updated_at.isoformat()

    savings = await _savings_headline(repo.local_path)
    repowise_dir = Path(repo.local_path) / ".repowise" if repo.local_path else Path(".repowise")

    return {
        "repo": {
            "id": repo.id,
            "name": repo.name,
            "local_path": repo.local_path,
            "default_branch": repo.default_branch,
            "head_commit": repo.head_commit,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
        },
        "stats": {
            "file_count": file_count,
            "symbol_count": symbol_count,
            "entry_point_count": entry_point_count,
            "doc_page_count": total_pages,
            "doc_auto_page_count": auto_pages,
            "doc_coverage_pct": doc_coverage_pct,
            "freshness_score": freshness_score,
            "dead_export_count": dead_export_count,
            "hotspot_count": hotspot_count,
            "silo_count": silo_count,
            "module_count": module_count,
            "deltas": deltas,
        },
        "health": {
            "average_health": health_summary.get("average_health"),
            "hotspot_health": hotspot_health,
            "worst_performer_path": health_summary.get("worst_performer_path"),
            "worst_performer_score": health_summary.get("worst_performer_score"),
            "open_findings": health_summary.get("open_findings", 0),
            # The two co-equal pillars surfaced alongside the defect headline.
            "maintainability_average": health_summary.get("maintainability_average"),
            "performance_average": health_summary.get("performance_average"),
            "performance_findings": health_summary.get("performance_findings", 0),
            "worst_performance_path": health_summary.get("worst_performance_path"),
            "worst_performance_score": health_summary.get("worst_performance_score"),
            "severity_breakdown": severity_breakdown,
            "last_indexed_at": last_indexed_at,
            "snapshot_count": len(snapshots),
            "history": [
                {
                    "taken_at": s.taken_at.isoformat() if s.taken_at else None,
                    "average_health": round(float(s.average_health), 2),
                    "hotspot_health": round(float(s.hotspot_health), 2),
                }
                for s in snapshots[-12:]
            ],
        },
        "languages": languages,
        "attention": attention,
        "onboarding_targets": knowledge.get("onboarding_targets", []),
        "top_hotspots": top_hotspots,
        "recent_decisions": recent_decisions,
        "savings": savings,
        "sync": {
            "last_sync_at": last_sync_at,
            "last_resync_at": last_resync_at,
            "last_sync_model": last_sync_model,
            "active_job_id": active_job_id,
            "page_count": total_pages,
            "index_storage_bytes": _index_storage_bytes(repowise_dir),
        },
    }
