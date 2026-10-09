"""Owner / contributor aggregation service.

Fetches ``GitMetadata``, ``DeadCodeFinding`` and ``GitCommit`` rows and folds them with
:func:`repowise.core.analysis.owners.aggregate_owners`. The exposed ``key`` is
URL-safe only at the router layer; the fold deals with the canonical form.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis import owners as _fold
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.owners import (
    OwnerAccumulator,
    module_share,
    owner_key,
    silo_modules,
)
from repowise.core.persistence.crud import code_file_rows
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitCommit,
    GitMetadata,
    Repository,
)

_OwnerAccumulator = OwnerAccumulator


async def aggregate_owners(
    session: AsyncSession, repo_id: str
) -> tuple[dict[str, OwnerAccumulator], dict[str, int]]:
    """Per-owner accumulators and files per module for *repo_id*.

    Git rows stay ORM entities: ``file_meta`` hands them back to the router.
    """

    git_rows = (
        (await session.execute(select(GitMetadata).where(code_file_rows(repo_id))))
        .scalars()
        .all()
    )
    dead_rows = (
        await session.execute(
            select(
                DeadCodeFinding.primary_owner,
                DeadCodeFinding.file_path,
                DeadCodeFinding.lines,
            ).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.kind.not_in(excluded_types()),
            )
        )
    ).all()
    commit_rows = (
        await session.execute(
            select(
                GitCommit.sha,
                GitCommit.author_name,
                GitCommit.author_email,
                GitCommit.committed_at,
            ).where(GitCommit.repository_id == repo_id)
        )
    ).all()
    total_commits = await session.scalar(
        select(Repository.total_commit_count).where(Repository.id == repo_id)
    )
    return _fold.aggregate_owners(git_rows, dead_rows, commit_rows, total_commits)


__all__ = [
    "OwnerAccumulator",
    "_OwnerAccumulator",
    "aggregate_owners",
    "module_share",
    "owner_key",
    "silo_modules",
]
