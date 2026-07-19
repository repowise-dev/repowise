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
import re
from datetime import timedelta
from itertools import pairwise
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.git_indexer import build_identity_resolver
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DecisionRecord,
    ExternalSystem,
    GitCommit,
    GitMetadata,
    GraphMetric,
    GraphNode,
    GraphNodeMembership,
    LlmCost,
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


# Test-path convention shared with analysis (communities / execution_flows):
# catches conftest, fixtures, and spec files the `is_test` flag misses.
_TEST_PATH_RE = re.compile(
    r"(test[s_/]|_test\.|\.test\.|\.spec\.|__tests__|conftest|fixture[s]?[/.])"
)


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


def _punch_card_summary(punch: list[list[int]], dated_total: int) -> dict[str, Any]:
    """Fold the weekday x hour commit matrix into a renderable summary.

    ``matrix`` is 7 rows (0=Monday) x 24 hours in the stored UTC. Also names the
    single hottest cell and the busiest weekday / peak hour (by marginal
    totals) — the human-readable hooks the hero renders. The weekend share is
    deliberately not computed here: which days are the weekend is a reader
    preference, and the matrix already carries every weekday total."""
    peak = {"weekday": 0, "hour": 0, "count": 0}
    for wd in range(7):
        for hr in range(24):
            if punch[wd][hr] > peak["count"]:
                peak = {"weekday": wd, "hour": hr, "count": punch[wd][hr]}

    weekday_totals = [sum(punch[wd]) for wd in range(7)]
    hour_totals = [sum(punch[wd][hr] for wd in range(7)) for hr in range(24)]
    busiest_weekday = max(range(7), key=lambda wd: weekday_totals[wd]) if dated_total else None
    peak_hour = max(range(24), key=lambda hr: hour_totals[hr]) if dated_total else None

    return {
        "matrix": punch,
        "peak": peak if peak["count"] > 0 else None,
        "busiest_weekday": busiest_weekday,
        "peak_hour": peak_hour,
        "total": dated_total,
    }


def _commit_velocity(commit_times: list[Any], last_at: Any) -> dict[str, Any]:
    """Recent-vs-prior commit momentum, anchored to the newest commit.

    Anchoring to ``last_at`` (not wall-clock now) keeps the signal meaningful on
    an index that hasn't been synced today: it compares the 90 days ending at
    the latest commit against the 90 before that. ``pct_change`` is None when the
    prior window is empty (a young repo), so the UI can omit a divide-by-zero
    arrow rather than show a fake spike."""
    if last_at is None or not commit_times:
        return {"recent_90d": 0, "prior_90d": 0, "pct_change": None}
    recent_cut = last_at - timedelta(days=90)
    prior_cut = last_at - timedelta(days=180)
    recent = sum(1 for t in commit_times if t > recent_cut)
    prior = sum(1 for t in commit_times if prior_cut < t <= recent_cut)
    pct_change = round((recent - prior) / prior * 100.0, 1) if prior else None
    return {"recent_90d": recent, "prior_90d": prior, "pct_change": pct_change}


async def _activity(session: AsyncSession, repo_id: str, repo: Any) -> dict[str, Any]:
    """Commit volume, project age, agent-vs-human split, and a monthly series.

    Buckets the bounded ``git_commits`` table in Python so it is portable
    across SQLite/Postgres date functions (same approach as the agent-trend
    endpoint).

    Headline totals (commit count, project age, contributor count, founder)
    prefer the whole-history values captured on the ``Repository`` row at index
    time — the ``git_commits`` table is bounded to the newest N commits, so
    deriving them from it undercounts a long-lived repo badly (issue #730). The
    bounded scan still drives the *sample* signals (agent/fix ratios, monthly
    series, awards) that are meaningful on the recent window.

    Also derives the ``biggest_commit`` and ``longest_streak`` awards on the
    same pass — they need per-commit rows anyway, and riding along here keeps
    the endpoint at a single scan of the commits table. The caller moves them
    into the superlatives payload."""
    rows = (
        await session.execute(
            select(
                GitCommit.committed_at,
                GitCommit.author_name,
                GitCommit.author_email,
                GitCommit.agent_name,
                GitCommit.is_fix,
                GitCommit.sha,
                GitCommit.subject,
                GitCommit.lines_added,
                GitCommit.lines_deleted,
                GitCommit.files_changed,
                GitCommit.change_risk_level,
            ).where(GitCommit.repository_id == repo_id)
        )
    ).all()

    # Fold GitHub noreply variants and same-name real+noreply emails to one
    # identity so the same person isn't counted as several contributors.
    resolve = build_identity_resolver([(name, email) for _, name, email, *_ in rows])

    total = 0
    agent_total = 0
    fix_total = 0
    months: dict[str, dict[str, int]] = {}
    agent_names: dict[str, int] = {}
    contributors: set[str] = set()
    first_at: Any = None
    first_sha: str | None = None
    last_at: Any = None
    commit_days: set[Any] = set()
    # Coding-rhythm punch card: commits bucketed by weekday (0=Mon) x hour (UTC,
    # the stored tz). Plus the raw timestamps for the recent-vs-prior velocity
    # window and a low/moderate/high change-risk tally — all off this one scan.
    punch = [[0] * 24 for _ in range(7)]
    commit_times: list[Any] = []
    risk_mix = {"low": 0, "moderate": 0, "high": 0}
    # Top-2 commits by churn: the repo's very first commit is excluded from
    # the "biggest commit" award (every import/initial commit would win), so
    # two candidates are enough to survive dropping it.
    top_commits: list[dict[str, Any]] = []

    for row in rows:
        committed_at, author_name, author_email, agent_name, is_fix = row[:5]
        sha, subject, lines_added, lines_deleted, files_changed, change_risk_level = row[5:]
        total += 1
        if author_email or author_name:
            key = resolve(author_name, author_email)
            if key:
                contributors.add(key)
        if is_fix:
            fix_total += 1
        if change_risk_level in risk_mix:
            risk_mix[change_risk_level] += 1
        if agent_name:
            agent_total += 1
            agent_names[agent_name] = agent_names.get(agent_name, 0) + 1
        churn = int(lines_added or 0) + int(lines_deleted or 0)
        if churn > 0:
            top_commits.append(
                {
                    "sha": sha,
                    "subject": subject or "",
                    "lines_changed": churn,
                    "files_changed": int(files_changed or 0),
                }
            )
            top_commits.sort(key=lambda c: -c["lines_changed"])
            del top_commits[2:]
        if committed_at is not None:
            if first_at is None or committed_at < first_at:
                first_at = committed_at
                first_sha = sha
            if last_at is None or committed_at > last_at:
                last_at = committed_at
            commit_days.add(committed_at.date())
            commit_times.append(committed_at)
            punch[committed_at.weekday()][committed_at.hour] += 1
            key = committed_at.strftime("%Y-%m")
            b = months.setdefault(key, {"total": 0, "agent": 0})
            b["total"] += 1
            if agent_name:
                b["agent"] += 1

    biggest_commit = next((c for c in top_commits if c["sha"] != first_sha), None)

    longest_streak: dict[str, Any] | None = None
    if commit_days:
        days_sorted = sorted(commit_days)
        best_len = run_len = 1
        best_end = days_sorted[0]
        for prev, cur in pairwise(days_sorted):
            run_len = run_len + 1 if (cur - prev).days == 1 else 1
            if run_len > best_len:
                best_len, best_end = run_len, cur
        if best_len >= 2:
            longest_streak = {
                "days": best_len,
                "start": (best_end - timedelta(days=best_len - 1)).isoformat(),
                "end": best_end.isoformat(),
            }

    monthly = [
        {"month": m, "total": b["total"], "agent": b["agent"]} for m, b in sorted(months.items())
    ]
    busiest = max(monthly, key=lambda r: r["total"], default=None)

    punch_card = _punch_card_summary(punch, len(commit_times))
    velocity = _commit_velocity(commit_times, last_at)

    # Prefer the whole-history values stamped on the repo at index time; fall
    # back to the bounded sample when they're absent (older index, non-git
    # repo). Age runs from the true first commit to the latest commit we have.
    true_total = getattr(repo, "total_commit_count", None)
    true_first = getattr(repo, "first_commit_at", None)
    true_contributors = getattr(repo, "total_contributor_count", None)
    effective_total = true_total if true_total is not None else total
    effective_first = true_first if true_first is not None else first_at
    effective_contributors = (
        true_contributors if true_contributors is not None else len(contributors)
    )
    age_days = (last_at - effective_first).days if (effective_first and last_at) else None

    return {
        "total_commits": effective_total,
        "agent_commits": agent_total,
        "agent_pct": round(agent_total / total * 100.0, 1) if total else 0.0,
        "fix_commits": fix_total,
        "fix_pct": round(fix_total / total * 100.0, 1) if total else 0.0,
        "contributor_count": effective_contributors,
        "first_commit_at": _iso(effective_first),
        "first_commit_author": getattr(repo, "first_commit_author", None),
        "last_commit_at": _iso(last_at),
        "age_days": age_days,
        "busiest_month": busiest,
        "monthly": monthly,
        "agent_names": sorted(
            ({"name": k, "count": v} for k, v in agent_names.items()),
            key=lambda x: -x["count"],
        ),
        "biggest_commit": biggest_commit,
        "longest_streak": longest_streak,
        "punch_card": punch_card,
        "velocity": velocity,
        "change_risk_mix": risk_mix,
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

    # Truck factor: the fewest primary owners who together hold >50% of owned
    # files — "how many people could walk out before the bus problem bites".
    # A factor of 1 means a single person owns most of the codebase.
    owned_total = sum(owners.values())
    truck_factor: int | None = None
    if owned_total:
        cumulative = 0
        truck_factor = 0
        for count in sorted(owners.values(), reverse=True):
            cumulative += count
            truck_factor += 1
            if cumulative * 2 > owned_total:
                break

    return {
        "owner_count": len(owners),
        "top_owners": top_owners,
        "single_owner_files": single_owner_files,
        "silo_count": silo_count,
        "truck_factor": truck_factor,
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

    # Most imported file — highest fan-in among non-test file nodes, the
    # legible version of "most central". External and test nodes are excluded
    # so the award names real project source; the `is_test` flag misses
    # conftest/fixture files, so the top candidates are re-checked against the
    # test-path convention used across analysis. Falls back to the PageRank
    # pick when graph metrics were not materialized for this repo.
    candidates = (
        await session.execute(
            select(GraphMetric.node_id, GraphMetric.in_degree, GraphMetric.pagerank)
            .join(
                GraphNode,
                (GraphNode.repository_id == GraphMetric.repository_id)
                & (GraphNode.node_id == GraphMetric.node_id),
            )
            .where(
                GraphMetric.repository_id == repo_id,
                GraphNode.node_type == "file",
                GraphNode.is_test.is_(False),
                GraphNode.external_system_id.is_(None),
                ~GraphNode.node_id.like("external:%"),
            )
            .order_by(GraphMetric.in_degree.desc())
            .limit(10)
        )
    ).all()
    imported = next((c for c in candidates if not _TEST_PATH_RE.search(c[0])), None)
    if imported is not None and (imported[1] or 0) > 0:
        out["most_central_file"] = {
            "path": imported[0],
            "pagerank": round(float(imported[2] or 0.0), 4),
            "import_count": int(imported[1]),
        }
    else:
        central = (
            await session.execute(
                select(GraphNode.node_id, GraphNode.pagerank)
                .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
                .order_by(GraphNode.pagerank.desc())
                .limit(1)
            )
        ).first()
        if central is not None and (central[1] or 0) > 0:
            out["most_central_file"] = {
                "path": central[0],
                "pagerank": round(float(central[1]), 4),
            }

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


async def _dependencies(session: AsyncSession, repo_id: str) -> dict[str, Any] | None:
    """Third-party dependency rollup from the manifest-derived rows."""
    rows = (
        await session.execute(
            select(ExternalSystem.ecosystem, ExternalSystem.is_dev_dep, func.count())
            .where(ExternalSystem.repository_id == repo_id)
            .group_by(ExternalSystem.ecosystem, ExternalSystem.is_dev_dep)
        )
    ).all()
    if not rows:
        return None

    total = runtime = dev = 0
    ecosystems: dict[str, int] = {}
    for eco, is_dev, n in rows:
        total += n
        if is_dev:
            dev += n
        else:
            runtime += n
        key = eco or "other"
        ecosystems[key] = ecosystems.get(key, 0) + n

    return {
        "total": total,
        "runtime": runtime,
        "dev": dev,
        "ecosystems": sorted(
            ({"name": k, "count": v} for k, v in ecosystems.items()),
            key=lambda x: -x["count"],
        ),
    }


async def _graph(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """Dependency-cycle and community structure from the graph snapshot.

    Reads the materialized ``graph_node_membership`` rows (no graph rebuild):
    strongly-connected components with ``scc_size > 1`` are import cycles, and
    ``symbol_community_id`` groups the natural neighborhoods. Pure aggregate
    SQL — a couple of GROUP BY scans, never a row pull."""
    scc_rows = (
        await session.execute(
            select(GraphNodeMembership.scc_id, func.count())
            .where(
                GraphNodeMembership.repository_id == repo_id,
                GraphNodeMembership.scc_size > 1,
            )
            .group_by(GraphNodeMembership.scc_id)
        )
    ).all()
    cycle_clusters = len(scc_rows)
    files_in_cycles = sum(int(n) for _, n in scc_rows)
    largest_cycle = max((int(n) for _, n in scc_rows), default=0)

    community_count = (
        await session.scalar(
            select(func.count(func.distinct(GraphNodeMembership.symbol_community_id))).where(
                GraphNodeMembership.repository_id == repo_id,
                GraphNodeMembership.symbol_community_id.is_not(None),
            )
        )
        or 0
    )

    return {
        "cycle_clusters": cycle_clusters,
        "files_in_cycles": files_in_cycles,
        "largest_cycle": largest_cycle,
        "community_count": int(community_count),
    }


async def _build(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """The knowledge base's own build stats — the wiki bragging about itself.

    Pages come from the ``wiki_pages`` count; tokens and cost from the
    ``llm_costs`` ledger written during generation. Single aggregate scan;
    every field degrades to 0 when generation was index-only (no LLM spend)."""
    page_count = (
        await session.scalar(select(func.count(Page.id)).where(Page.repository_id == repo_id)) or 0
    )
    cost_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(LlmCost.input_tokens + LlmCost.output_tokens), 0),
                func.coalesce(func.sum(LlmCost.cost_usd), 0.0),
                func.count(LlmCost.id),
            ).where(LlmCost.repository_id == repo_id)
        )
    ).one()
    total_tokens, cost_usd, op_count = cost_row

    return {
        "page_count": int(page_count),
        "total_tokens": int(total_tokens or 0),
        "cost_usd": round(float(cost_usd or 0.0), 2),
        "llm_operations": int(op_count or 0),
    }


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

    activity = await _activity(session, repo_id, repo)
    superlatives = await _superlatives(session, repo_id, metrics, all_meta)
    # Computed on _activity's commit scan for performance, but they are awards
    # so they belong under superlatives in the payload.
    for key in ("biggest_commit", "longest_streak"):
        value = activity.pop(key, None)
        if value:
            superlatives[key] = value

    return {
        "repo": {
            "id": repo.id,
            "name": repo.name,
            "default_branch": repo.default_branch,
            "head_commit": repo.head_commit,
        },
        "scale": await _scale(session, repo_id, metrics),
        "activity": activity,
        "people": await _people(session, repo_id, all_meta),
        "quality": await _quality(session, repo_id, metrics),
        "knowledge": {
            "decision_count": decision_count,
            "active_decision_count": active_decisions,
        },
        "dependencies": await _dependencies(session, repo_id),
        "graph": await _graph(session, repo_id),
        "build": await _build(session, repo_id),
        "superlatives": superlatives,
    }
