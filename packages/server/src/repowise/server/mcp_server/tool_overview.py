"""MCP Tool 1: get_overview — repository architecture overview."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func as sa_func, select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _is_workspace_mode,
    _resolve_all_contexts,
    _resolve_repo_context,
)
from repowise.server.mcp_server._server import mcp


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

            # One-line summary from repo_overview page
            ov_result = await session.execute(
                select(Page.content).where(
                    Page.repository_id == repo_obj.id,
                    Page.page_type == "repo_overview",
                )
            )
            ov_content = ov_result.scalar_one_or_none() or ""
            summary = ov_content.split("\n")[0].strip("# ").strip()[:200] if ov_content else ""

            # File and symbol counts
            file_count_res = await session.execute(
                select(sa_func.count()).select_from(GraphNode).where(
                    GraphNode.repository_id == repo_obj.id,
                    GraphNode.node_type == "file",
                )
            )
            file_count = file_count_res.scalar_one()

            symbol_count_res = await session.execute(
                select(sa_func.count()).select_from(GraphNode).where(
                    GraphNode.repository_id == repo_obj.id,
                    GraphNode.node_type == "symbol",
                )
            )
            symbol_count = symbol_count_res.scalar_one()

            total_files += file_count
            total_symbols += symbol_count

            is_default = (
                registry is not None
                and ctx.alias == registry.get_default_alias()
            )

            repos_info.append({
                "alias": ctx.alias,
                "path": str(ctx.path),
                "summary": summary,
                "file_count": file_count,
                "symbol_count": symbol_count,
                "is_default": is_default,
            })

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
                repo_info["depends_on"] = sorted(
                    set(d["target_repo"] for d in deps)
                )

    result: dict[str, Any] = {
        "workspace": True,
        "workspace_root": str(registry.workspace_root) if registry else "",
        "total_repos": len(repos_info),
        "total_files": total_files,
        "total_symbols": total_symbols,
        "repos": repos_info,
        "hint": (
            "Use repo='<alias>' to query a specific repo. "
            "Omit repo to use the default."
        ),
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
    other_repos = [
        a for a in registry.get_all_aliases() if a != default_alias
    ]
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
    """Get the repository overview: architecture summary, module map, key entry points.

    Best first call when starting to explore an unfamiliar codebase.

    In workspace mode:
    - Omit ``repo`` for the default repo overview (includes workspace context,
      cross-repo co-changes, package dependencies, and API contract links).
    - Use ``repo="all"`` for a workspace-level summary of all repos including
      cross-repo topology and contract links (HTTP routes, gRPC services, topics).
    - Use ``repo="<alias>"`` to query a specific repo.

    Args:
        repo: Repository alias, path, or ID. Use ``"all"`` for workspace overview.
    """
    if repo == "all":
        return await _workspace_overview()

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

        # Get repo overview page
        result = await session.execute(
            select(Page).where(
                Page.repository_id == repository.id,
                Page.page_type == "repo_overview",
            )
        )
        overview_page = result.scalar_one_or_none()

        # Get module pages
        result = await session.execute(
            select(Page)
            .where(
                Page.repository_id == repository.id,
                Page.page_type == "module_page",
            )
            .order_by(Page.title)
        )
        module_pages = result.scalars().all()[:20]  # Cap to keep response bounded

        # Get entry point files from graph nodes (exclude tests & fixtures)
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.is_entry_point == True,  # noqa: E712
                GraphNode.is_test == False,  # noqa: E712
            )
        )
        entry_nodes = [
            n
            for n in result.scalars().all()
            if not any(
                seg in n.node_id.lower()
                for seg in ("fixture", "test_data", "testdata", "sample_repo")
            )
        ]

        # Phase 4: repo-wide git health summary
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git = git_res.scalars().all()

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
                "alembic/versions/", "__init__.py", "migrations/",
                ".lock", "package-lock", "conftest.py",
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
            all_nodes = node_result.scalars().all()
        else:
            node_result = await session.execute(
                select(GraphNode).where(
                    GraphNode.repository_id == repository.id,
                    GraphNode.is_test == False,  # noqa: E712
                )
            )
            all_nodes = node_result.scalars().all()

        # Group file nodes by community_id
        community_groups: dict[int, list[GraphNode]] = defaultdict(list)
        for n in all_nodes:
            if n.node_type == "file" and n.community_id is not None:
                community_groups[n.community_id].append(n)

        # Sort communities by size descending, take top 10
        # Skip communities with generic/unhelpful labels
        generic_labels = {"packages", "src", "lib", "core", "app", ""}
        for cid, members in sorted(
            community_groups.items(), key=lambda x: -len(x[1])
        ):
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
                if dir_counts:
                    display_label = dir_counts.most_common(1)[0][0]
                else:
                    display_label = f"cluster_{cid}"

            community_summary.append({
                "id": cid,
                "label": display_label,
                "size": len(members),
                "cohesion": round(cohesion, 3),
            })

        result = {
            "title": overview_page.title if overview_page else repository.name,
            "content_md": overview_page.content if overview_page else "No overview generated yet.",
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
            "entry_points": [n.node_id for n in entry_nodes[:15]],
            "git_health": git_health,
            "knowledge_map": knowledge_map,
            "community_summary": community_summary,
        }

        # Append workspace context footer when in workspace mode
        ws_footer = _build_workspace_footer()
        if ws_footer:
            result["workspace"] = ws_footer

        return result
