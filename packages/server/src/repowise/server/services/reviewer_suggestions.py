"""Reviewer suggestion service.

Fetches the git rows of the changed files and of their co-change partners,
then ranks authors with :func:`repowise.core.analysis.reviewers.suggest_reviewers`.
The output is intentionally short (top 10): PRs with 30 suggested reviewers
are no better than zero.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis import reviewers as _fold
from repowise.core.persistence.models import GitMetadata
from repowise.server.schemas import ReviewerSuggestion


async def suggest_reviewers(
    session: AsyncSession,
    repo_id: str,
    paths: list[str],
    limit: int = 10,
) -> list[ReviewerSuggestion]:
    if not paths:
        return []

    direct_rows = (
        (
            await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path.in_(paths),
                )
            )
        )
        .scalars()
        .all()
    )

    partner_paths = _fold.cochange_paths(direct_rows)
    cochange_rows = []
    if partner_paths:
        cochange_rows = (
            (
                await session.execute(
                    select(GitMetadata).where(
                        GitMetadata.repository_id == repo_id,
                        GitMetadata.file_path.in_(partner_paths),
                    )
                )
            )
            .scalars()
            .all()
        )

    return [
        ReviewerSuggestion(**s)
        for s in _fold.suggest_reviewers(direct_rows, cochange_rows, limit=limit)
    ]
