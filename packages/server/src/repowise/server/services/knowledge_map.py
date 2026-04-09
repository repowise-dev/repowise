"""Shared logic for computing the knowledge map for a repository."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GitMetadata, GraphNode, Page


async def compute_knowledge_map(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """Return knowledge-map data for *repo_id*.

    Returns a dict with keys:
        top_owners        — list of {email, name, files_owned, percentage}
        knowledge_silos   — list of {file_path, owner_email, owner_pct}
        onboarding_targets — list of {path, pagerank, doc_words}

    Returns an empty dict when no git metadata is available.
    """
    git_res = await session.execute(
        select(GitMetadata).where(GitMetadata.repository_id == repo_id)
    )
    all_git = git_res.scalars().all()

    if not all_git:
        return {}

    # top_owners: aggregate primary_owner_email across all files
    owner_file_count: dict[str, int] = defaultdict(int)
    owner_name_map: dict[str, str] = {}
    for g in all_git:
        email = g.primary_owner_email or ""
        if email:
            owner_file_count[email] += 1
            if g.primary_owner_name:
                owner_name_map[email] = g.primary_owner_name

    total_files = len(all_git) or 1
    top_owners = sorted(
        [
            {
                "email": email,
                "name": owner_name_map.get(email, ""),
                "files_owned": count,
                "percentage": round(count / total_files * 100.0, 1),
            }
            for email, count in owner_file_count.items()
        ],
        key=lambda x: -x["files_owned"],
    )[:10]

    # knowledge_silos: files where primary owner has > 80% ownership
    knowledge_silos = [
        {
            "file_path": g.file_path,
            "owner_email": g.primary_owner_email or "",
            "owner_pct": round(float(g.primary_owner_commit_pct or 0.0), 3),
        }
        for g in all_git
        if (g.primary_owner_commit_pct or 0.0) > 0.8
    ]

    # onboarding_targets: high-centrality files with fewest docs
    node_result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_test == False,  # noqa: E712
        )
    )
    all_nodes = node_result.scalars().all()

    page_result = await session.execute(
        select(Page).where(
            Page.repository_id == repo_id,
            Page.page_type == "file_page",
        )
    )
    doc_words: dict[str, int] = {
        p.target_path: len(p.content.split()) for p in page_result.scalars().all()
    }

    candidates = [
        {
            "path": n.node_id,
            "pagerank": n.pagerank,
            "doc_words": doc_words.get(n.node_id, 0),
        }
        for n in all_nodes
        if n.pagerank > 0.0
    ]
    candidates.sort(key=lambda x: (x["doc_words"], -x["pagerank"]))
    onboarding_targets = candidates[:10]

    return {
        "top_owners": top_owners,
        "knowledge_silos": knowledge_silos,
        "onboarding_targets": onboarding_targets,
    }
