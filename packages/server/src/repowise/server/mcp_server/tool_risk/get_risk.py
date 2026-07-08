"""MCP Tool 3: get_risk — modification risk assessment (orchestrator)."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    filter_path_list,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

from .assessment import _assess_one_target, _get_active_contributor_count
from .directives import _build_pr_directive, _governance_directive
from .enrichment import _enrich_cross_repo, _enrich_health, _finalize_dep_summaries


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
