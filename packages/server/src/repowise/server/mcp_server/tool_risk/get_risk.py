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

from .assessment import _assess_one_target, _get_active_contributor_count, fix_annotation
from .directives import _build_pr_directive, _governance_directive
from .enrichment import _enrich_cross_repo, _enrich_health, _finalize_dep_summaries


@mcp.tool()
async def get_risk(
    targets: list[str],
    repo: str | None = None,
    changed_files: list[str] | None = None,
) -> dict:
    """What history says about touching these files — bug fixes, churn, owners.

    Fuses git temporal signals (churn percentile, trend, bus factor) with
    graph topology (dependents, co-changes, impact surface) and security
    findings. Consult before editing a file that is bug-fixed or busy. Pass
    changed_files for PR mode: the response leads with a directive block
    (will_break, missing_cochanges, missing_tests, tests_to_run) — read it
    first. tests_to_run is coverage-backed: the tests the per-test map proves
    exercise the changed files, empty when no coverage map is ingested.
    To score a live commit or ``base..head`` diff by revspec instead of file
    paths, use ``get_change_risk``.

    defect_profile appears only on files with counted bug fixes: how many landed
    in the trailing 6 months, how long ago the last one was, a bug_magnet flag
    for sustained recent fix pressure, and top_symbols. Those per-symbol counts
    are approximate, because symbol spans are current-tree while each fix's line
    ranges are numbered on its own parent commit, so read them as "mostly here"
    rather than exact. Nothing here names the commit that introduced a bug.
    global_hotspots ranks the same way: fix history first, churn as fallback.

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
        test_paths = {nid for nid, n in node_meta.items() if n.is_test}

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

        # Elsewhere-in-the-repo attention list (excluding requested targets).
        # Ranked on bug-fix history first, churn second. This list sits beside
        # per-target verdicts that already read "bug-prone" off counted fixes,
        # so ranking it purely on churn made the two halves of one response
        # disagree about what deserves attention. Admitting bug magnets matters
        # as much as the ordering: filtering on is_hotspot alone means a file
        # fixed four times last month that is not busy can never appear.
        # Churn stays the fallback, so a repo with no fix convention keeps
        # exactly the list it had. These are full ORM rows, so the fix columns
        # are already in memory and this adds no query.
        target_set = set(targets)
        res = await session.execute(
            select(GitMetadata)
            .where(
                GitMetadata.repository_id == repo_id,
                (GitMetadata.is_hotspot == True)  # noqa: E712
                | (GitMetadata.bug_magnet == True),  # noqa: E712
            )
            .order_by(
                GitMetadata.bug_magnet.desc(),
                GitMetadata.fix_mass.desc(),
                GitMetadata.churn_percentile.desc(),
            )
            .limit(len(targets) + 5)
        )
        all_hotspots = filter_rows_by_attr(list(res.scalars().all()), "file_path", exclude_spec)
        global_hotspots = []
        for h in all_hotspots:
            if h.file_path in target_set:
                continue
            entry = {
                "file_path": h.file_path,
                "hotspot_score": h.churn_percentile,
                "primary_owner": h.primary_owner_name,
            }
            # Silent on files with no counted fixes, so a repo without fix
            # history pays nothing for this.
            fixes = fix_annotation(h)
            if fixes is not None:
                entry.update(fixes)
            global_hotspots.append(entry)
        global_hotspots = global_hotspots[:5]

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
            test_paths,
            ctx.alias,
        )
    else:
        # Standard per-file risk request (no diff) — keep global hotspots as
        # ambient awareness. Cheap (≤5 entries) and useful for orientation.
        response["global_hotspots"] = global_hotspots

    response["_meta"] = _build_meta(
        repository=repository,
        targets=[*targets, *(changed_files or [])] if targets or changed_files else None,
    )
    collector.attach(response)
    return response
