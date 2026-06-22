"""/api/repos/{repo_id}/stats/highlights — the "By the Numbers" payload.

A single read-only aggregate that powers the repo Stats page: a showcase of
signals the engine already computes across every layer (graph, git, health,
docs, decisions, dead code) plus a handful of fun superlatives and a derived
"size class" label. Nothing here is new analysis — it stitches together rows
the indexer already wrote, so the page is cheap and never blocks.

Every section is built defensively: a missing table or column degrades that
section to ``None`` / empty rather than 500-ing the whole page, mirroring the
overview-summary contract.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DecisionRecord,
    GitCommit,
    GitMetadata,
    GraphNode,
    Page,
    WikiSymbol,
)
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    prefix="/api/repos",
    tags=["stats"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Size class — a playful, NLOC-driven label for "how big is this codebase".
# Thresholds are on non-comment lines of code (the health-metric NLOC sum),
# the most honest single proxy for scale across languages.
# ---------------------------------------------------------------------------

_SIZE_CLASSES: tuple[tuple[int, str, str], ...] = (
    (1_000, "Seedling", "A fresh sprout — small enough to hold in your head."),
    (5_000, "Hamlet", "A cozy codebase you could read in an afternoon."),
    (20_000, "Village", "A tidy village — a few neighborhoods, easy to walk."),
    (60_000, "Town", "A proper town with its own districts and main streets."),
    (150_000, "City", "A real city — busy, layered, plenty going on."),
    (500_000, "Metropolis", "A sprawling metropolis with serious infrastructure."),
)
_MEGALOPOLIS = ("Megalopolis", "A vast megalopolis — its own self-contained world.")


def _size_class(total_nloc: int) -> dict[str, Any]:
    for ceiling, name, blurb in _SIZE_CLASSES:
        if total_nloc < ceiling:
            return {"name": name, "blurb": blurb, "nloc": total_nloc}
    name, blurb = _MEGALOPOLIS
    return {"name": name, "blurb": blurb, "nloc": total_nloc}


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _scale(session: AsyncSession, repo_id: str, metrics: list[Any]) -> dict[str, Any]:
    """Graph + NLOC scale signals + the size-class label."""
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
    total_nloc = sum(int(m.nloc or 0) for m in metrics)

    lang_rows = await session.execute(
        select(GraphNode.language, func.count(GraphNode.id))
        .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
        .group_by(GraphNode.language)
    )
    languages = sorted(
        ({"language": lang or "other", "file_count": n} for lang, n in lang_rows),
        key=lambda r: -r["file_count"],
    )
    module_count = len({m.module for m in metrics if m.module})

    return {
        "file_count": file_count,
        "symbol_count": symbol_count,
        "entry_point_count": entry_point_count,
        "module_count": module_count,
        "total_nloc": total_nloc,
        "language_count": len(languages),
        "languages": languages,
        "size_class": _size_class(total_nloc),
    }


async def _activity(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """Commit volume, project age, agent-vs-human split, and a monthly series.

    Buckets the bounded ``git_commits`` table in Python so it is portable
    across SQLite/Postgres date functions (same approach as the agent-trend
    endpoint)."""
    rows = (
        await session.execute(
            select(
                GitCommit.committed_at,
                GitCommit.author_email,
                GitCommit.agent_name,
                GitCommit.is_fix,
            ).where(GitCommit.repository_id == repo_id)
        )
    ).all()

    total = 0
    agent_total = 0
    fix_total = 0
    months: dict[str, dict[str, int]] = {}
    agent_names: dict[str, int] = {}
    contributors: set[str] = set()
    first_at: Any = None
    last_at: Any = None

    for committed_at, author_email, agent_name, is_fix in rows:
        total += 1
        if author_email:
            contributors.add(author_email.lower())
        if is_fix:
            fix_total += 1
        if agent_name:
            agent_total += 1
            agent_names[agent_name] = agent_names.get(agent_name, 0) + 1
        if committed_at is not None:
            if first_at is None or committed_at < first_at:
                first_at = committed_at
            if last_at is None or committed_at > last_at:
                last_at = committed_at
            key = committed_at.strftime("%Y-%m")
            b = months.setdefault(key, {"total": 0, "agent": 0})
            b["total"] += 1
            if agent_name:
                b["agent"] += 1

    monthly = [
        {"month": m, "total": b["total"], "agent": b["agent"]} for m, b in sorted(months.items())
    ]
    busiest = max(monthly, key=lambda r: r["total"], default=None)
    age_days = (last_at - first_at).days if (first_at and last_at) else None

    return {
        "total_commits": total,
        "agent_commits": agent_total,
        "agent_pct": round(agent_total / total * 100.0, 1) if total else 0.0,
        "fix_commits": fix_total,
        "fix_pct": round(fix_total / total * 100.0, 1) if total else 0.0,
        "contributor_count": len(contributors),
        "first_commit_at": _iso(first_at),
        "last_commit_at": _iso(last_at),
        "age_days": age_days,
        "busiest_month": busiest,
        "monthly": monthly,
        "agent_names": sorted(
            ({"name": k, "count": v} for k, v in agent_names.items()),
            key=lambda x: -x["count"],
        ),
    }


async def _people(session: AsyncSession, repo_id: str, all_meta: list[Any]) -> dict[str, Any]:
    """Ownership concentration: top owners, single-owner files, module silos."""
    owners: dict[str, int] = {}
    single_owner_files = 0
    module_owner_files: dict[str, dict[str, int]] = {}
    module_file_totals: dict[str, int] = {}

    for m in all_meta:
        if m.primary_owner_name:
            owners[m.primary_owner_name] = owners.get(m.primary_owner_name, 0) + 1
        if (m.bus_factor or 0) == 1:
            single_owner_files += 1
        parts = m.file_path.split("/")
        module = parts[0] if len(parts) > 1 else "root"
        module_file_totals[module] = module_file_totals.get(module, 0) + 1
        if m.primary_owner_name:
            bucket = module_owner_files.setdefault(module, {})
            bucket[m.primary_owner_name] = bucket.get(m.primary_owner_name, 0) + 1

    total_files = len(all_meta) or 1
    top_owners = sorted(
        ({"name": k, "file_count": v, "pct": v / total_files} for k, v in owners.items()),
        key=lambda x: -x["file_count"],
    )[:8]

    silo_count = 0
    for module, mowners in module_owner_files.items():
        top = max(mowners.values(), default=0)
        if module_file_totals.get(module) and top / module_file_totals[module] > 0.8:
            silo_count += 1

    return {
        "owner_count": len(owners),
        "top_owners": top_owners,
        "single_owner_files": single_owner_files,
        "silo_count": silo_count,
    }


async def _quality(session: AsyncSession, repo_id: str, metrics: list[Any]) -> dict[str, Any]:
    """Health KPIs + the defect-validation stat + dead code + doc coverage."""
    from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
    from repowise.core.analysis.health.grading import distribution as health_distribution

    summary = await crud.get_health_summary(session, repo_id)
    findings = await crud.get_health_findings(session, repo_id)

    severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        s = (f.severity or "").lower()
        if s in severity:
            severity[s] += 1

    metric_dicts = [
        {
            "file_path": m.file_path,
            "score": m.score,
            "nloc": m.nloc,
            "has_test_file": m.has_test_file,
            "module": m.module,
        }
        for m in metrics
    ]
    finding_dicts = [
        {
            "file_path": f.file_path,
            "biomarker_type": f.biomarker_type,
            "severity": f.severity,
        }
        for f in findings
    ]
    try:
        defect_accuracy = compute_defect_accuracy(metric_dicts, finding_dicts)
    except Exception:
        defect_accuracy = None
    try:
        dist = health_distribution(metric_dicts)
    except Exception:
        dist = None

    # Dead code rollup
    dead = await crud.get_dead_code_findings(session, repo_id, status="open")
    deletable_lines = sum(int(f.lines or 0) for f in dead if f.safe_to_delete)
    dead_total = len(dead)

    # Doc coverage (avg page confidence) + page count
    avg_conf = float(
        await session.scalar(select(func.avg(Page.confidence)).where(Page.repository_id == repo_id))
        or 0.0
    )
    page_count = (
        await session.scalar(select(func.count(Page.id)).where(Page.repository_id == repo_id)) or 0
    )

    # Test coverage: share of health-metric files that have a test file.
    tested = sum(1 for m in metrics if m.has_test_file)
    test_coverage_pct = round(tested / len(metrics) * 100.0, 1) if metrics else None

    return {
        "average_health": summary.get("average_health"),
        "maintainability_average": summary.get("maintainability_average"),
        "performance_average": summary.get("performance_average"),
        "worst_performer_path": summary.get("worst_performer_path"),
        "worst_performer_score": summary.get("worst_performer_score"),
        "open_findings": summary.get("open_findings", len(findings)),
        "severity_breakdown": severity,
        "defect_accuracy": defect_accuracy,
        "distribution": dist,
        "doc_coverage_pct": avg_conf * 100.0,
        "page_count": page_count,
        "test_coverage_pct": test_coverage_pct,
        "dead_code": {"total_findings": dead_total, "deletable_lines": deletable_lines},
    }


async def _superlatives(
    session: AsyncSession, repo_id: str, metrics: list[Any], all_meta: list[Any]
) -> dict[str, Any]:
    """The fun "biggest / oldest / most" awards, one row each."""
    out: dict[str, Any] = {}

    # Largest file by NLOC
    largest = max(metrics, key=lambda m: m.nloc or 0, default=None)
    if largest is not None and (largest.nloc or 0) > 0:
        out["largest_file"] = {"path": largest.file_path, "nloc": int(largest.nloc)}

    # Most complex symbol
    sym = (
        await session.execute(
            select(WikiSymbol.name, WikiSymbol.file_path, WikiSymbol.complexity_estimate)
            .where(WikiSymbol.repository_id == repo_id)
            .order_by(WikiSymbol.complexity_estimate.desc())
            .limit(1)
        )
    ).first()
    if sym is not None and (sym[2] or 0) > 0:
        out["most_complex_symbol"] = {
            "name": sym[0],
            "file_path": sym[1],
            "complexity": int(sym[2]),
        }

    # Most-changed file + oldest file (from git metadata)
    most_changed = max(all_meta, key=lambda m: m.commit_count_total or 0, default=None)
    if most_changed is not None and (most_changed.commit_count_total or 0) > 0:
        out["most_changed_file"] = {
            "path": most_changed.file_path,
            "commit_count": int(most_changed.commit_count_total),
        }
    dated = [m for m in all_meta if m.first_commit_at is not None]
    if dated:
        oldest = min(dated, key=lambda m: m.first_commit_at)
        out["oldest_file"] = {
            "path": oldest.file_path,
            "first_commit_at": _iso(oldest.first_commit_at),
        }

    # Most central file (highest PageRank file node)
    central = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank)
            .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
            .order_by(GraphNode.pagerank.desc())
            .limit(1)
        )
    ).first()
    if central is not None and (central[1] or 0) > 0:
        out["most_central_file"] = {"path": central[0], "pagerank": round(float(central[1]), 4)}

    # Strongest hidden coupling pair (max co-change count across files)
    best_pair: dict[str, Any] | None = None
    for m in all_meta:
        try:
            partners = json.loads(m.co_change_partners_json or "[]")
        except Exception:
            continue
        for p in partners:
            count = p.get("co_change_count", 0)
            other = p.get("file_path") or p.get("partner")
            if other and (best_pair is None or count > best_pair["count"]):
                best_pair = {"a": m.file_path, "b": other, "count": count}
    if best_pair and best_pair["count"] > 0:
        out["strongest_coupling"] = best_pair

    return out


@router.get("/{repo_id}/stats/highlights")
async def stats_highlights(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Everything the Stats ("By the Numbers") page needs, in one call."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    all_meta = list(
        (await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id)))
        .scalars()
        .all()
    )

    decision_count = (
        await session.scalar(
            select(func.count(DecisionRecord.id)).where(DecisionRecord.repository_id == repo_id)
        )
        or 0
    )
    active_decisions = (
        await session.scalar(
            select(func.count(DecisionRecord.id)).where(
                DecisionRecord.repository_id == repo_id,
                DecisionRecord.status == "active",
            )
        )
        or 0
    )

    return {
        "repo": {
            "id": repo.id,
            "name": repo.name,
            "default_branch": repo.default_branch,
            "head_commit": repo.head_commit,
        },
        "scale": await _scale(session, repo_id, metrics),
        "activity": await _activity(session, repo_id),
        "people": await _people(session, repo_id, all_meta),
        "quality": await _quality(session, repo_id, metrics),
        "knowledge": {
            "decision_count": decision_count,
            "active_decision_count": active_decisions,
        },
        "superlatives": await _superlatives(session, repo_id, metrics, all_meta),
    }
