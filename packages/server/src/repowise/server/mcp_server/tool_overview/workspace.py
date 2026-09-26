"""Workspace views: the repo="all" summary and the footer on a default-repo overview."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import _get_repo, _resolve_all_contexts
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_overview.surface import _tool_surface_guide


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
        "tool_surface": _tool_surface_guide(is_workspace=True),
        "_meta": _build_meta(),
    }
    if cross_repo_topology:
        result["cross_repo_topology"] = cross_repo_topology

    collector = OmissionCollector(
        "get_overview", repo_root=registry.workspace_root if registry else None
    )
    collector.attach(result)
    return result


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
