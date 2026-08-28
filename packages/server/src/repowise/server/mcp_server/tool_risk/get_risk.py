"""MCP Tool 3: get_risk — modification risk assessment (orchestrator)."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from repowise.core.analysis.risk_semantics import file_risk_scales
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
)
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._episodes import enrich_episode_counts as _enrich_episodes
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    attach_ignored_arguments,
    filter_path_list,
    filter_rows_by_attr,
    resolve_enum_argument,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

from .assessment import _assess_one_target, _get_active_contributor_count, fix_annotation
from .directives import _build_pr_directive, _governance_directive
from .enrichment import _enrich_cross_repo, _enrich_health

#: Fields an agent cannot rank or act on: uncalibrated pagerank floats, and
#: labels derived from numbers already printed beside them. Computed either way
#: (``risk_summary`` reads them); ``include`` only decides whether they ship.
_TARGET_CARD_INCLUDES: dict[str, tuple[str, ...]] = {
    "graph": (
        "impact_surface",
        "impact_surface_total",
        "impact_surface_emitted",
        "impact_surface_truncated",
        "impact_surface_reduced_reason",
        "impact_surface_omitted",
        "dependents",
        "dependents_total",
        "dependents_emitted",
        "dependents_truncated",
        "dependents_reduced_reason",
        "dependents_omitted",
        "direct_dependents_total",
        "transitive_dependents_total",
        "consumers",
        "consumers_total",
        "consumers_emitted",
        "consumers_truncated",
        "consumers_reduced_reason",
        "consumers_omitted",
        "cross_repo_links",
        "cross_repo_links_total",
        "cross_repo_links_emitted",
        "cross_repo_links_truncated",
        "cross_repo_links_reduced_reason",
        "cross_repo_links_omitted",
        "relationship_analysis",
    ),
    "churn": ("change_magnitude", "risk_type", "change_pattern"),
}
_BLAST_INCLUDES: dict[str, tuple[str, ...]] = {"graph": ("direct_risks",)}
#: Per-field units and calibration. Identical on every call, so it is opt-in.
_INCLUDE_BLOCKS = frozenset(_TARGET_CARD_INCLUDES) | frozenset(_BLAST_INCLUDES) | {"scales"}


def _drop_opt_in_blocks(response: dict, include: set[str]) -> None:
    """Strip the opt-in fields no ``include`` key asked for."""
    cards = list(response.get("targets", {}).values())
    blast = response.get("pr_blast_radius") or {}
    for source, keys_by_block in ((cards, _TARGET_CARD_INCLUDES), ([blast], _BLAST_INCLUDES)):
        for block, keys in keys_by_block.items():
            if block in include:
                continue
            for holder in source:
                for key in keys:
                    holder.pop(key, None)


@mcp.tool(
    surface_order=50,
    recipes=(
        ToolRecipe(
            "assess_hotspot",
            'get_risk(targets=["path"])',
            ("get_risk",),
        ),
        ToolRecipe(
            "review_change",
            'get_risk(targets=["path"], changed_files=["path"])',
            ("get_risk",),
        ),
    ),
)
async def get_risk(
    targets: list[str],
    repo: str | None = None,
    changed_files: list[str] | None = None,
    include: list[str] | None = None,
) -> dict:
    """What history says about touching these files — bug fixes, churn, owners.

    Fuses git temporal signals (``hotspot_score``/``owner_pct`` are 0-1; trend;
    bus factor) with graph topology. ``dependents`` are directed structural
    reach (source depends on target), ``consumers`` require typed contract links,
    and ``co_change_partners`` are historical correlation only. Structural reach
    is not proof of runtime breakage. The response also includes security
    findings. Pass changed_files for PR mode: the response leads with a
    directive block (may_break, missing_cochanges, missing_tests,
    tests_to_run) — read it first. Each test_recommendations row carries a
    measured or inferred basis, and coverage availability is explicit. To
    score a commit or ``base..head`` range instead, use ``get_change_risk``.

    In PR mode ``structural_impact_score`` is an uncalibrated 0-10 structural
    heuristic, never a runtime-breakage probability; ``overall_risk_score`` is
    its deprecated exact alias.

    Default responses fit 24,000 serialized chars; nonempty ``include`` uses
    32,000. Reductions carry counts and ``_meta.omitted`` recovery refs;
    ``_meta.recovery_unavailable`` names a storage failure.
    Include-gated blocks are projections, not omissions.

    Args:
        targets: file paths to assess.
        repo: usually omitted.
        changed_files: PR-changed files for blast-radius mode.
        include: opt-in blocks - "graph", "churn", "scales" (units and
            calibration for every scalar; identical per call, so ask once).
    """
    if repo == "all":
        return _unsupported_repo_all("get_risk")
    ignored: list[dict] = []
    include_set = {
        block
        for block in (include or [])
        if resolve_enum_argument(block, _INCLUDE_BLOCKS, argument="include", ignored=ignored)
    }
    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_risk", repo_root=ctx.path)
    exclude_spec = _get_exclude_spec(ctx.path)
    targets = filter_path_list(targets, exclude_spec)
    if changed_files:
        changed_files = filter_path_list(changed_files, exclude_spec)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

        # File-node endpoints and the positive dependency vocabulary are both
        # required: an untyped or symbol edge must not become a file dependent.
        node_res = await session.execute(
            select(GraphNode).where(GraphNode.repository_id == repo_id)
        )
        node_meta = {n.node_id: n for n in node_res.scalars().all()}
        file_node_ids = {node_id for node_id, node in node_meta.items() if node.node_type == "file"}

        # Pre-load edges. Dependency edges only: everything below reads these as
        # "X depends on Y", and the graph also carries containment and co-change
        # edges. Leaving co_changes in made the relation circular: a co-change
        # partner was fed back in as an import link, so every partner that
        # cleared the count floor was annotated ``(imports)``, including the
        # markdown and JSON files that are graph nodes but import nothing.
        res = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.edge_type.in_(FILE_DEPENDENCY_EDGE_TYPES),
                GraphEdge.source_node_id.in_(file_node_ids),
                GraphEdge.target_node_id.in_(file_node_ids),
            )
        )
        all_edges = res.scalars().all()
        import_links: dict[str, dict[str, set[str]]] = {}
        reverse_deps: dict[str, dict[str, set[str]]] = {}
        for e in all_edges:
            edge_type = str(e.edge_type)
            import_links.setdefault(e.source_node_id, {}).setdefault(e.target_node_id, set()).add(
                edge_type
            )
            import_links.setdefault(e.target_node_id, {}).setdefault(e.source_node_id, set()).add(
                edge_type
            )
            reverse_deps.setdefault(e.target_node_id, {}).setdefault(e.source_node_id, set()).add(
                edge_type
            )
        # Count unique incoming dependent nodes, not parallel edge rows. A file
        # with both an import and a type-use edge is still one direct dependent.
        dep_counts = {target: len(sources) for target, sources in reverse_deps.items()}

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
                    collector,
                    "graph" in include_set,
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
        global_hotspots = []
        if len(targets) > 1 and not changed_files:
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
            )
            all_hotspots = filter_rows_by_attr(
                list(res.scalars().all()), "file_path", exclude_spec
            )
            for h in all_hotspots:
                if h.file_path in target_set:
                    continue
                entry = {
                    "file_path": h.file_path,
                    "hotspot_score": h.churn_percentile,
                    "is_hotspot": True,
                    "primary_owner": h.primary_owner_name,
                }
                fixes = fix_annotation(h)
                if fixes is not None:
                    entry.update(fixes)
                global_hotspots.append(entry)

        # A. PR blast radius (only when caller passes changed_files)
        pr_blast_radius: dict | None = None
        if changed_files:
            from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer

            analyzer = PRBlastRadiusAnalyzer(session, repo_id, repository_alias=ctx.alias)
            pr_blast_radius = await analyzer.analyze_files(changed_files, exclude_spec=exclude_spec)

    # Cross-repo blast radius enrichment (Phase 3 + 4)
    await _enrich_cross_repo(
        results, ctx.alias, collector, include_graph="graph" in include_set
    )

    # ---- Code-health enrichment --------------------------------------------
    # Attach per-file health_score + top_biomarkers (up to 3) drawn from the
    # health tables. Conservative: missing data → no field, never invented.
    await _enrich_health(results, ctx, repo_id)

    # ---- Precedent enrichment ----------------------------------------------
    # One integer per target: how many dated episodes are bound here. A number
    # invites a follow-up get_why; a paragraph would spend the budget of every
    # caller that only wanted the risk card. Absent rather than zero.
    await asyncio.to_thread(_enrich_episodes, results, ctx.path)

    response: dict = {
        "targets": {r["target"]: r for r in results},
        **({"risk_scales": file_risk_scales()} if "scales" in include_set else {}),
    }

    if pr_blast_radius is not None:
        assert changed_files is not None
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
            full_scale="scales" in include_set,
        )
        # Dict insertion order is the serialized external order. PR mode is
        # action-first by contract, so the directive must precede dossiers,
        # targets, metadata, and omission details in the exact payload.
        response = {"directive": response.pop("directive"), **response}
    elif len(targets) > 1:
        # Standard per-file risk request (no diff) — ambient orientation across
        # a set of targets. On one file the caller already named, it is noise.
        cap_collection(
            response,
            "global_hotspots",
            global_hotspots,
            5,
            collector,
            label="global_hotspots beyond cap=5",
        )

    response["_meta"] = _build_meta(
        repository=repository,
        targets=[*targets, *(changed_files or [])] if targets or changed_files else None,
    )
    _drop_opt_in_blocks(response, include_set)
    attach_ignored_arguments(response, ignored)
    collector.attach(response)
    return response
