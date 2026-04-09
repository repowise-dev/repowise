"""MCP Tool 1: get_overview — repository architecture overview."""

from __future__ import annotations

from collections import Counter, defaultdict
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
from repowise.server.services.knowledge_map import compute_knowledge_map


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
            knowledge_silos = [
                g.file_path
                for g in all_git
                if (g.primary_owner_commit_pct or 0.0) > 0.8
            ]

            # onboarding_targets: high-centrality files with least docs
            # pagerank from graph_nodes; doc length from wiki_pages
            node_result = await session.execute(
                select(GraphNode).where(
                    GraphNode.repository_id == repository.id,
                    GraphNode.is_test == False,  # noqa: E712
                )
            )
            all_nodes = node_result.scalars().all()

            # Build word-count map from wiki_pages (file pages)
            page_result = await session.execute(
                select(Page).where(
                    Page.repository_id == repository.id,
                    Page.page_type == "file_page",
                )
            )
            doc_words: dict[str, int] = {
                p.target_path: len(p.content.split()) for p in page_result.scalars().all()
            }

            onboarding_candidates = [
                {
                    "path": n.node_id,
                    "pagerank": n.pagerank,
                    "doc_words": doc_words.get(n.node_id, 0),
                }
                for n in all_nodes
                if n.pagerank > 0.0
            ]
            # Sort by fewest doc words first (least documented), then by highest pagerank
            onboarding_candidates.sort(key=lambda x: (x["doc_words"], -x["pagerank"]))
            onboarding_targets = [c["path"] for c in onboarding_candidates[:5]]

            knowledge_map = {
                "top_owners": top_owners,
                "knowledge_silos": knowledge_silos,
                "onboarding_targets": onboarding_targets,
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
            "knowledge_map": knowledge_map,
        }
