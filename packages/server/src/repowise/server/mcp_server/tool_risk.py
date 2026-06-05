"""MCP Tool 3: get_risk — modification risk assessment."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.database import get_session
from repowise.core.persistence.decision_graph import get_governing_decisions, list_conflict_edges
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
    Repository,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _is_workspace_mode,
    _resolve_repo_context,
    _unsupported_repo_all,
    filter_dicts_by_key,
    filter_path_list,
    filter_rows_by_attr,
    is_excluded,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

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


def _classify_risk_type(meta: Any, dep_count: int, team_size: int | None = None) -> str:
    """Classify risk as churn-heavy, bug-prone, high-coupling, or bus-factor-risk.

    *team_size* is the repo's active-contributor count (90d). On a small
    team (≤ SMALL_TEAM_MAX_CONTRIBUTORS) a single-author file is the
    expected operating model, so ``bus-factor-risk`` is reserved for
    hotspot-active files there (issue #361). ``None`` = unknown → keep
    the historical behaviour.
    """
    from repowise.core.analysis.health.biomarkers.base import SMALL_TEAM_MAX_CONTRIBUTORS

    # Count bug-fix commits from significant_commits messages
    commits = json.loads(meta.significant_commits_json) if meta.significant_commits_json else []
    fix_count = sum(1 for c in commits if _FIX_PATTERN.search(c.get("message", "")))

    churn_score = meta.churn_percentile or 0.0
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    total_commits = meta.commit_count_total or 0

    small_team = team_size is not None and team_size <= SMALL_TEAM_MAX_CONTRIBUTORS

    # Bug-prone takes priority if fix ratio is high
    if commits and fix_count / len(commits) >= 0.4:
        return "bug-prone"
    if churn_score >= 0.7:
        return "churn-heavy"
    if (
        bus_factor == 1
        and total_commits > 20
        and (not small_team or bool(getattr(meta, "is_hotspot", False)))
    ):
        return "bus-factor-risk"
    if dep_count >= 5:
        return "high-coupling"
    return "stable"


async def _get_active_contributor_count(session: AsyncSession, repo_id: str) -> int | None:
    """Repo-wide active-contributor count from persisted git metadata.

    Reuses ``count_active_contributors`` (per-author ``last_commit_ts`` in
    ``top_authors_json``) over all rows. ``None`` = unknown (no rows, or an
    index that predates per-author timestamps).
    """
    from repowise.core.ingestion.git_indexer import count_active_contributors

    try:
        rows = await session.execute(
            select(GitMetadata.top_authors_json).where(GitMetadata.repository_id == repo_id)
        )
        metas = [{"top_authors_json": r[0]} for r in rows.all() if r[0]]
        if not metas:
            return None
        return count_active_contributors(metas)
    except Exception:
        return None


def _compute_impact_surface(
    target: str,
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
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
    ranked = filter_dicts_by_key(ranked, "file_path", exclude_spec)
    return ranked[:3]


async def _check_test_gap(session: AsyncSession, repo_id: str, target: str) -> bool:
    """Return True if no test file corresponding to *target* exists in graph_nodes.

    Test files themselves (is_test=True) are never considered to have a test gap.
    """
    import os

    # Test files don't need tests — skip the check entirely
    node_res = await session.execute(
        select(GraphNode.is_test)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id == target,
        )
        .limit(1)
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
            select(GraphNode)
            .where(
                GraphNode.repository_id == repo_id,
                GraphNode.is_test == True,  # noqa: E712
                GraphNode.node_id.like(pat),
            )
            .limit(1)
        )
        if res.scalar_one_or_none() is not None:
            return False
    return True


async def _get_security_signals(session: AsyncSession, repo_id: str, target: str) -> list[dict]:
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
        return [{"kind": r[0], "severity": r[1], "snippet": r[2]} for r in rows.all()]
    except Exception:
        return []


async def _assess_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    all_edge_map: dict[str, int],
    import_links: dict[str, set[str]],
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
    team_size: int | None = None,
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
            exclude_spec,
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
    co_changes = filter_dicts_by_key(
        [
            {
                "file_path": p.get("file_path", p.get("path", "")),
                "count": p.get("co_change_count", p.get("count", 0)),
                "last_co_change": p.get("last_co_change"),
                "has_import_link": p.get("file_path", p.get("path", "")) in import_related,
            }
            for p in partners_sorted
        ],
        "file_path",
        exclude_spec,
    )

    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0

    # --- Risk velocity (trend) ---
    trend = _compute_trend(meta)

    # --- Risk type classification ---
    risk_type = _classify_risk_type(meta, dep_count, team_size)

    # --- Impact surface ---
    impact_surface = _compute_impact_surface(target, reverse_deps, node_meta, exclude_spec)

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

    # NOTE: risk_summary is built here but dependents_count may be updated
    # later by cross-repo enrichment. We store dep_count now and let the
    # outer function rebuild the summary after enrichment if needed.
    result_data["_base_dep_count"] = dep_count
    result_data["risk_summary"] = (
        f"{target} — hotspot score {hotspot_score:.0%} ({trend}), "
        f"{dep_count} dependents, {risk_type}, {change_pattern}, "
        f"{len(co_changes)} co-change partners, owned {pct:.0%} by {owner}"
        f"{bus_note}{capped_note}"
    )

    return result_data


def _as_path(entry: Any) -> str | None:
    """Best-effort file path from a blast-radius list entry (str or dict)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return (
            entry.get("file_path")
            or entry.get("path")
            or entry.get("file")
            or entry.get("missing_partner")
            or entry.get("partner")
        )
    return None


def _trim_blast_lists(
    pr_blast_radius: dict[str, Any],
    exclude_spec: Any,
    collector: OmissionCollector | None = None,
) -> dict[str, Any]:
    """Cap the noisy ``pr_blast_radius`` lists, capturing what gets dropped.

    ``pr_blast_radius`` is the analyzer's own payload — preserve it for
    callers that want the full picture, but drop excluded paths and truncate
    the noisy lists so we stay well under the 25k-token transport ceiling on
    PRs that touch many files. With a *collector*, every entry trimmed for
    size is persisted to the omission store (excluded paths are not — they
    are filtered by policy, not budget).
    """
    trimmed_blast: dict[str, Any] = dict(pr_blast_radius)
    for key, cap in (
        ("transitive_affected", 15),
        ("cochange_warnings", 10),
        ("test_gaps", 10),
        ("recommended_reviewers", 5),
    ):
        value = trimmed_blast.get(key)
        if not isinstance(value, list):
            continue
        if exclude_spec:
            value = [e for e in value if not is_excluded(_as_path(e), exclude_spec)]
            trimmed_blast[key] = value
        if len(value) > cap:
            trimmed_blast[key] = value[:cap]
            trimmed_blast[f"{key}_truncated_total"] = len(value)
            if collector is not None:
                collector.add(
                    f"pr_blast_radius.{key} beyond cap={cap} ({len(value) - cap} dropped)",
                    value[cap:],
                )
    return trimmed_blast


@mcp.tool()
async def get_risk(
    targets: list[str],
    repo: str | None = None,
    changed_files: list[str] | None = None,
) -> dict:
    """What history says about touching these files — hotspot, churn, owners, blast radius.

    The only tool that fuses git temporal signals (churn percentile, trend,
    bus factor) with graph topology (dependents, co-changes, impact surface)
    and security findings into one decision-shaped payload. Consult before
    editing any file in the 95th+ churn percentile or before merging a
    multi-file PR.

    Per-file fields: ``hotspot_score``, ``trend``, ``risk_type``,
    ``impact_surface`` (top 3), ``dependents_count``, ``co_change_partners``,
    ``primary_owner``, ``bus_factor``, ``test_gap``, ``security_signals``.

    Pass ``changed_files`` for PR-blast-radius mode. The response then carries
    a ``directive`` block — three short lists the caller can read in one
    glance (``will_break``, ``missing_cochanges``, ``missing_tests``) — plus
    the trimmed full ``pr_blast_radius`` dossier for deeper review. Global
    hotspots are omitted in PR mode (irrelevant to the diff) and re-included
    when ``changed_files`` is absent.

    In workspace mode, cross-repo consumers and API contract links bump the
    dependents count and appear in ``cross_repo_impact``.

    Example: get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])

    Args:
        targets: List of file paths to assess (standard per-file risk).
        repo: Repository path, name, or ID.
        changed_files: Optional list of files changed in a PR for blast-radius analysis.
    """
    if repo == "all":
        return _unsupported_repo_all("get_risk")
    ctx = await _resolve_repo_context(repo)
    exclude_spec = _get_exclude_spec(ctx.path)
    targets = filter_path_list(targets, exclude_spec)
    if changed_files:
        changed_files = filter_path_list(changed_files, exclude_spec)
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

        # Team size is repo-wide — compute once, share across targets
        # (small-team calibration for bus-factor-risk, issue #361).
        team_size = await _get_active_contributor_count(session, repo_id)

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
                    exclude_spec,
                    team_size,
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
        all_hotspots = filter_rows_by_attr(list(res.scalars().all()), "file_path", exclude_spec)
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

    # Cross-repo blast radius enrichment (Phase 3 + 4)
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
                # Rebuild risk_summary with updated dependents count
                if "_base_dep_count" in r:
                    r["risk_summary"] = r["risk_summary"].replace(
                        f"{r['_base_dep_count']} dependents",
                        f"{r['dependents_count']} dependents",
                    )

            # Contract links (Phase 4)
            if enricher.has_contract_data:
                provider_links = enricher.get_contract_links_as_provider(ctx.alias, target)
                consumer_links = enricher.get_contract_links_as_consumer(ctx.alias, target)
                if provider_links or consumer_links:
                    impact = r.setdefault("cross_repo_impact", {})
                    if provider_links:
                        impact["contract_consumers"] = [
                            {
                                "consumer_repo": lk["consumer_repo"],
                                "consumer_file": lk["consumer_file"],
                                "contract_id": lk["contract_id"],
                                "type": lk["contract_type"],
                            }
                            for lk in provider_links[:5]
                        ]
                        r["dependents_count"] = r.get("dependents_count", 0) + len(provider_links)
                    if consumer_links:
                        impact["contract_providers"] = [
                            {
                                "provider_repo": lk["provider_repo"],
                                "provider_file": lk["provider_file"],
                                "contract_id": lk["contract_id"],
                                "type": lk["contract_type"],
                            }
                            for lk in consumer_links[:5]
                        ]

    # Final risk_summary rebuild for any remaining dependents_count updates
    # (e.g. contract provider links) and cleanup of internal keys.
    for r in results:
        base = r.pop("_base_dep_count", None)
        if base is not None and r.get("dependents_count", base) != base:
            r["risk_summary"] = r["risk_summary"].replace(
                f"{base} dependents",
                f"{r['dependents_count']} dependents",
            )

    # ---- Code-health enrichment --------------------------------------------
    # Attach per-file health_score + top_biomarkers (up to 3) drawn from the
    # health tables. Conservative: missing data → no field, never invented.
    try:
        from repowise.core.persistence.models import HealthFileMetric, HealthFinding

        target_paths = [r["target"] for r in results if r.get("target")]
        if target_paths:
            async with get_session(ctx.session_factory) as _h_session:
                m_res = await _h_session.execute(
                    select(HealthFileMetric).where(
                        HealthFileMetric.repository_id == repo_id,
                        HealthFileMetric.file_path.in_(target_paths),
                    )
                )
                metric_map = {m.file_path: m for m in m_res.scalars().all()}

                f_res = await _h_session.execute(
                    select(HealthFinding)
                    .where(
                        HealthFinding.repository_id == repo_id,
                        HealthFinding.file_path.in_(target_paths),
                        HealthFinding.status == "open",
                    )
                    .order_by(HealthFinding.health_impact.desc())
                )
                top_by_file: dict[str, list[dict]] = {}
                for f in f_res.scalars().all():
                    lst = top_by_file.setdefault(f.file_path, [])
                    if len(lst) >= 3:
                        continue
                    lst.append(
                        {
                            "biomarker_type": f.biomarker_type,
                            "severity": f.severity,
                            "function_name": f.function_name,
                            "impact": round(f.health_impact, 2),
                        }
                    )

            for r in results:
                path = r.get("target")
                m = metric_map.get(path)
                if m is not None:
                    r["health_score"] = round(m.score, 2)
                    if m.line_coverage_pct is not None:
                        r["coverage_pct"] = round(m.line_coverage_pct, 2)
                    if m.branch_coverage_pct is not None:
                        r["branch_coverage_pct"] = round(m.branch_coverage_pct, 2)
                if path in top_by_file:
                    r["top_biomarkers"] = top_by_file[path]
    except Exception:
        pass

    response: dict = {
        "targets": {r["target"]: r for r in results},
    }

    collector = OmissionCollector("get_risk", repo_root=ctx.path)
    if pr_blast_radius is not None:
        # PR mode — drop global_hotspots (irrelevant to a specific diff), trim
        # per-target co-change lists, and synthesize a tight directive the
        # agent can act on without parsing the whole blast-radius dossier.
        # Everything trimmed below is persisted via the collector so the
        # response carries an expandable [repowise#<ref>] marker for it.
        for r in response["targets"].values():
            partners = r.get("co_change_partners") or []
            if len(partners) > 3:
                r["co_change_partners"] = partners[:3]
                collector.add(
                    f"{r.get('target')} :: co_change_partners beyond 3",
                    partners[3:],
                )

        trimmed_blast = _trim_blast_lists(pr_blast_radius, exclude_spec, collector)
        response["pr_blast_radius"] = trimmed_blast

        # Directive: 3 short lists the agent can read in one glance. Each
        # entry is a file path (string), never a dossier. Designed to answer
        # "what should I do about this PR" in three lines.

        will_break = filter_path_list(
            [p for p in (_as_path(e) for e in trimmed_blast.get("transitive_affected", [])) if p],
            exclude_spec,
        )[:5]
        missing_cochanges = filter_path_list(
            [p for p in (_as_path(e) for e in trimmed_blast.get("cochange_warnings", [])) if p],
            exclude_spec,
        )[:3]
        missing_tests = filter_path_list(
            [p for p in (_as_path(e) for e in trimmed_blast.get("test_gaps", [])) if p],
            exclude_spec,
        )[:3]

        # Governance risk — bounded query over changed_files (small set).
        governance_risk: list[dict[str, Any]] = []
        try:
            async with get_session(ctx.session_factory) as _gr_session:
                _gr_repo = await _get_repo(_gr_session)
                _gr_repo_id = _gr_repo.id
                conflict_edges = await list_conflict_edges(_gr_session, _gr_repo_id)
                conflict_decision_ids: set[str] = set()
                for ce in conflict_edges:
                    conflict_decision_ids.add(ce.src_decision_id)
                    conflict_decision_ids.add(ce.dst_decision_id)
                seen_dr_ids: set[str] = set()
                for cf in changed_files:
                    for dr in await get_governing_decisions(_gr_session, _gr_repo_id, cf):
                        if dr.id in seen_dr_ids:
                            continue
                        seen_dr_ids.add(dr.id)
                        staleness = dr.staleness_score or 0.0
                        is_stale = dr.status == "active" and staleness >= 0.5
                        is_superseded = dr.status == "superseded"
                        is_conflicted = dr.id in conflict_decision_ids
                        if is_stale:
                            reason = "stale_governance"
                        elif is_superseded:
                            reason = "superseded_decision"
                        elif is_conflicted:
                            reason = "contradicted_decision"
                        else:
                            continue
                        governance_risk.append(
                            {
                                "file": cf,
                                "decision_id": dr.id,
                                "title": dr.title,
                                "status": dr.status,
                                "reason": reason,
                            }
                        )
                        if len(governance_risk) >= 5:
                            break
                    if len(governance_risk) >= 5:
                        break
        except Exception:
            pass

        gov_count = len(governance_risk)
        gov_suffix = f" {gov_count} governance risk(s) detected." if gov_count > 0 else ""
        response["directive"] = {
            "will_break": will_break,
            "missing_cochanges": missing_cochanges,
            "missing_tests": missing_tests,
            "governance_risk": governance_risk,
            "overall_risk_score": trimmed_blast.get("overall_risk_score"),
            "summary": (
                f"PR touches {len(changed_files)} file(s). "
                f"~{len(will_break)} downstream file(s) likely affected, "
                f"{len(missing_cochanges)} historical co-changer(s) missing, "
                f"{len(missing_tests)} file(s) without tests."
                f"{gov_suffix}"
            ),
        }
    else:
        # Standard per-file risk request (no diff) — keep global hotspots as
        # ambient awareness. Cheap (≤5 entries) and useful for orientation.
        response["global_hotspots"] = global_hotspots

    response["_meta"] = _build_meta(repository=repository)
    collector.attach(response)
    return response
