"""Shared logic for computing the knowledge map for a repository."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.knowledge_map import (
    SILO_OWNER_PCT,
    onboarding_targets,
    rank_silos,
)
from repowise.core.persistence.models import GitMetadata, GraphNode, Page


async def compute_knowledge_silos(session: AsyncSession, repo_id: str) -> list[dict[str, Any]]:
    """Files whose primary owner holds more than 80% of the commits.

    Split out of :func:`compute_knowledge_map` because Overview wants only this
    list and the full map is expensive to the point of dominating that page:
    it hydrates every ``GitMetadata`` row as an ORM entity (seven large JSON
    blob columns) and loads the full ``content`` of every file page to count
    words for a top-10 onboarding list Overview never reads.

    Five columns, one query, no entity hydration; ranked by :func:`rank_silos`.
    """
    rows = await session.execute(
        select(
            GitMetadata.file_path,
            GitMetadata.primary_owner_email,
            GitMetadata.primary_owner_commit_pct,
            GitMetadata.commit_count_90d,
            GitMetadata.is_hotspot,
        ).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.primary_owner_commit_pct > SILO_OWNER_PCT,
        )
    )
    return rank_silos(rows)


async def compute_onboarding_targets(
    session: AsyncSession, repo_id: str
) -> list[dict[str, Any]]:
    """High-centrality files carrying the least documentation.

    Its own function because two callers want exactly this and nothing else
    around it: the knowledge-map endpoint, and the Overview payload, which
    feeds the first-index experience. Overview used to reach it through
    :func:`compute_knowledge_map` and pay for the owner aggregation as well.
    """
    # `pagerank > 0` in SQL too, though the fold repeats it: the filter
    # discards most rows and there is no reason to ship them.
    node_result = await session.execute(
        select(GraphNode.node_id, GraphNode.pagerank).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_test == False,  # noqa: E712
            GraphNode.pagerank > 0.0,
        )
    )
    all_nodes = node_result.all()

    # Character length, computed in the database. The previous version selected
    # `Page.content` for every file page and called `len(text.split())` on each
    # one, which loads the entire generated prose of the repository — megabytes
    # on a documented tree — to produce a ten-row list.
    #
    # Length is only the shortlisting key. It ranks the same way a word count
    # does for prose, and the exact counts are read back below for the handful
    # of rows that survive, so the figure this returns is still a word count.
    size_result = await session.execute(
        select(Page.target_path, func.length(Page.content)).where(
            Page.repository_id == repo_id,
            Page.page_type == "file_page",
        )
    )
    doc_chars: dict[str, int] = {path: int(n or 0) for path, n in size_result}
    shortlist = onboarding_targets(all_nodes, doc_chars)

    # Exact words for the ten rows that are actually returned. Most of them are
    # undocumented files with no page at all, so this usually fetches nothing.
    documented = [c["path"] for c in shortlist if c["doc_words"] > 0]
    exact_words: dict[str, int] = {}
    if documented:
        word_rows = await session.execute(
            select(Page.target_path, Page.content).where(
                Page.repository_id == repo_id,
                Page.page_type == "file_page",
                Page.target_path.in_(documented),
            )
        )
        exact_words = {path: len((content or "").split()) for path, content in word_rows}

    return [{**c, "doc_words": exact_words.get(c["path"], 0)} for c in shortlist]


async def compute_knowledge_map(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """Return knowledge-map data for *repo_id*.

    Returns a dict with keys:
        top_owners        — list of {email, name, files_owned, percentage}
        knowledge_silos   — list of {file_path, owner_email, owner_pct}
        onboarding_targets — list of {path, pagerank, doc_words}

    Returns an empty dict when no git metadata is available.
    """
    # Four columns, not the whole entity: GitMetadata carries seven JSON blob
    # columns (top authors, significant commits, co-change partners, …) and
    # none of them is read here.
    git_res = await session.execute(
        select(
            GitMetadata.file_path,
            GitMetadata.primary_owner_email,
            GitMetadata.primary_owner_name,
            GitMetadata.primary_owner_commit_pct,
        ).where(GitMetadata.repository_id == repo_id)
    )
    all_git = git_res.all()

    if not all_git:
        return {}

    # top_owners: aggregate primary_owner_email across all files
    owner_file_count: dict[str, int] = defaultdict(int)
    owner_name_map: dict[str, str] = {}
    for _file_path, owner_email, owner_name, _pct in all_git:
        email = owner_email or ""
        if email:
            owner_file_count[email] += 1
            if owner_name:
                owner_name_map[email] = owner_name

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

    # knowledge_silos: files where primary owner has > 80% ownership. Derived
    # from the rows already in hand rather than by calling
    # compute_knowledge_silos, which would be a second scan of the same table.
    knowledge_silos = [
        {
            "file_path": file_path,
            "owner_email": owner_email or "",
            "owner_pct": round(float(pct or 0.0), 3),
        }
        for file_path, owner_email, _owner_name, pct in all_git
        if (pct or 0.0) > SILO_OWNER_PCT
    ]

    return {
        "top_owners": top_owners,
        "knowledge_silos": knowledge_silos,
        "onboarding_targets": await compute_onboarding_targets(session, repo_id),
    }
