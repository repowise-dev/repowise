"""Section builders for ``/api/repos/{repo_id}/overview-summary``.

Each function reads and shapes one block of the Overview payload. The router
assembles them in its fixed key order; a private consumer mirrors that
payload field for field, so a builder's keys and their order are its contract.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.aggregation import (
    severity_breakdown as health_severity_breakdown,
)
from repowise.core.analysis.health.grading import headline_band
from repowise.core.analysis.health.scoring import hotspot_health
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.queue_counts import unit_counts
from repowise.core.persistence.crud.analysis.shown import ungated
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GenerationJob,
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.core.stats_highlights import file_mix
from repowise.server.services.attention import build_attention
from repowise.server.services.knowledge_map import (
    compute_knowledge_silos,
    compute_onboarding_targets,
)
from repowise.server.services.module_health import top_level_module


async def _count(session: AsyncSession, stmt: Any) -> int:
    return await session.scalar(stmt) or 0


async def docs_split(session: AsyncSession, repo_id: str) -> dict:
    """Page counts, model-written share, coverage and freshness."""
    avg_confidence = float(
        await session.scalar(select(func.avg(Page.confidence)).where(Page.repository_id == repo_id))
        or 0.0
    )
    doc_coverage_pct = avg_confidence * 100
    total_pages = await _count(
        session, select(func.count(Page.id)).where(Page.repository_id == repo_id)
    )
    # Pages a model actually wrote, as opposed to the ones assembled from the
    # index alone. The discriminator is the provider: "template" is the stub
    # writer, so anything else means a real generation call produced the prose.
    #
    # Deliberately NOT additionally scoped to MODEL_WRITTEN_PAGE_TYPES: file and
    # symbol pages do get generated with a real provider on some runs, so
    # scoping by type puts model-written pages in the "assembled from the index"
    # bucket. Asking "did a model write this page" needs no page-type carve-out.
    #
    # A stubbed page (a provider call that failed and fell back) counts as
    # not-prose, which is correct: it has no prose.
    prose_pages = await _count(
        session,
        select(func.count(Page.id)).where(
            Page.repository_id == repo_id,
            Page.provider_name.is_not(None),
            Page.provider_name != "template",
        ),
    )
    fresh_pages = await _count(
        session,
        select(func.count(Page.id)).where(
            Page.repository_id == repo_id, Page.freshness_status == "fresh"
        ),
    )
    return {
        "doc_page_count": total_pages,
        "doc_prose_page_count": prose_pages,
        "doc_coverage_pct": doc_coverage_pct,
        "freshness_score": (
            (fresh_pages / total_pages * 100) if total_pages > 0 else doc_coverage_pct
        ),
    }


async def stat_strip(session: AsyncSession, repo_id: str) -> dict:
    """Graph, docs, dead-export and hotspot counts, in payload order."""
    file_count = await _count(
        session,
        select(func.count(GraphNode.id)).where(
            GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
        ),
    )
    symbol_count = int(
        await session.scalar(
            select(func.sum(GraphNode.symbol_count)).where(GraphNode.repository_id == repo_id)
        )
        or 0
    )
    entry_point_count = await _count(
        session,
        select(func.count(GraphNode.id)).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_entry_point.is_(True),
        ),
    )
    docs = await docs_split(session, repo_id)
    dead_export_count = await _count(
        session,
        select(func.count(DeadCodeFinding.id)).where(
            DeadCodeFinding.repository_id == repo_id,
            DeadCodeFinding.kind == "unused_export",
            DeadCodeFinding.status == "open",
            ungated("dead_code", DeadCodeFinding.file_path, DeadCodeFinding.kind),
        ),
    )
    hotspot_count = await _count(
        session,
        select(func.count(GitMetadata.id)).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.is_hotspot.is_(True),
        ),
    )
    return {
        "file_count": file_count,
        "symbol_count": symbol_count,
        "entry_point_count": entry_point_count,
        **docs,
        "dead_export_count": dead_export_count,
        "hotspot_count": hotspot_count,
    }


def count_silos(rows: Any) -> int:
    """Top-level modules where one owner holds over 80% of the files.

    Mirrors the ``/ownership?granularity=module`` aggregation the page used to
    fetch. ``rows`` are ``(file_path, primary_owner_name)`` pairs.
    """
    module_owner_files: dict[str, dict[str, int]] = {}
    module_file_totals: dict[str, int] = {}
    for fp, owner in rows:
        module = top_level_module(fp)
        module_file_totals[module] = module_file_totals.get(module, 0) + 1
        if owner:
            bucket = module_owner_files.setdefault(module, {})
            bucket[owner] = bucket.get(owner, 0) + 1
    silo_count = 0
    for module, owners in module_owner_files.items():
        top = max(owners.values(), default=0)
        if module_file_totals.get(module) and top / module_file_totals[module] > 0.8:
            silo_count += 1
    return silo_count


async def module_silo_count(session: AsyncSession, repo_id: str) -> int:
    owner_rows = await session.execute(
        select(GitMetadata.file_path, GitMetadata.primary_owner_name).where(
            crud.code_file_rows(repo_id)
        )
    )
    return count_silos(owner_rows)


async def language_mix(session: AsyncSession, repo_id: str) -> dict:
    """Languages and module count, from the one rule Stats uses.

    So the two pages cannot disagree on either number. One narrow row per file
    node, not a GROUP BY: module_count needs paths. Fine to ~100k files; past
    that, count languages in SQL and fetch only code paths.
    """
    node_rows = await session.execute(
        select(GraphNode.node_id, GraphNode.language, GraphNode.external_system_id).where(
            GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
        )
    )
    return file_mix(row._mapping for row in node_rows)


async def snapshot_deltas(session: AsyncSession, repo_id: str, snapshot: Any) -> dict:
    """Stat-strip deltas between the two newest recorded snapshots.

    Snapshot-derived on purpose: there is no live value for "the run before
    this one", so a delta means "since the last recorded snapshot".
    """
    deltas: dict[str, float | None] = {
        "average_health": None,
        "hotspot_health": None,
        "file_count": None,
    }
    if len(snapshot.recent) >= 2:
        prev, cur = snapshot.recent[-2], snapshot.recent[-1]
        deltas["average_health"] = round(cur.average_health - prev.average_health, 2)
        if cur.hotspot_health is not None and prev.hotspot_health is not None:
            deltas["hotspot_health"] = round(cur.hotspot_health - prev.hotspot_health, 2)
        file_counts = await crud.get_health_snapshot_file_counts(session, repo_id, limit=2)
        if len(file_counts) == 2 and all(file_counts):
            deltas["file_count"] = file_counts[1] - file_counts[0]
    return deltas


def defect_accuracy(health_metrics: list[Any], findings: list[Any]) -> dict | None:
    """Backtested precision of the defect ranking ("can you trust this score?").

    Sourced here rather than from the stats payload: this is a health number,
    and reaching into another page's payload for it is what broke when that
    payload changed.

    ``prior_defect`` rows only, each carrying its parsed ``details``. The stat
    reads no other biomarker, and it reads the fix count and the window out of
    ``details``; without them every flagged file reported ``recent_fixes: 1``
    and ``window_days`` echoed the default, so this card disagreed with the
    health dashboard on the same figure. Parsed per row: one malformed blob
    must not cost the whole panel.
    """
    prior_defect_rows: list[dict] = []
    for f in findings:
        if f.biomarker_type != "prior_defect":
            continue
        details: Any = {}
        with contextlib.suppress(Exception):
            details = json.loads(f.details_json) if f.details_json else {}
        prior_defect_rows.append(
            {
                "file_path": f.file_path,
                "biomarker_type": f.biomarker_type,
                "details": details,
            }
        )
    try:
        from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
        from repowise.core.analysis.health.ranking import deduction_by_path

        return compute_defect_accuracy(
            [
                {
                    "file_path": m.file_path,
                    "score": m.score,
                    "nloc": m.nloc,
                    "has_test_file": m.has_test_file,
                    "module": m.module,
                }
                for m in health_metrics
            ],
            prior_defect_rows,
            deductions=deduction_by_path(findings),
        )
    except Exception:
        # Best-effort: the card omits the panel rather than failing the page.
        return None


def snapshot_history(snapshot: Any) -> list[dict]:
    return [
        {
            "taken_at": s.taken_at.isoformat() if s.taken_at else None,
            "average_health": round(s.average_health, 2),
            "hotspot_health": (round(s.hotspot_health, 2) if s.hotspot_health is not None else None),
        }
        for s in snapshot.recent
    ]


async def health_block(
    session: AsyncSession, repo_id: str, health_metrics: list[Any], snapshot: Any
) -> dict:
    """Health KPIs, the defect-accuracy stat and the snapshot trend.

    ``health_metrics`` is loaded once by the caller and shared: the KPI rollup
    and the defect-accuracy stat both want every per-file row, and letting each
    fetch its own pulled the whole table twice per page load.

    The headline is the *current* number, so it comes from the metrics rather
    than the newest snapshot: ``repowise update`` re-scores health without
    writing a snapshot, so the stored value can lag the rows every other figure
    on this page is built from. ``history`` stays snapshot-derived: it is a
    series of recorded runs.
    """
    # Every figure here is a KPI the scores counted, so the file-held rows stay
    # in; ``gated`` says what the finding lists leave out.
    findings = await crud.get_health_findings(session, repo_id, file_holds=False)
    summary = await crud.get_health_summary(
        session, repo_id, metrics=health_metrics, findings=findings
    )
    hotspot_paths = await crud.get_hotspot_file_paths(session, repo_id)
    last_indexed_at = (
        snapshot.taken_at.isoformat() if snapshot.snapshot_count and snapshot.taken_at else None
    )
    return {
        "average_health": summary.get("average_health"),
        # Files in a language health has no dialect for: in no figure here.
        "unanalysed_file_count": summary.get("unanalysed_file_count", 0),
        "analysed_share": summary.get("analysed_share"),
        # ``None`` when too few code files were analysed to grade the repo.
        "band": headline_band(summary.get("average_health"), summary.get("analysed_share")),
        "hotspot_health": hotspot_health(health_metrics, hotspot_paths),
        "worst_performer_path": summary.get("worst_performer_path"),
        "worst_performer_score": summary.get("worst_performer_score"),
        "open_findings": summary.get("open_findings", 0),
        # The findings' count vocabulary, which the Code Health page and the
        # agent surfaces report too; ``open_findings`` stays for older readers.
        "finding_counts": (await unit_counts(session, repo_id, "findings")).as_dict(),
        # Why the finding lists hold some of those back, by language or file hold.
        "gated": await crud.gated_health_counts(session, repo_id),
        # The two co-equal pillars surfaced alongside the defect headline.
        "maintainability_average": summary.get("maintainability_average"),
        "performance_average": summary.get("performance_average"),
        # Open causes: worth fixing now, in production code, overall.
        "performance_causes": summary.get("performance_causes"),
        "worst_performance_path": summary.get("worst_performance_path"),
        "worst_performance_score": summary.get("worst_performance_score"),
        "severity_breakdown": health_severity_breakdown(findings),
        "defect_accuracy": defect_accuracy(health_metrics, findings),
        "last_indexed_at": last_indexed_at,
        # The true total, not the length of the windowed read.
        "snapshot_count": snapshot.snapshot_count,
        "history": snapshot_history(snapshot),
    }


async def attention_block(session: AsyncSession, repo_id: str) -> dict:
    """``attention``, ``attention_summary`` and ``onboarding_targets``."""
    decision_health = await crud.get_decision_health_summary(session, repo_id)
    # Silos only. The full knowledge map also computes top owners and
    # onboarding targets, and paying for those here meant loading the content
    # of every generated page to count its words for a list this page does not
    # render.
    knowledge_silos = await compute_knowledge_silos(session, repo_id)
    # Read by the first-index experience. Its own call rather than the full
    # knowledge map, which would also aggregate owners this payload never uses.
    onboarding_targets = await compute_onboarding_targets(session, repo_id)
    # Every finding store, merged and ranked worst-first; see
    # `core/analysis/attention` for the ranking.
    result = await build_attention(
        session,
        repo_id,
        decision_health=decision_health,
        knowledge_silos=knowledge_silos,
    )
    return {
        "attention": result["items"],
        # Counts for everything the sources hold, beside the capped list. The
        # page shows five rows out of these, and "5 open" would be a lie that
        # gets worse the more findings a repository has.
        "attention_summary": {
            "total": result["total"],
            "by_source": result["by_source"],
            "areas": result["areas"],
        },
        "onboarding_targets": onboarding_targets,
    }


def decision_slim(d: Any) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "source": d.source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "staleness_score": round(float(d.staleness_score or 0.0), 3),
    }


async def recent_decisions(session: AsyncSession, repo_id: str) -> list[dict]:
    return [decision_slim(d) for d in await crud.list_decisions(session, repo_id, limit=8)]


async def savings_block(repo_local_path: str | None) -> dict:
    """Savings headline, from the one core report service.

    Headline figures only: no breakdowns and no transcript scan, which reads
    agent transcripts and is far too slow for an overview payload. Every field
    comes from the same report the savings endpoint and ``repowise saved``
    read, so the three cannot drift. They previously did, in four separate
    ways, because each aggregated and priced the ledger for itself.

    The measured/inferred and priced/unpriced splits travel beside the total on
    purpose. A lone headline reads as one confident number, and the evidence
    behind it is not uniform.
    """
    if not repo_local_path:
        return {"available": False}

    from repowise.core.savings.service import load_report

    report = load_report(repo_local_path)
    if report is None:
        return {"available": False}
    return {
        "available": True,
        "saved_input_tokens": report.saved_input_tokens,
        "measured_saved_input_tokens": report.measured_saved_input_tokens,
        "inferred_saved_input_tokens": report.inferred_saved_input_tokens,
        "priced_saved_input_tokens": report.priced_saved_input_tokens,
        "unpriced_saved_input_tokens": report.unpriced_saved_input_tokens,
        "estimated_usd_saved": report.priced_input_savings_usd,
        "mcp_queries_answered": report.mcp_queries_answered,
        "last_event_at": report.last_event_at,
    }


def sync_times(job_rows: list[Any], repo_updated_at: Any) -> dict:
    """Last sync, last full re-index, its model and any active job id.

    ``job_rows`` are newest first.
    """
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
        and repo_updated_at is not None
        and (last_resync_dt is None or repo_updated_at > last_resync_dt)
    ):
        last_sync_at = repo_updated_at.isoformat()
    return {
        "last_sync_at": last_sync_at,
        "last_resync_at": last_resync_at,
        "last_sync_model": last_sync_model,
        "active_job_id": active_job_id,
    }


async def sync_block(session: AsyncSession, repo_id: str, repo: Any, page_count: int) -> dict:
    """Sync status from the last 30 jobs, plus the page count."""
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
    return {**sync_times(job_rows, repo.updated_at), "page_count": page_count}
