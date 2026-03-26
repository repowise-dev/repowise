"""MCP Tool 1: get_overview — repository architecture overview."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._server import mcp


@mcp.tool()
async def get_overview(repo: str | None = None) -> dict:
    """Get the repository overview: architecture summary, module map, key entry points.

    Best first call when starting to explore an unfamiliar codebase.

    Args:
        repo: Repository path, name, or ID. Omit if only one repo exists.
    """
    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)

        # Get repo overview page
        result = await session.execute(
            select(Page).where(
                Page.repository_id == repository.id,
                Page.page_type == "repo_overview",
            )
        )
        overview_page = result.scalar_one_or_none()

        # Get architecture diagram page
        result = await session.execute(
            select(Page).where(
                Page.repository_id == repository.id,
                Page.page_type == "architecture_diagram",
            )
        )
        arch_page = result.scalar_one_or_none()

        # Get module pages
        result = await session.execute(
            select(Page)
            .where(
                Page.repository_id == repository.id,
                Page.page_type == "module_page",
            )
            .order_by(Page.title)
        )
        module_pages = result.scalars().all()

        # Get entry point files from graph nodes (exclude tests & fixtures)
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.is_entry_point == True,  # noqa: E712
                GraphNode.is_test == False,  # noqa: E712
            )
        )
        entry_nodes = [
            n for n in result.scalars().all()
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
                churn_trend = "increasing" if ratio > 1.5 else ("decreasing" if ratio < 0.5 else "stable")
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

        return {
            "title": overview_page.title if overview_page else repository.name,
            "content_md": overview_page.content if overview_page else "No overview generated yet.",
            "architecture_diagram_mermaid": arch_page.content if arch_page else None,
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
            "entry_points": [n.node_id for n in entry_nodes],
            "git_health": git_health,
        }
