"""MCP Tool 1: get_overview — repository architecture overview."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select

from repowise.core.generation.onboarding.slots import (
    ONBOARDING_ORDER,
    PROMOTED_SLOTS,
)
from repowise.core.persistence.crud import (
    get_health_metrics as _get_health_metrics,
)
from repowise.core.persistence.crud import (
    get_health_summary as _get_health_summary,
)
from repowise.core.persistence.crud import (
    get_kg_layers as _get_kg_layers,
)
from repowise.core.persistence.crud import (
    get_kg_tour_steps as _get_kg_tour_steps,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionEdge,
    DecisionRecord,
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_all_contexts,
    _resolve_repo_context,
    filter_graph_nodes,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

# ---------------------------------------------------------------------------
# repo="all" — workspace-level summary
# ---------------------------------------------------------------------------


async def _workspace_overview() -> dict:
    """Build a concise workspace-level overview across all repos."""
    contexts = await _resolve_all_contexts()
    registry = _state._registry

    repos_info: list[dict] = []
    total_files = 0
    total_symbols = 0

    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repo_obj = await _get_repo(session)

            # One-line summary from repo_overview page. Same multi-row
            # safety as the single-repo path below.
            ov_result = await session.execute(
                select(Page.content)
                .where(
                    Page.repository_id == repo_obj.id,
                    Page.page_type == "repo_overview",
                )
                .order_by(
                    (Page.target_path == repo_obj.name).desc(),
                    Page.updated_at.desc(),
                )
            )
            ov_content = ov_result.scalars().first() or ""
            summary = ov_content.split("\n")[0].strip("# ").strip()[:200] if ov_content else ""

            # File and symbol counts
            file_count_res = await session.execute(
                select(sa_func.count())
                .select_from(GraphNode)
                .where(
                    GraphNode.repository_id == repo_obj.id,
                    GraphNode.node_type == "file",
                )
            )
            file_count = file_count_res.scalar_one()

            symbol_count_res = await session.execute(
                select(sa_func.count())
                .select_from(GraphNode)
                .where(
                    GraphNode.repository_id == repo_obj.id,
                    GraphNode.node_type == "symbol",
                )
            )
            symbol_count = symbol_count_res.scalar_one()

            total_files += file_count
            total_symbols += symbol_count

            is_default = registry is not None and ctx.alias == registry.get_default_alias()

            repos_info.append(
                {
                    "alias": ctx.alias,
                    "path": str(ctx.path),
                    "summary": summary,
                    "file_count": file_count,
                    "symbol_count": symbol_count,
                    "is_default": is_default,
                }
            )

    # Cross-repo topology (Phase 3 + 4)
    cross_repo_topology: dict[str, Any] = {}
    enricher = _state._cross_repo_enricher
    if enricher is not None and enricher.has_data:
        cross_repo_topology = enricher.get_cross_repo_summary()
        if enricher.has_contract_data:
            cross_repo_topology["contracts"] = enricher.get_contract_summary()
        # Add per-repo package deps
        for repo_info in repos_info:
            deps = enricher.get_package_deps(repo_info["alias"])
            if deps:
                repo_info["depends_on"] = sorted(set(d["target_repo"] for d in deps))

    result: dict[str, Any] = {
        "workspace": True,
        "workspace_root": str(registry.workspace_root) if registry else "",
        "total_repos": len(repos_info),
        "total_files": total_files,
        "total_symbols": total_symbols,
        "repos": repos_info,
        "hint": ("Use repo='<alias>' to query a specific repo. Omit repo to use the default."),
    }
    if cross_repo_topology:
        result["cross_repo_topology"] = cross_repo_topology

    return result


# ---------------------------------------------------------------------------
# Workspace footer — appended to default-repo overview
# ---------------------------------------------------------------------------


def _build_workspace_footer() -> dict | None:
    """Build workspace context footer for the default overview."""
    registry = _state._registry
    if registry is None:
        return None

    default_alias = registry.get_default_alias()
    other_repos = [a for a in registry.get_all_aliases() if a != default_alias]
    if not other_repos:
        return None

    footer: dict[str, Any] = {
        "workspace_root": str(registry.workspace_root),
        "default_repo": default_alias,
        "other_repos": other_repos,
        "hint": (
            "This repo is part of a workspace. "
            f"Other repos: {', '.join(other_repos)}. "
            "Use repo='<alias>' to query another repo, "
            "or repo='all' for workspace-wide results."
        ),
    }

    # Cross-repo intelligence (Phase 3 + 4)
    enricher = _state._cross_repo_enricher
    if enricher is not None and enricher.has_data:
        footer["cross_repo"] = enricher.get_cross_repo_summary()
        if enricher.has_contract_data:
            footer["contract_links"] = enricher.get_contract_summary()

    return footer


@mcp.tool()
async def get_overview(repo: str | None = None) -> dict:
    """Architecture map for an unfamiliar repo — first call when you don't know your way around.

    Returns the synthesised overview plus key modules, entry points, repo-wide
    git health (hotspot count, churn trend, bus-factor distribution), the
    knowledge map (top owners, knowledge silos), and the community summary.
    Skip this on subsequent calls — once you have the map, jump straight to
    ``get_context`` / ``get_answer``.

    In workspace mode:
    - Omit ``repo`` for the default repo's overview plus a workspace footer.
    - ``repo="all"`` returns the cross-repo topology (co-changes, package deps,
      API contracts) — no single-repo detail.
    - ``repo="<alias>"`` targets one specific repo.

    Args:
        repo: Repository alias, path, or ID. Use ``"all"`` for workspace overview.
    """
    if repo == "all":
        return await _workspace_overview()

    ctx = await _resolve_repo_context(repo)
    exclude_spec = _get_exclude_spec(ctx.path)
    # Entries beyond the response caps below are persisted, not silently
    # dropped — the response carries an expandable [repowise#<ref>] marker.
    collector = OmissionCollector("get_overview", repo_root=ctx.path)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

        # Get repo overview page. Older indexes occasionally left a stale
        # row with target_path='repo' alongside the canonical
        # target_path=<repo_name> row, so prefer the row matching the repo
        # name and fall back to the most recently updated one. Using
        # scalar_one_or_none here would crash with MultipleResultsFound on
        # those legacy DBs.
        result = await session.execute(
            select(Page)
            .where(
                Page.repository_id == repository.id,
                Page.page_type == "repo_overview",
            )
            .order_by(
                (Page.target_path == repository.name).desc(),
                Page.updated_at.desc(),
            )
        )
        overview_page = result.scalars().first()

        # Get module pages
        result = await session.execute(
            select(Page)
            .where(
                Page.repository_id == repository.id,
                Page.page_type == "module_page",
            )
            .order_by(Page.title)
        )
        all_module_pages = result.scalars().all()
        module_pages = all_module_pages[:20]  # Cap to keep response bounded
        if len(all_module_pages) > 20:
            collector.add(
                f"module pages beyond cap=20 ({len(all_module_pages) - 20} dropped)",
                "\n".join(f"{p.title}: {p.target_path}" for p in all_module_pages[20:]),
            )

        # Get entry point files from graph nodes (exclude tests & fixtures)
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.is_entry_point == True,  # noqa: E712
                GraphNode.is_test == False,  # noqa: E712
            )
        )
        entry_nodes = filter_graph_nodes(
            [
                n
                for n in result.scalars().all()
                if not any(
                    seg in n.node_id.lower()
                    for seg in ("fixture", "test_data", "testdata", "sample_repo")
                )
            ],
            exclude_spec,
        )

        # Phase 4: repo-wide git health summary
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git = filter_rows_by_attr(
            list(git_res.scalars().all()), "file_path", exclude_spec
        )

        git_health: dict[str, Any] = {}
        if all_git:
            hotspot_count = sum(1 for g in all_git if g.is_hotspot)
            bus_factors = [getattr(g, "bus_factor", 0) or 0 for g in all_git]
            avg_bus = sum(bus_factors) / len(bus_factors) if bus_factors else 0
            bf1 = sum(1 for b in bus_factors if b == 1)
            c30_total = sum(g.commit_count_30d or 0 for g in all_git)
            c90_total = sum(g.commit_count_90d or 0 for g in all_git)
            baseline = c90_total - c30_total
            if baseline > 0:
                ratio = (c30_total / 30.0) / (baseline / 60.0)
                churn_trend = (
                    "increasing" if ratio > 1.5 else ("decreasing" if ratio < 0.5 else "stable")
                )
            else:
                churn_trend = "increasing" if c30_total > 0 else "stable"
            # Top churn modules (group by first directory component)
            module_churn: Counter = Counter()
            for g in all_git:
                parts = g.file_path.split("/")
                mod = parts[0] if len(parts) == 1 else "/".join(parts[:2])
                module_churn[mod] += g.commit_count_90d or 0
            top_modules = [m for m, _ in module_churn.most_common(5) if module_churn[m] > 0]

            git_health = {
                "total_files_indexed": len(all_git),
                "hotspot_count": hotspot_count,
                "avg_bus_factor": round(avg_bus, 1),
                "files_with_bus_factor_1": bf1,
                "churn_trend": churn_trend,
                "top_churn_modules": top_modules,
            }

        # B. Knowledge map -------------------------------------------------------
        knowledge_map: dict[str, Any] = {}
        if all_git:
            # top_owners: aggregate primary_owner_email across all files
            owner_file_count: dict[str, int] = defaultdict(int)
            owner_pct_sum: dict[str, float] = defaultdict(float)
            for g in all_git:
                email = g.primary_owner_email or ""
                if email:
                    owner_file_count[email] += 1
                    owner_pct_sum[email] += float(g.primary_owner_commit_pct or 0.0)

            total_files = len(all_git) or 1
            top_owners = sorted(
                [
                    {
                        "email": email,
                        "files_owned": count,
                        "percentage": round(count / total_files * 100.0, 1),
                    }
                    for email, count in owner_file_count.items()
                ],
                key=lambda x: -x["files_owned"],
            )[:10]

            # knowledge_silos: files where primary owner has > 80% ownership
            # Filter out boilerplate (migrations, __init__.py, config, lock files)
            silo_exclude_patterns = (
                "alembic/versions/",
                "__init__.py",
                "migrations/",
                ".lock",
                "package-lock",
                "conftest.py",
            )
            knowledge_silos = [
                g.file_path
                for g in sorted(all_git, key=lambda g: -(g.primary_owner_commit_pct or 0.0))
                if (g.primary_owner_commit_pct or 0.0) > 0.8
                and not any(pat in g.file_path for pat in silo_exclude_patterns)
            ][:10]

            knowledge_map = {
                "top_owners": top_owners,
                "knowledge_silos": knowledge_silos,
            }

        # C. Community summary ---------------------------------------------------
        community_summary: list[dict[str, Any]] = []
        # Fetch file nodes for community grouping
        if not all_git:
            node_result = await session.execute(
                select(GraphNode).where(
                    GraphNode.repository_id == repository.id,
                    GraphNode.node_type == "file",
                )
            )
            all_nodes = filter_graph_nodes(list(node_result.scalars().all()), exclude_spec)
        else:
            node_result = await session.execute(
                select(GraphNode).where(
                    GraphNode.repository_id == repository.id,
                    GraphNode.is_test == False,  # noqa: E712
                )
            )
            all_nodes = filter_graph_nodes(list(node_result.scalars().all()), exclude_spec)

        # Group file nodes by community_id
        community_groups: dict[int, list[GraphNode]] = defaultdict(list)
        for n in all_nodes:
            if n.node_type == "file" and n.community_id is not None:
                community_groups[n.community_id].append(n)

        # Sort communities by size descending, take top 10
        # Skip communities with generic/unhelpful labels
        generic_labels = {"packages", "src", "lib", "core", "app", ""}
        for cid, members in sorted(community_groups.items(), key=lambda x: -len(x[1])):
            if len(community_summary) >= 10:
                break
            label = ""
            cohesion = 0.0
            if members:
                try:
                    meta = json.loads(members[0].community_meta_json or "{}")
                    label = meta.get("label", "")
                    cohesion = meta.get("cohesion", 0.0)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Build a useful label: if the heuristic label is generic,
            # use the most common directory segment among members
            display_label = label
            if not label or label.lower() in generic_labels:
                # Find dominant specific directory
                dir_counts: Counter = Counter()
                for m in members:
                    parts = m.node_id.split("/")
                    # Use the deepest meaningful directory segment
                    for p in reversed(parts[:-1]):
                        if p.lower() not in generic_labels and p not in ("src",):
                            dir_counts[p] += 1
                            break
                display_label = (
                    dir_counts.most_common(1)[0][0] if dir_counts else f"cluster_{cid}"
                )

            community_summary.append(
                {
                    "id": cid,
                    "label": display_label,
                    "size": len(members),
                    "cohesion": round(cohesion, 3),
                }
            )

        # D. KG architecture layers + tour availability -------------------------
        kg_layers = await _get_kg_layers(session, repository.id)
        kg_tour = await _get_kg_tour_steps(session, repository.id)
        architecture: dict[str, Any] = {}
        if kg_layers:
            architecture["layers"] = [
                {
                    "name": layer.name,
                    "description": (layer.description or "")[:120],
                    "file_count": len(
                        json.loads(layer.node_ids_json) if layer.node_ids_json else []
                    ),
                }
                for layer in kg_layers
            ]
            architecture["tour_available"] = bool(kg_tour)
            architecture["tour_step_count"] = len(kg_tour)

        # E. Reading order — the canonical onboarding spine, mirrored for agents
        # so they can walk the wiki in the same order a human would (§ dual
        # audience). Only slots that actually produced a page are listed.
        ro_result = await session.execute(
            select(Page).where(
                Page.repository_id == repository.id,
                Page.page_type.in_(["onboarding", *PROMOTED_SLOTS.keys()]),
            )
        )
        slot_to_page: dict[str, Page] = {}
        for p in ro_result.scalars().all():
            if p.page_type == "onboarding":
                slot = (p.target_path or "").rsplit("/", 1)[-1]
            else:
                slot = PROMOTED_SLOTS.get(p.page_type, "")
            if slot and slot not in slot_to_page:
                slot_to_page[slot] = p
        reading_order: list[dict[str, Any]] = []
        for slot in ONBOARDING_ORDER:
            p = slot_to_page.get(slot)
            if p is None:
                continue
            reading_order.append(
                {
                    "order": len(reading_order) + 1,
                    "slot": slot,
                    "title": p.title,
                    "page_id": p.id,
                    "target_path": p.target_path,
                }
            )

        # Older indexes persisted titles like "Repository Overview: repo" because
        # repo_name was not passed through to generate_repo_overview. Substitute
        # the actual repo name back in so the response is useful without reindex.
        if overview_page:
            persisted_title = overview_page.title or ""
            title = persisted_title.replace(
                "Repository Overview: repo", f"Repository Overview: {repository.name}"
            )
        else:
            title = repository.name
        # Code-health KPIs — three headline numbers for the architecture
        # summary. Falls back to defaults (avg 10/10, no worst file) when
        # health hasn't been run on this repo yet.
        code_health: dict[str, Any] = {}
        try:
            health_summary = await _get_health_summary(session, repository.id)
            metrics_rows = await _get_health_metrics(session, repository.id)
            if metrics_rows:
                # Hotspot health: NLOC-weighted avg over the top-25% files
                # by NLOC, matching the dashboard KPI definition.
                sorted_by_nloc = sorted(metrics_rows, key=lambda m: m.nloc or 0, reverse=True)
                top_q = sorted_by_nloc[: max(1, len(sorted_by_nloc) // 4)]
                tot = sum(max(m.nloc, 1) for m in top_q)
                hotspot_avg = sum(m.score * max(m.nloc, 1) for m in top_q) / tot if tot else 10.0
                code_health = {
                    "average_health": health_summary["average_health"],
                    "hotspot_health": round(hotspot_avg, 2),
                    "worst_performer_path": health_summary["worst_performer_path"],
                    "worst_performer_score": health_summary["worst_performer_score"],
                    "open_findings": health_summary["open_findings"],
                    "file_count": health_summary["file_count"],
                }
        except Exception:
            code_health = {}

        # F. Key decisions + recent reversals (Phase 4A) -----------------------
        key_decisions_section: dict[str, Any] = {}
        try:
            top_decisions_res = await session.execute(
                select(DecisionRecord)
                .where(
                    DecisionRecord.repository_id == repository.id,
                    DecisionRecord.status == "active",
                )
                .order_by(DecisionRecord.confidence.desc())
                .limit(5)
            )
            top_decisions = top_decisions_res.scalars().all()
            if top_decisions:
                key_decisions_list = []
                for dr in top_decisions:
                    try:
                        affected_files = json.loads(dr.affected_files_json or "[]")[:3]
                    except (json.JSONDecodeError, TypeError):
                        affected_files = []
                    key_decisions_list.append(
                        {
                            "id": dr.id,
                            "title": dr.title,
                            "status": dr.status,
                            "confidence": dr.confidence,
                            "verification": dr.verification,
                            "affected_files": affected_files,
                        }
                    )
                recent_reversals: list[dict[str, Any]] = []
                supersede_edges_res = await session.execute(
                    select(DecisionEdge)
                    .where(
                        DecisionEdge.repository_id == repository.id,
                        DecisionEdge.kind == "supersedes",
                    )
                    .order_by(DecisionEdge.created_at.desc())
                    .limit(5)
                )
                supersede_edges = supersede_edges_res.scalars().all()
                if supersede_edges:
                    all_edge_ids = list(
                        {e.src_decision_id for e in supersede_edges}
                        | {e.dst_decision_id for e in supersede_edges}
                    )
                    edge_recs_res = await session.execute(
                        select(DecisionRecord).where(DecisionRecord.id.in_(all_edge_ids))
                    )
                    edge_recs = {r.id: r for r in edge_recs_res.scalars().all()}
                    for edge in supersede_edges:
                        src = edge_recs.get(edge.src_decision_id)
                        dst = edge_recs.get(edge.dst_decision_id)
                        if src and dst:
                            recent_reversals.append(
                                {
                                    "newer": {"id": src.id, "title": src.title},
                                    "older": {
                                        "id": dst.id,
                                        "title": dst.title,
                                        "status": dst.status,
                                    },
                                }
                            )
                key_decisions_section = {
                    "top_active": key_decisions_list,
                    "recent_reversals": recent_reversals,
                }
        except Exception:
            key_decisions_section = {}

        result = {
            "title": title,
            "content_md": overview_page.content if overview_page else "No overview generated yet.",
            "code_health": code_health,
            "key_modules": [
                {
                    "name": p.title,
                    "path": p.target_path,
                    "description": (
                        p.content[:200].rsplit(" ", 1)[0] + "..."
                        if len(p.content) > 200
                        else p.content
                    ),
                }
                for p in module_pages
            ],
            "entry_points": _capped_entry_points(entry_nodes, collector),
            "git_health": git_health,
            "knowledge_map": knowledge_map,
            "community_summary": community_summary,
        }

        if architecture:
            result["architecture"] = architecture

        if key_decisions_section:
            result["key_decisions"] = key_decisions_section

        if reading_order:
            result["reading_order"] = reading_order
            result["reading_order_hint"] = (
                "Canonical onboarding sequence — read these page_ids in order "
                "via get_context/get_symbol to understand the repo the way a "
                "new contributor would."
            )

        # Topology-driven guided tour — the ordered, page-by-page walk derived
        # from the import graph (entry points first, then inward, infra last).
        # Persisted on the repo_overview page metadata at generation time.
        if overview_page:
            from repowise.core.generation.models import compute_page_id

            try:
                ov_meta = json.loads(overview_page.metadata_json or "{}")
            except (json.JSONDecodeError, TypeError):
                ov_meta = {}
            tour = ov_meta.get("guided_tour") or []
            if tour:
                result["guided_tour"] = [
                    {
                        "order": s.get("order"),
                        "title": s.get("title"),
                        "kind": s.get("kind"),
                        "reason": s.get("reason"),
                        "target_path": s.get("target_path"),
                        "page_id": compute_page_id(
                            s.get("page_type", "file_page"), s.get("target_path", "")
                        ),
                    }
                    for s in tour
                ]
                result["guided_tour_hint"] = (
                    "Topology-ordered walk of the codebase: read these page_ids "
                    "in order — entry points first, then the files they import, "
                    "with infrastructure last. Each step builds on the previous."
                )
            layer_order = ov_meta.get("layer_order") or []
            if layer_order:
                result.setdefault("architecture", {})["layer_order"] = layer_order

        # Append workspace context footer when in workspace mode
        ws_footer = _build_workspace_footer()
        if ws_footer:
            result["workspace"] = ws_footer

        result["_meta"] = _build_meta(repository=repository)
        collector.attach(result)
        return result


def _capped_entry_points(entry_nodes: list, collector: OmissionCollector) -> list[str]:
    """First 15 entry-point ids; the remainder goes to the omission store."""
    if len(entry_nodes) > 15:
        collector.add(
            f"entry points beyond cap=15 ({len(entry_nodes) - 15} dropped)",
            "\n".join(n.node_id for n in entry_nodes[15:]),
        )
    return [n.node_id for n in entry_nodes[:15]]
