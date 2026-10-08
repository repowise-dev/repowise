"""The ``get_risk`` MCP tool: modification risk for files and PR change sets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, NamedTuple

from sqlalchemy import func, select

from repowise.core.analysis.risk_semantics import file_risk_scales
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.batches import chunked
from repowise.core.persistence.crud import get_graph_nodes_by_ids, get_test_file_paths
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
)
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.core.support_paths import is_doc_or_config_path
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._episodes import enrich_episode_counts as _enrich_episodes
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    attach_ignored_arguments,
    drop_echoed_target,
    filter_path_list,
    filter_rows_by_attr,
    resolve_enum_argument,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

from .assessment import (
    _assess_one_target,
    _get_active_contributor_count,
    fix_annotation,
    normalize_target_path,
)
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
    "owners": (
        "owner_pct",
        "owner_line_pct",
        "recent_owner",
        "recent_owner_pct",
        "bus_factor",
        "contributor_count",
    ),
}
_BLAST_INCLUDES: dict[str, tuple[str, ...]] = {"graph": ("direct_risks",)}
#: Per-field units and calibration. Identical on every call, so it is opt-in.
#: ``tests`` adds the PR directive's typed ``test_recommendations`` rows and
#: ``blast`` the ``pr_blast_radius`` dossier.
_INCLUDE_BLOCKS = frozenset(_TARGET_CARD_INCLUDES) | {"blast", "scales", "tests"}


def _drop_opt_in_blocks(response: dict, include: set[str]) -> None:
    """Strip the opt-in fields no ``include`` key asked for."""
    cards = list(response.get("targets", {}).values())
    blast = response.get("pr_blast_radius") or {}
    for holders, keys_by_block in ((cards, _TARGET_CARD_INCLUDES), ([blast], _BLAST_INCLUDES)):
        dropped = [
            key for block, keys in keys_by_block.items() if block not in include for key in keys
        ]
        for holder in holders:
            for key in dropped:
                holder.pop(key, None)


class _DependencyGraph(NamedTuple):
    node_meta: dict[str, GraphNode]
    import_links: dict[str, dict[str, set[str]]]
    reverse_deps: dict[str, dict[str, set[str]]]
    dep_counts: dict[str, int]


@dataclass
class _RiskEvidence:
    repository: Any
    results: list[dict]
    test_paths: set[str]
    global_hotspots: list[dict]
    pr_blast_radius: dict | None


def _add_link(links: dict[str, dict[str, set[str]]], node: str, other: str, edge_type: str) -> None:
    links.setdefault(node, {}).setdefault(other, set()).add(edge_type)


async def _file_dependency_edges(
    session: Any, repo_id: str, endpoint: Any, node_ids: set[str], node_meta: dict[str, GraphNode]
) -> list[GraphEdge]:
    """File-to-file dependency edges whose *endpoint* column is in *node_ids*.

    File-to-file only: every edge is read as "X depends on Y", so a symbol,
    containment or co-change edge would invent a dependent (a co-change
    partner would be reported as an import). The node-type check runs here
    rather than as SQL subqueries, which led SQLite onto the source-node index
    and cost seconds per call. Endpoint rows are added to *node_meta*.
    """
    edges: list[GraphEdge] = []
    for batch in chunked(sorted(node_ids)):
        res = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.edge_type.in_(FILE_DEPENDENCY_EDGE_TYPES),
                endpoint.in_(batch),
            )
        )
        edges.extend(res.scalars().all())
    unseen = {n for e in edges for n in (e.source_node_id, e.target_node_id)} - node_meta.keys()
    node_meta.update(await get_graph_nodes_by_ids(session, repo_id, sorted(unseen)))

    def is_file(node_id: str) -> bool:
        node = node_meta.get(node_id)
        return node is not None and node.node_type == "file"

    return [e for e in edges if is_file(e.source_node_id) and is_file(e.target_node_id)]


async def _load_dependency_graph(session: Any, repo_id: str, roots: set[str]) -> _DependencyGraph:
    """The part of the dependency graph the cards for *roots* read.

    That is every edge touching a root (``import_links``), plus the edges into
    each direct dependent, the second hop ``_dependency_population`` walks.
    Adjacency and ``dep_counts`` are complete for roots and their direct
    dependents only, and ``node_meta`` holds just the roots and the endpoints
    of those edges. Reading the whole graph instead cost tens of seconds per
    call on a large repository.
    """
    node_meta = await get_graph_nodes_by_ids(session, repo_id, sorted(roots))
    near = await _file_dependency_edges(
        session, repo_id, GraphEdge.target_node_id, roots, node_meta
    )
    near += await _file_dependency_edges(
        session, repo_id, GraphEdge.source_node_id, roots, node_meta
    )
    direct = {e.source_node_id for e in near if e.target_node_id in roots}
    far = await _file_dependency_edges(
        session, repo_id, GraphEdge.target_node_id, direct - roots, node_meta
    )

    import_links: dict[str, dict[str, set[str]]] = {}
    reverse_deps: dict[str, dict[str, set[str]]] = {}
    # A root-to-root edge comes back from both queries; the sets absorb it.
    for e in [*near, *far]:
        edge_type = str(e.edge_type)
        _add_link(import_links, e.source_node_id, e.target_node_id, edge_type)
        _add_link(import_links, e.target_node_id, e.source_node_id, edge_type)
        _add_link(reverse_deps, e.target_node_id, e.source_node_id, edge_type)
    # Count unique incoming dependent nodes, not parallel edge rows. A file
    # with both an import and a type-use edge is still one direct dependent.
    dep_counts = {target: len(sources) for target, sources in reverse_deps.items()}
    return _DependencyGraph(node_meta, import_links, reverse_deps, dep_counts)


def _hotspot_entry(meta: GitMetadata, as_of_ts: Any = None) -> dict:
    entry = {
        "file_path": meta.file_path,
        "hotspot_score": meta.churn_percentile,
        "is_hotspot": True,
        "primary_owner": meta.primary_owner_name,
    }
    fixes = fix_annotation(meta, now=as_of_ts)
    if fixes is not None:
        entry.update(fixes)
    return entry


async def _global_hotspots(
    session: Any,
    repo_id: str,
    targets: list[str],
    exclude_spec: Any,
    as_of_ts: Any = None,
) -> list[dict]:
    """Hotspots outside ``targets``, ranked on bug-fix history, then churn.

    Fix-first ranking keeps this list agreeing with the per-target "bug-prone"
    verdicts, and admitting bug magnets lets an often-fixed but quiet file
    appear. A repo with no fix convention falls back to churn order.
    """
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
    all_hotspots = filter_rows_by_attr(list(res.scalars().all()), "file_path", exclude_spec)
    target_set = set(targets)
    return [
        _hotspot_entry(h, as_of_ts=as_of_ts)
        for h in all_hotspots
        if h.file_path not in target_set
    ]


async def _pr_blast_radius(
    session: Any, repo_id: str, alias: str, changed_files: list[str], exclude_spec: Any
) -> dict:
    from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer

    analyzer = PRBlastRadiusAnalyzer(session, repo_id, repository_alias=alias)
    return await analyzer.analyze_files(changed_files, exclude_spec=exclude_spec)


async def _gather_evidence(
    ctx: Any,
    targets: list[str],
    changed_files: list[str],
    exclude_spec: Any,
    collector: OmissionCollector,
    include_graph: bool,
) -> _RiskEvidence:
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id
        # A card walks from the target as given and reads links under its
        # normalized path, so both spellings are roots.
        roots = {*targets, *(normalize_target_path(t, repository.local_path) for t in targets)}
        graph = await _load_dependency_graph(session, repo_id, roots)

        # Repo-wide, so computed once for every target's bus-factor calibration.
        team_size = await _get_active_contributor_count(session, repo_id)
        as_of_ts = (
            await session.execute(
                select(func.max(GitMetadata.last_commit_at)).where(
                    GitMetadata.repository_id == repo_id
                )
            )
        ).scalar()

        results = await asyncio.gather(
            *[
                _assess_one_target(
                    session,
                    repository,
                    t,
                    graph.dep_counts,
                    graph.import_links,
                    graph.reverse_deps,
                    graph.node_meta,
                    exclude_spec,
                    team_size,
                    collector,
                    include_graph,
                    as_of_ts=as_of_ts,
                )
                for t in targets
            ]
        )
        global_hotspots = []
        if len(targets) > 1 and not changed_files:
            global_hotspots = await _global_hotspots(
                session, repo_id, targets, exclude_spec, as_of_ts=as_of_ts
            )
        pr_blast_radius = None
        # Only the PR directive reads this, and only for affected file paths.
        test_paths: set[str] = set()
        if changed_files:
            pr_blast_radius = await _pr_blast_radius(
                session, repo_id, ctx.alias, changed_files, exclude_spec
            )
            test_paths = await get_test_file_paths(session, repo_id)
    return _RiskEvidence(repository, results, test_paths, global_hotspots, pr_blast_radius)


async def _enrich_cards(
    results: list[dict], ctx: Any, repo_id: str, collector: OmissionCollector, include_graph: bool
) -> None:
    # Enrichers key on a path prefix, so they will bind signal to a card that
    # resolved nothing. Mutation is in place, so ``results`` keeps its order.
    scored = [r for r in results if r.get("resolved") is not False]

    await _enrich_cross_repo(scored, ctx.alias, collector, include_graph=include_graph)

    await _enrich_health(scored, ctx, repo_id)

    # An episode count, not episode text, keeps the card within budget; get_why
    # carries the detail. Absent rather than zero.
    await asyncio.to_thread(_enrich_episodes, scored, ctx.path)


async def _lead_with_pr_directive(
    response: dict,
    evidence: _RiskEvidence,
    changed_files: list[str],
    ctx: Any,
    exclude_spec: Any,
    collector: OmissionCollector,
    full_scale: bool,
    include_tests: bool,
    include_blast: bool,
) -> dict:
    governance_risk = await _governance_directive(ctx, changed_files)
    _build_pr_directive(
        response,
        evidence.pr_blast_radius,
        changed_files,
        exclude_spec,
        collector,
        governance_risk,
        evidence.test_paths,
        ctx.alias,
        full_scale=full_scale,
        include_tests=include_tests,
        include_blast=include_blast,
    )
    # Insertion order is the serialized order, and PR mode leads with the directive.
    return {"directive": response.pop("directive"), **response}


@mcp.tool(
    surface_order=50,
    artifact_type="risk",
    presentation="risk",
    evidence_basis="measured",
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
    targets: list[str] | None = None,
    repo: str | None = None,
    changed_files: list[str] | None = None,
    include: list[str] | None = None,
) -> dict:
    """What history says about touching these files — bug fixes, churn, owners.

    Fuses git temporal signals (``hotspot_score`` is 0-1; trend) with graph
    topology. ``primary_owner`` is unconditional; detailed owner metrics
    (``owner_pct``, ``recent_owner``, ``bus_factor``, ``contributor_count``)
    require ``include=["owners"]``. ``dependents`` are directed structural
    reach; ``consumers`` require typed contract links; ``co_change_partners``
    are historical correlation only. Structural reach does not prove runtime
    breakage. Pass changed_files for PR mode: response leads with a directive
    block (may_break, missing_cochanges, missing_tests, tests_to_run,
    tests_to_update). ``tests_to_run_basis`` says measured or inferred. To
    score a commit or range, use ``get_change_risk``.

    ``directive.reach`` (localized, moderate, broad) bands import reach:
    uncalibrated, never a breakage probability. ``include=["blast"]`` adds
    ``pr_blast_radius``; its raw score requires ``include=["blast", "scales"]``.

    Default responses fit 24k chars; nonempty ``include`` uses 32k. Reductions
    carry counts and ``_meta.omitted`` recovery refs. Include-gated blocks are
    projections, not omissions.

    Args:
        targets: file paths to assess; defaults to changed_files.
        repo: usually omitted.
        changed_files: PR-changed files for blast-radius mode.
        include: opt-in blocks - "graph", "churn", "owners", "tests" (typed test
            rows), "blast", "scales" (units and calibration).
    """
    if repo == "all":
        return _unsupported_repo_all("get_risk")
    if not targets and not changed_files:
        return {"error": "targets or changed_files is required", "_meta": _build_meta()}
    ignored: list[dict] = []
    include_set = {
        block
        for block in (include or [])
        if resolve_enum_argument(block, _INCLUDE_BLOCKS, argument="include", ignored=ignored)
    }
    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_risk", repo_root=ctx.path)
    exclude_spec = _get_exclude_spec(ctx.path)
    targets = filter_path_list(targets or changed_files, exclude_spec)
    changed_files = filter_path_list(changed_files, exclude_spec)
    include_graph = "graph" in include_set
    evidence = await _gather_evidence(
        ctx, targets, changed_files, exclude_spec, collector, include_graph
    )
    await _enrich_cards(evidence.results, ctx, evidence.repository.id, collector, include_graph)

    # The budget sheds cards from the tail, so docs and config cards go last.
    cards = evidence.results
    if changed_files:
        cards = sorted(cards, key=lambda r: is_doc_or_config_path(r["target"]))
    response: dict = {
        "targets": {r["target"]: r for r in cards},
        **({"risk_scales": file_risk_scales()} if "scales" in include_set else {}),
    }
    if evidence.pr_blast_radius is not None:
        response = await _lead_with_pr_directive(
            response,
            evidence,
            changed_files,
            ctx,
            exclude_spec,
            collector,
            full_scale="scales" in include_set,
            include_tests="tests" in include_set,
            include_blast="blast" in include_set,
        )
    elif len(targets) > 1:
        # Ambient hotspots orient a multi-file request; beside one named file they are noise.
        cap_collection(
            response,
            "global_hotspots",
            evidence.global_hotspots,
            5,
            collector,
            label="global_hotspots beyond cap=5",
        )

    response["_meta"] = _build_meta(
        repository=evidence.repository, targets=[*targets, *changed_files] or None
    )
    _drop_opt_in_blocks(response, include_set)
    drop_echoed_target(response.get("targets"))
    attach_ignored_arguments(response, ignored)
    collector.attach(response)
    return response
