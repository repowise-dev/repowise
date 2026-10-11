"""Workspace views: the repo="all" summary and the footer on a default-repo overview."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import _get_repo, _resolve_all_contexts
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_overview.pages import _load_overview_page
from repowise.server.mcp_server.tool_overview.surface import _tool_surface_guide


async def _workspace_overview() -> dict:
    """Build a concise workspace-level overview across all repos."""
    contexts = await _resolve_all_contexts()
    registry = _state._registry
    repos_info = [await _repo_entry(ctx, registry) for ctx in contexts]
    cross_repo_topology = _cross_repo_topology(repos_info)

    result: dict[str, Any] = {
        "workspace": True,
        "workspace_root": str(registry.workspace_root) if registry else "",
        "total_repos": len(repos_info),
        "total_files": sum(r["file_count"] for r in repos_info),
        "total_symbols": sum(r["symbol_count"] for r in repos_info),
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


async def _repo_entry(ctx: Any, registry: Any) -> dict[str, Any]:
    """One repo's line in the workspace summary: overview headline and graph size."""
    async with get_session(ctx.session_factory) as session:
        repo_obj = await _get_repo(session)
        # Same multi-row safety as the single-repo overview.
        overview_page = await _load_overview_page(session, repo_obj)
        ov_content = (overview_page.content if overview_page else None) or ""
        return {
            "alias": ctx.alias,
            "path": str(ctx.path),
            "summary": ov_content.split("\n")[0].strip("# ").strip()[:200],
            "file_count": await _count_nodes(session, repo_obj.id, "file"),
            "symbol_count": await _count_nodes(session, repo_obj.id, "symbol"),
            "is_default": registry is not None and ctx.alias == registry.get_default_alias(),
        }


async def _count_nodes(session: Any, repository_id: str, node_type: str) -> int:
    result = await session.execute(
        select(sa_func.count())
        .select_from(GraphNode)
        .where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == node_type,
        )
    )
    return result.scalar_one()


def _cross_repo_topology(repos_info: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-repo summary and contracts; also tags each repo with the repos it depends on."""
    enricher = _enricher_with_data()
    if enricher is None:
        return {}
    topology = enricher.get_cross_repo_summary()
    if enricher.has_contract_data:
        topology["contracts"] = enricher.get_contract_summary()
    for repo_info in repos_info:
        deps = enricher.get_package_deps(repo_info["alias"])
        if deps:
            repo_info["depends_on"] = sorted(set(d["target_repo"] for d in deps))
    return topology


def _enricher_with_data() -> Any | None:
    enricher = _state._cross_repo_enricher
    return enricher if enricher is not None and enricher.has_data else None


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

    enricher = _enricher_with_data()
    if enricher is not None:
        footer["cross_repo"] = enricher.get_cross_repo_summary()
        if enricher.has_contract_data:
            footer["contract_links"] = enricher.get_contract_summary()

    return footer
