"""MCP Tool 3: get_risk — modification risk assessment."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
    Repository,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _is_workspace_mode,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._server import mcp

_FIX_PATTERN = re.compile(
    r"\b(fix|bug|patch|hotfix|revert|regression|broken|crash|error)\b",
    re.IGNORECASE,
)


def _derive_change_pattern(categories: dict[str, int]) -> str:
    """Derive a human-readable change pattern from commit category counts."""
    if not categories:
        return "uncategorized"
    total = sum(categories.values())
    if total == 0:
        return "uncategorized"
    dominant = max(categories, key=lambda k: categories[k])
    ratio = categories[dominant] / total
    if ratio >= 0.5:
        labels = {
            "feature": "feature-active",
            "refactor": "primarily refactored",
            "fix": "fix-heavy",
            "dependency": "dependency-churn",
        }
        return labels.get(dominant, dominant)
    return "mixed-activity"


def _compute_trend(meta: Any) -> str:
    """Compute risk velocity from 30d vs 90d commit rates."""
    c30 = meta.commit_count_30d or 0
    c90 = meta.commit_count_90d or 0
    # Baseline: commits in the 60-day window before the last 30 days
    baseline_commits = c90 - c30
    recent_rate = c30 / 30.0
    baseline_rate = baseline_commits / 60.0

    if c90 == 0:
        return "stable"
    if baseline_rate == 0:
        return "increasing" if c30 > 0 else "stable"
    ratio = recent_rate / baseline_rate
    if ratio > 1.5:
        return "increasing"
    elif ratio < 0.5:
        return "decreasing"
    return "stable"


def _classify_risk_type(meta: Any, dep_count: int) -> str:
    """Classify risk as churn-heavy, bug-prone, high-coupling, or bus-factor-risk."""
    # Count bug-fix commits from significant_commits messages
    commits = json.loads(meta.significant_commits_json) if meta.significant_commits_json else []
    fix_count = sum(1 for c in commits if _FIX_PATTERN.search(c.get("message", "")))

    churn_score = meta.churn_percentile or 0.0
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    total_commits = meta.commit_count_total or 0

    # Bug-prone takes priority if fix ratio is high
    if commits and fix_count / len(commits) >= 0.4:
        return "bug-prone"
    if churn_score >= 0.7:
        return "churn-heavy"
    if bus_factor == 1 and total_commits > 20:
        return "bus-factor-risk"
    if dep_count >= 5:
        return "high-coupling"
    return "stable"


def _compute_impact_surface(
    target: str,
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
) -> list[dict]:
    """Find the top 3 most critical modules that depend on this file."""
    # BFS up to 2 hops through reverse dependencies
    visited: set[str] = set()
    frontier = {target}
    for _ in range(2):
        next_frontier: set[str] = set()
        for node in frontier:
            for dep in reverse_deps.get(node, set()):
                if dep != target and dep not in visited:
                    visited.add(dep)
                    next_frontier.add(dep)
        frontier = next_frontier

    if not visited:
        return []

    # Rank by pagerank (most critical first)
    ranked = []
    for dep in visited:
        meta = node_meta.get(dep)
        ranked.append(
            {
                "file_path": dep,
                "pagerank": meta.pagerank if meta else 0.0,
                "is_entry_point": meta.is_entry_point if meta else False,
            }
        )
    ranked.sort(key=lambda x: -x["pagerank"])
    return ranked[:3]


async def _check_test_gap(session: AsyncSession, repo_id: str, target: str) -> bool:
    """Return True if no test file corresponding to *target* exists in graph_nodes.

    Test files themselves (is_test=True) are never considered to have a test gap.
    """
    import os

    # Test files don't need tests — skip the check entirely
    node_res = await session.execute(
        select(GraphNode.is_test).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id == target,
        ).limit(1)
    )
    row = node_res.scalar_one_or_none()
    if row is True:
        return False

    base = os.path.splitext(os.path.basename(target))[0]
    ext = os.path.splitext(target)[1].lstrip(".")
    # Build a LIKE pattern broad enough to catch test_<base>, <base>_test, <base>.spec.*
    patterns = [f"%test_{base}%", f"%{base}_test%", f"%{base}.spec.{ext}%"]
    for pat in patterns:
        res = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.is_test == True,  # noqa: E712
                GraphNode.node_id.like(pat),
            ).limit(1)
        )
        if res.scalar_one_or_none() is not None:
            return False
    return True


async def _get_security_signals(
    session: AsyncSession, repo_id: str, target: str
) -> list[dict]:
    """Fetch stored security findings for *target* from security_findings table."""
    try:
        rows = await session.execute(
            text(
                "SELECT kind, severity, snippet FROM security_findings "
                "WHERE repository_id = :repo_id AND file_path = :fp "
                "ORDER BY severity DESC, kind"
            ),
            {"repo_id": repo_id, "fp": target},
        )
        return [
            {"kind": r[0], "severity": r[1], "snippet": r[2]}
            for r in rows.all()
        ]
    except Exception:  # noqa: BLE001 — table may not exist pre-migration
        return []


async def _assess_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    all_edge_map: dict[str, int],
    import_links: dict[str, set[str]],
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
) -> dict:
    """Assess risk for a single target file.

    Enriches each result with:
    - test_gap: bool — True when no test file matching this file's basename exists.
    - security_signals: list of {kind, severity, snippet} from security_findings.
    """
    repo_id = repository.id
    result_data: dict[str, Any] = {"target": target}

    dep_count = all_edge_map.get(target, 0)

    # Git metadata
    res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path == target,
        )
    )
    meta = res.scalar_one_or_none()

    if meta is None:
        result_data["hotspot_score"] = 0.0
        result_data["dependents_count"] = dep_count
        result_data["co_change_partners"] = []
        result_data["primary_owner"] = None
        result_data["owner_pct"] = None
        result_data["trend"] = "unknown"
        result_data["risk_type"] = "high-coupling" if dep_count >= 5 else "unknown"
        result_data["impact_surface"] = _compute_impact_surface(
            target,
            reverse_deps,
            node_meta,
        )
        result_data["test_gap"] = await _check_test_gap(session, repo_id, target)
        result_data["security_signals"] = await _get_security_signals(session, repo_id, target)
        result_data["risk_summary"] = f"{target} — no git metadata available"
        return result_data

    hotspot_score = meta.churn_percentile or 0.0

    # Co-change partners — keep only the top-5 by frequency. Larger lists make
    # MCP responses verbose without adding signal: top-5 captures the bulk of
    # the temporal-coupling mass and keeps tool output tight for LLM agents.
    partners = json.loads(meta.co_change_partners_json)
    partners_sorted = sorted(
        partners,
        key=lambda p: p.get("co_change_count", p.get("count", 0)) or 0,
        reverse=True,
    )[:5]
    import_related = import_links.get(target, set())
    co_changes = [
        {
            "file_path": p.get("file_path", p.get("path", "")),
            "count": p.get("co_change_count", p.get("count", 0)),
            "last_co_change": p.get("last_co_change"),
            "has_import_link": p.get("file_path", p.get("path", "")) in import_related,
        }
        for p in partners_sorted
    ]

    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0

    # --- Risk velocity (trend) ---
    trend = _compute_trend(meta)

    # --- Risk type classification ---
    risk_type = _classify_risk_type(meta, dep_count)

    # --- Impact surface ---
    impact_surface = _compute_impact_surface(target, reverse_deps, node_meta)

    # Phase 2: diff size & change magnitude
    lines_added = getattr(meta, "lines_added_90d", 0) or 0
    lines_deleted = getattr(meta, "lines_deleted_90d", 0) or 0
    avg_size = getattr(meta, "avg_commit_size", 0.0) or 0.0

    # Phase 2: commit classification → change_pattern
    categories = {}
    cat_json = getattr(meta, "commit_categories_json", None)
    if cat_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            categories = json.loads(cat_json)
    change_pattern = _derive_change_pattern(categories)

    # Phase 2: recent owner & bus factor
    recent_owner = getattr(meta, "recent_owner_name", None)
    recent_owner_pct = getattr(meta, "recent_owner_commit_pct", None)
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    contributor_count = getattr(meta, "contributor_count", 0) or 0

    # Phase 3: rename tracking & merge commit proxy
    original_path = getattr(meta, "original_path", None)
    merge_commit_count = getattr(meta, "merge_commit_count_90d", 0) or 0

    result_data["hotspot_score"] = hotspot_score
    result_data["dependents_count"] = dep_count
    result_data["co_change_partners"] = co_changes
    result_data["primary_owner"] = owner
    result_data["owner_pct"] = pct
    result_data["recent_owner"] = recent_owner
    result_data["recent_owner_pct"] = recent_owner_pct
    result_data["bus_factor"] = bus_factor
    result_data["contributor_count"] = contributor_count
    result_data["trend"] = trend
    result_data["risk_type"] = risk_type
    result_data["change_pattern"] = change_pattern
    result_data["change_magnitude"] = {
        "lines_added_90d": lines_added,
        "lines_deleted_90d": lines_deleted,
        "avg_commit_size": round(avg_size, 1),
    }
    result_data["impact_surface"] = impact_surface
    if original_path:
        result_data["original_path"] = original_path
    if merge_commit_count > 0:
        result_data["merge_commit_count_90d"] = merge_commit_count

    # C. Test gaps + security signals
    result_data["test_gap"] = await _check_test_gap(session, repo_id, target)
    result_data["security_signals"] = await _get_security_signals(session, repo_id, target)

    capped = getattr(meta, "commit_count_capped", False)
    capped_note = " (history truncated — actual count may be higher)" if capped else ""
    result_data["commit_count_capped"] = capped

    bus_note = ""
    if bus_factor == 1 and (meta.commit_count_total or 0) > 20:
        bus_note = f", bus factor risk (sole maintainer: {owner})"

    result_data["risk_summary"] = (
        f"{target} — hotspot score {hotspot_score:.0%} ({trend}), "
        f"{dep_count} dependents, {risk_type}, {change_pattern}, "
        f"{len(co_changes)} co-change partners, owned {pct:.0%} by {owner}"
        f"{bus_note}{capped_note}"
    )

    return result_data


@mcp.tool()
async def get_risk(
    targets: list[str],
    repo: str | None = None,
    changed_files: list[str] | None = None,
) -> dict:
    """Assess modification risk for one or more files before making changes.

    Pass ALL files you plan to modify in a single call. Returns per-file:
    - hotspot_score and trend ("increasing"/"stable"/"decreasing")
    - risk_type ("churn-heavy"/"bug-prone"/"high-coupling"/"stable")
    - impact_surface: top 3 critical modules that would break
    - dependents, co-change partners, ownership
    - test_gap: bool — True if no test file exists for this file
    - security_signals: list of {kind, severity, snippet} from static analysis

    Plus the top 5 global hotspots for ambient awareness.

    Pass ``changed_files`` for PR review / blast radius analysis. When provided,
    the response includes an additional ``pr_blast_radius`` key containing:
    - direct_risks: per-file risk score (centrality × temporal hotspot)
    - transitive_affected: files that import any changed file (up to depth 3)
    - cochange_warnings: historical co-change partners missing from the PR
    - recommended_reviewers: top 5 owners of affected files
    - test_gaps: changed/affected files lacking a corresponding test
    - overall_risk_score: 0-10 composite score

    Example: get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])

    Args:
        targets: List of file paths to assess (standard per-file risk).
        repo: Repository path, name, or ID.
        changed_files: Optional list of files changed in a PR for blast-radius analysis.
    """
    if repo == "all":
        return _unsupported_repo_all("get_risk")
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

        # Pre-load edges
        res = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
            )
        )
        all_edges = res.scalars().all()
        dep_counts: dict[str, int] = {}
        import_links: dict[str, set[str]] = {}
        reverse_deps: dict[str, set[str]] = {}  # target -> set of importers
        for e in all_edges:
            dep_counts[e.target_node_id] = dep_counts.get(e.target_node_id, 0) + 1
            import_links.setdefault(e.source_node_id, set()).add(e.target_node_id)
            import_links.setdefault(e.target_node_id, set()).add(e.source_node_id)
            reverse_deps.setdefault(e.target_node_id, set()).add(e.source_node_id)

        # Pre-load graph nodes for pagerank / impact surface
        node_res = await session.execute(
            select(GraphNode).where(GraphNode.repository_id == repo_id)
        )
        node_meta = {n.node_id: n for n in node_res.scalars().all()}

        # Assess each target
        results = await asyncio.gather(
            *[
                _assess_one_target(
                    session,
                    repository,
                    t,
                    dep_counts,
                    import_links,
                    reverse_deps,
                    node_meta,
                )
                for t in targets
            ]
        )

        # Global hotspots (excluding requested targets)
        target_set = set(targets)
        res = await session.execute(
            select(GitMetadata)
            .where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.is_hotspot == True,  # noqa: E712
            )
            .order_by(GitMetadata.churn_percentile.desc())
            .limit(len(targets) + 5)
        )
        all_hotspots = res.scalars().all()
        global_hotspots = [
            {
                "file_path": h.file_path,
                "hotspot_score": h.churn_percentile,
                "primary_owner": h.primary_owner_name,
            }
            for h in all_hotspots
            if h.file_path not in target_set
        ][:5]

        # A. PR blast radius (only when caller passes changed_files)
        pr_blast_radius: dict | None = None
        if changed_files:
            from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer

            analyzer = PRBlastRadiusAnalyzer(session, repo_id)
            pr_blast_radius = await analyzer.analyze_files(changed_files)

    # Cross-repo blast radius enrichment (Phase 3)
    enricher = _state._cross_repo_enricher
    if enricher is not None and enricher.has_data and _is_workspace_mode():
        for r in results:
            target = r["target"]
            cross_partners = enricher.get_cross_repo_partners(ctx.alias, target)
            affected_repos = enricher.get_affected_repos(ctx.alias, target)
            if cross_partners or affected_repos:
                r["cross_repo_impact"] = {
                    "cross_repo_consumers": [
                        {"repo": p["repo"], "file": p["file"], "strength": p["strength"]}
                        for p in cross_partners[:5]
                    ],
                    "affected_repos": affected_repos,
                }
                r["dependents_count"] = r.get("dependents_count", 0) + len(cross_partners)

    response: dict = {
        "targets": {r["target"]: r for r in results},
        "global_hotspots": global_hotspots,
    }
    if pr_blast_radius is not None:
        response["pr_blast_radius"] = pr_blast_radius
    return response
