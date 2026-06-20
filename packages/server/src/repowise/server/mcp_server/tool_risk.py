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


def _build_co_changes(meta: Any, import_related: set[str], exclude_spec: Any) -> list[dict]:
    """Top-5 co-change partners for *meta*, by frequency, with import-link flags.

    Larger lists make MCP responses verbose without adding signal: top-5 captures
    the bulk of the temporal-coupling mass and keeps tool output tight for agents.
    """
    partners = json.loads(meta.co_change_partners_json)
    partners_sorted = sorted(
        partners,
        key=lambda p: p.get("co_change_count", p.get("count", 0)) or 0,
        reverse=True,
    )[:5]
    return filter_dicts_by_key(
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


def _load_commit_categories(meta: Any) -> dict:
    """Parse the persisted commit-category counts, tolerating malformed JSON."""
    categories: dict = {}
    cat_json = getattr(meta, "commit_categories_json", None)
    if cat_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            categories = json.loads(cat_json)
    return categories


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

    co_changes = _build_co_changes(meta, import_links.get(target, set()), exclude_spec)

    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0

    # --- Risk velocity (trend) ---
    trend = _compute_trend(meta)

    # --- Risk type classification ---
    risk_type = _classify_risk_type(meta, dep_count, team_size)

    # --- Impact surface ---
    impact_surface = _compute_impact_surface(target, reverse_deps, node_meta, exclude_spec)

    # Phase 2: commit classification → change_pattern
    change_pattern = _derive_change_pattern(_load_commit_categories(meta))

    # Phase 2: recent owner & bus factor
    bus_factor = getattr(meta, "bus_factor", 0) or 0

    result_data["hotspot_score"] = hotspot_score
    result_data["dependents_count"] = dep_count
    result_data["co_change_partners"] = co_changes
    result_data["primary_owner"] = owner
    result_data["owner_pct"] = pct
    result_data["recent_owner"] = getattr(meta, "recent_owner_name", None)
    result_data["recent_owner_pct"] = getattr(meta, "recent_owner_commit_pct", None)
    result_data["bus_factor"] = bus_factor
    result_data["contributor_count"] = getattr(meta, "contributor_count", 0) or 0
    result_data["trend"] = trend
    result_data["risk_type"] = risk_type
    result_data["change_pattern"] = change_pattern
    result_data["change_magnitude"] = {
        "lines_added_90d": getattr(meta, "lines_added_90d", 0) or 0,
        "lines_deleted_90d": getattr(meta, "lines_deleted_90d", 0) or 0,
        "avg_commit_size": round(getattr(meta, "avg_commit_size", 0.0) or 0.0, 1),
    }
    result_data["impact_surface"] = impact_surface
    # Phase 3: rename tracking & merge commit proxy
    original_path = getattr(meta, "original_path", None)
    if original_path:
        result_data["original_path"] = original_path
    merge_commit_count = getattr(meta, "merge_commit_count_90d", 0) or 0
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


#: Caps on the cross-repo directive lists — kept tight so the PR directive stays
#: glanceable. The full impact set is on get_blast_radius / the REST endpoint.
_XR_WILL_BREAK_LIMIT = 5
_XR_COCHANGE_LIMIT = 3
#: Caps on the breaking-change directive — providers and consumers-per-provider.
#: The full report is on GET /api/workspace/breaking-changes.
_BC_PROVIDER_LIMIT = 5
_BC_CONSUMER_LIMIT = 5
#: Caps on the conformance directive — violations and cycles that touch the repo.
#: The full report is on GET /api/workspace/conformance.
_CF_VIOLATION_LIMIT = 5
_CF_CYCLE_LIMIT = 3


def _breaking_change_directive(repo_alias: str) -> list[dict[str, Any]]:
    """Breaking-change half of the PR directive: incompatible provider changes.

    Reads the persisted breaking-change report (current HEAD vs the previously
    indexed contracts), filtered to providers in the changed repo, and reports
    each change with the consumers it endangers across repos. Returns an empty
    list when not in workspace mode or no report is available. Never raises.
    """
    out: list[dict[str, Any]] = []
    try:
        if not _is_workspace_mode():
            return out
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_breaking_changes", False):
            return out
        for change in enricher.get_breaking_changes_for_repo(repo_alias):
            if len(out) >= _BC_PROVIDER_LIMIT:
                break
            consumers = change.get("impacted_consumers", [])
            # Only surface changes that actually endanger a cross-repo consumer —
            # an internal-only removed endpoint isn't a cross-repo break.
            cross = [c for c in consumers if c.get("repo") != repo_alias]
            if not cross:
                continue
            out.append(
                {
                    "contract_id": change.get("contract_id"),
                    "type": change.get("contract_type"),
                    "kind": change.get("kind"),
                    "severity": change.get("severity"),
                    "detail": change.get("detail"),
                    "impacted_consumers": [
                        {
                            "repo": c.get("repo"),
                            "service": c.get("service"),
                            "file": c.get("file"),
                        }
                        for c in cross[:_BC_CONSUMER_LIMIT]
                    ],
                }
            )
    except Exception:
        return []
    return out


def _conformance_directive(repo_alias: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Conformance half of the PR directive: architecture findings touching this repo.

    Reads the persisted conformance report (rule violations + dependency cycles
    over the system graph) and returns those that involve the changed repo, so a
    diff that participates in a denied dependency or a circular dependency is
    flagged. Returns two empty lists when not in workspace mode or no report is
    available. Never raises.
    """
    violations: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    try:
        if not _is_workspace_mode():
            return violations, cycles
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_conformance", False):
            return violations, cycles
        scoped = enricher.get_conformance_for_repo(repo_alias)
        for v in scoped.get("violations", [])[:_CF_VIOLATION_LIMIT]:
            violations.append(
                {
                    "source": v.get("source"),
                    "target": v.get("target"),
                    "rule": f"{v.get('rule_source')} !-> {v.get('rule_target')}",
                    "edge_kind": v.get("edge_kind"),
                    "description": v.get("rule_description") or None,
                }
            )
        for c in scoped.get("cycles", [])[:_CF_CYCLE_LIMIT]:
            cycles.append({"nodes": c.get("nodes", []), "length": c.get("length", 0)})
    except Exception:
        return [], []
    return violations, cycles


def _cross_repo_directive(repo_alias: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cross-repo half of the PR directive: downstream services in other repos.

    Resolves the changed repo to its system-graph nodes and ranks reachable
    services in OTHER repos by impact, splitting structural (``will_break``) from
    behavioral co-change (``missing_cochanges``). Returns two empty lists when
    not in workspace mode or no system graph is available. Never raises.
    """
    will_break_consumers: list[dict[str, Any]] = []
    missing_cross_repo_cochanges: list[dict[str, Any]] = []
    try:
        if not _is_workspace_mode():
            return will_break_consumers, missing_cross_repo_cochanges
        enricher = _state._cross_repo_enricher
        raw_graph = enricher.get_system_graph() if enricher is not None else None
        if not raw_graph:
            return will_break_consumers, missing_cross_repo_cochanges

        from repowise.core.workspace.blast_radius import cross_repo_blast_radius
        from repowise.core.workspace.system_graph import SystemGraph

        result = cross_repo_blast_radius(SystemGraph.from_dict(raw_graph), [repo_alias])
        for n in result.impacted:
            if n.repo == repo_alias:
                continue  # cross-repo only — intra-repo impact is the single-repo blast
            if n.structural:
                if len(will_break_consumers) < _XR_WILL_BREAK_LIMIT:
                    will_break_consumers.append(
                        {
                            "repo": n.repo,
                            "service": n.name,
                            "distance": n.distance,
                            "score": n.score,
                            "via": n.edge_kinds,
                        }
                    )
            elif len(missing_cross_repo_cochanges) < _XR_COCHANGE_LIMIT:
                missing_cross_repo_cochanges.append(
                    {"repo": n.repo, "service": n.name, "score": n.score}
                )
    except Exception:
        return [], []
    return will_break_consumers, missing_cross_repo_cochanges


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


async def _enrich_cross_repo(results: list[dict], alias: str) -> None:
    """Annotate per-target results with cross-repo partners, affected repos and
    contract links (workspace mode only). Mutates *results* in place; no-op when
    no enricher data is available. Behavior preserved verbatim from inline form.
    """
    enricher = _state._cross_repo_enricher
    if enricher is None or not enricher.has_data or not _is_workspace_mode():
        return
    for r in results:
        target = r["target"]
        cross_partners = enricher.get_cross_repo_partners(alias, target)
        affected_repos = enricher.get_affected_repos(alias, target)
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
        if not enricher.has_contract_data:
            continue
        provider_links = enricher.get_contract_links_as_provider(alias, target)
        consumer_links = enricher.get_contract_links_as_consumer(alias, target)
        if not (provider_links or consumer_links):
            continue
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


def _finalize_dep_summaries(results: list[dict]) -> None:
    """Rebuild risk_summary for any post-enrichment dependents_count change and
    drop the internal ``_base_dep_count`` key. Mutates *results* in place.
    """
    for r in results:
        base = r.pop("_base_dep_count", None)
        if base is not None and r.get("dependents_count", base) != base:
            r["risk_summary"] = r["risk_summary"].replace(
                f"{base} dependents",
                f"{r['dependents_count']} dependents",
            )


async def _enrich_health(results: list[dict], ctx: Any, repo_id: str) -> None:
    """Attach per-file health_score, coverage, and top_biomarkers from the health
    tables. Conservative: missing data → no field, never invented. Never raises.
    """
    try:
        from repowise.core.persistence.models import HealthFileMetric, HealthFinding

        target_paths = [r["target"] for r in results if r.get("target")]
        if not target_paths:
            return
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


async def _governance_directive(ctx: Any, changed_files: list[str]) -> list[dict[str, Any]]:
    """Governing decisions over *changed_files* that are stale, superseded, or
    contradicted. Bounded to 5 entries. Never raises (returns what it has).
    """
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
                    reason = _governance_reason(dr, conflict_decision_ids)
                    if reason is None:
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
    return governance_risk


def _governance_reason(dr: Any, conflict_decision_ids: set[str]) -> str | None:
    """Map a governing decision to a directive reason, or None when clean."""
    staleness = dr.staleness_score or 0.0
    if dr.status == "active" and staleness >= 0.5:
        return "stale_governance"
    if dr.status == "superseded":
        return "superseded_decision"
    if dr.id in conflict_decision_ids:
        return "contradicted_decision"
    return None


def _build_pr_directive(
    response: dict,
    pr_blast_radius: dict,
    changed_files: list[str],
    exclude_spec: Any,
    collector: OmissionCollector,
    governance_risk: list[dict[str, Any]],
    alias: str,
) -> None:
    """Assemble PR-mode output: trim co-change lists + blast radius, then build
    the directive block. Mutates *response* in place. Behavior preserved.
    """
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
    # Scope to the PR: the directive answers "what should I do about
    # THIS diff", so only changed files belong here. Repo-wide test
    # gaps stay available in pr_blast_radius.test_gaps for deeper
    # review — surfacing them in the directive made unrelated files
    # ("alembic/env.py has no tests") read as failings of the PR.
    # Read from the untrimmed analyzer payload: the trimmed list is
    # capped at 10 repo-wide entries and may have already dropped the
    # changed files we're looking for.
    changed_set = set(changed_files)
    missing_tests = filter_path_list(
        [
            p
            for p in (_as_path(e) for e in pr_blast_radius.get("test_gaps", []))
            if p and p in changed_set
        ],
        exclude_spec,
    )[:3]

    gov_count = len(governance_risk)
    gov_suffix = f" {gov_count} governance risk(s) detected." if gov_count > 0 else ""

    # Cross-repo directive (workspace mode only). Resolve the changed repo to
    # its system-graph nodes and walk reachability to find downstream
    # services in OTHER repos — split structural (will break) from behavioral
    # (co-change only). Repo-scoped: it answers "can this PR's repo break
    # something across a repo boundary?" using the same reachability the map
    # and get_blast_radius use.
    will_break_consumers, missing_cross_repo_cochanges = _cross_repo_directive(alias)
    xr_suffix = ""
    if will_break_consumers or missing_cross_repo_cochanges:
        xr_suffix = (
            f" Cross-repo: {len(will_break_consumers)} consumer service(s) may break, "
            f"{len(missing_cross_repo_cochanges)} cross-repo co-changer(s) missing."
        )

    # Breaking-change guard — incompatible provider changes (removed route /
    # field, type change, ...) in this repo and the consumers they endanger.
    # Schema-level truth, distinct from the topology-level will_break_consumers.
    breaking_changes = _breaking_change_directive(alias)
    bc_suffix = ""
    if breaking_changes:
        bc_consumers = sum(len(b["impacted_consumers"]) for b in breaking_changes)
        bc_suffix = (
            f" Breaking changes: {len(breaking_changes)} provider contract(s) changed "
            f"incompatibly, endangering {bc_consumers} consumer(s)."
        )

    # Architecture conformance — declared dependency-rule violations and
    # dependency cycles this repo participates in. Governance-level truth,
    # distinct from the topology / schema directives above.
    conformance_violations, dependency_cycles = _conformance_directive(alias)
    cf_suffix = ""
    if conformance_violations or dependency_cycles:
        cf_suffix = (
            f" Conformance: {len(conformance_violations)} architecture rule "
            f"violation(s), {len(dependency_cycles)} dependency cycle(s) involving "
            f"this repo."
        )

    response["directive"] = {
        "will_break": will_break,
        "missing_cochanges": missing_cochanges,
        "missing_tests": missing_tests,
        "will_break_consumers": will_break_consumers,
        "missing_cross_repo_cochanges": missing_cross_repo_cochanges,
        "breaking_changes": breaking_changes,
        "conformance_violations": conformance_violations,
        "dependency_cycles": dependency_cycles,
        "governance_risk": governance_risk,
        "overall_risk_score": trimmed_blast.get("overall_risk_score"),
        "summary": (
            f"PR touches {len(changed_files)} file(s). "
            f"~{len(will_break)} downstream file(s) likely affected, "
            f"{len(missing_cochanges)} historical co-changer(s) missing, "
            f"{len(missing_tests)} file(s) without tests."
            f"{gov_suffix}{xr_suffix}{bc_suffix}{cf_suffix}"
        ),
    }


@mcp.tool()
async def get_risk(
    targets: list[str],
    repo: str | None = None,
    changed_files: list[str] | None = None,
) -> dict:
    """What history says about touching these files — churn, owners, blast radius.

    Fuses git temporal signals (churn percentile, trend, bus factor) with
    graph topology (dependents, co-changes, impact surface) and security
    findings. Consult before editing 95th+ churn-percentile files. Pass
    changed_files for PR mode: the response leads with a directive block
    (will_break, missing_cochanges, missing_tests) — read it first.

    Args:
        targets: file paths to assess.
        repo: usually omitted.
        changed_files: PR-changed files for blast-radius mode.
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
    await _enrich_cross_repo(results, ctx.alias)

    # Final risk_summary rebuild for any remaining dependents_count updates
    # (e.g. contract provider links) and cleanup of internal keys.
    _finalize_dep_summaries(results)

    # ---- Code-health enrichment --------------------------------------------
    # Attach per-file health_score + top_biomarkers (up to 3) drawn from the
    # health tables. Conservative: missing data → no field, never invented.
    await _enrich_health(results, ctx, repo_id)

    response: dict = {
        "targets": {r["target"]: r for r in results},
    }

    collector = OmissionCollector("get_risk", repo_root=ctx.path)
    if pr_blast_radius is not None:
        # Governance risk — bounded query over changed_files (small set).
        governance_risk = await _governance_directive(ctx, changed_files)
        _build_pr_directive(
            response,
            pr_blast_radius,
            changed_files,
            exclude_spec,
            collector,
            governance_risk,
            ctx.alias,
        )
    else:
        # Standard per-file risk request (no diff) — keep global hotspots as
        # ambient awareness. Cheap (≤5 entries) and useful for orientation.
        response["global_hotspots"] = global_hotspots

    response["_meta"] = _build_meta(repository=repository)
    collector.attach(response)
    return response
