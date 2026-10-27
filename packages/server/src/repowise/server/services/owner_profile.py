"""Owner / contributor aggregation service.

Fetches ``GitMetadata``, ``DeadCodeFinding`` and ``GitCommit`` rows and folds them with
:func:`repowise.core.analysis.owners.aggregate_owners`. The exposed ``key`` is
URL-safe only at the router layer; the fold deals with the canonical form.

The fold reads every git row of the repository, so it and everything derived
from it (the summary, the knowledge matrix) are built once per index state
through :func:`per_index` and shared read-only across requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis import owners as _fold
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.rows import json_field
from repowise.core.analysis.knowledge_matrix import build_knowledge_matrix
from repowise.core.analysis.owners import (
    OwnerAccumulator,
    module_share,
    owner_key,
    people_resolver,
    silo_modules,
)
from repowise.core.analysis.owners_summary import build_owner_summary
from repowise.core.author_identity import IdentityResolver
from repowise.core.persistence.crud import code_file_rows
from repowise.core.persistence.crud.analysis.shown import ungated
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitCommit,
    GitMetadata,
    HealthFileMetric,
    Repository,
)
from repowise.server.mcp_server._graph_files import per_index

_OwnerAccumulator = OwnerAccumulator

# What the fold, the profile shaper and the matrix read from a git row.
_GIT_COLUMNS = (
    GitMetadata.file_path,
    GitMetadata.primary_owner_name,
    GitMetadata.primary_owner_email,
    GitMetadata.primary_owner_commit_pct,
    GitMetadata.top_authors_json,
    GitMetadata.commit_categories_json,
    GitMetadata.commit_count_90d,
    GitMetadata.lines_added_90d,
    GitMetadata.lines_deleted_90d,
    GitMetadata.first_commit_at,
    GitMetadata.last_commit_at,
    GitMetadata.is_hotspot,
    GitMetadata.bus_factor,
    GitMetadata.churn_percentile,
)
_JSON_COLUMNS = {"top_authors_json": [], "commit_categories_json": {}}


@dataclass(frozen=True)
class OwnerData:
    """One repository's owner fold and the inputs it was built from. Read-only."""

    git_rows: list[dict[str, Any]]
    resolve: IdentityResolver
    accs: dict[str, OwnerAccumulator]
    module_totals: dict[str, int]


async def _git_rows(session: AsyncSession, repo_id: str) -> list[dict[str, Any]]:
    """Code-file git rows as plain dicts, JSON columns decoded once."""
    result = await session.execute(select(*_GIT_COLUMNS).where(code_file_rows(repo_id)))
    rows = []
    for mapping in result.mappings():
        row = dict(mapping)
        for name, default in _JSON_COLUMNS.items():
            row[name] = json_field(row, name, default)
        rows.append(row)
    return rows


async def _load_owner_data(session: AsyncSession, repo_id: str) -> OwnerData:
    git_rows = await _git_rows(session, repo_id)
    dead_rows = (
        await session.execute(
            select(
                DeadCodeFinding.primary_owner,
                DeadCodeFinding.file_path,
                DeadCodeFinding.lines,
            ).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.kind.not_in(excluded_types()),
                ungated("dead_code", DeadCodeFinding.file_path, DeadCodeFinding.kind),
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
    resolve = people_resolver(git_rows, commit_rows)
    accs, module_totals = _fold.aggregate_owners(
        git_rows, dead_rows, commit_rows, total_commits, resolve
    )
    return OwnerData(git_rows, resolve, accs, module_totals)


async def owner_data(session: AsyncSession, repo_id: str) -> OwnerData:
    """The owner fold for *repo_id*, built once per index state."""
    return await per_index(
        session, repo_id, "owners.data", lambda: _load_owner_data(session, repo_id)
    )


async def aggregate_owners(
    session: AsyncSession, repo_id: str
) -> tuple[dict[str, OwnerAccumulator], dict[str, int]]:
    """Per-owner accumulators and files per module for *repo_id*."""
    data = await owner_data(session, repo_id)
    return data.accs, data.module_totals


async def owner_summary(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """The repository's knowledge-risk summary, built once per index state."""
    # Read first: ``per_index`` holds a lock that is not reentrant.
    data = await owner_data(session, repo_id)

    async def build() -> dict[str, Any]:
        return build_owner_summary(data.git_rows, data.resolve, data.accs)

    return await per_index(session, repo_id, "owners.summary", build)


async def knowledge_matrix(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """Every file's knowledge row, built once per index state."""
    data = await owner_data(session, repo_id)

    async def build() -> dict[str, Any]:
        result = await session.execute(
            select(HealthFileMetric.file_path, HealthFileMetric.score, HealthFileMetric.nloc).where(
                HealthFileMetric.repository_id == repo_id
            )
        )
        health = {path: (score, nloc) for path, score, nloc in result}
        return build_knowledge_matrix(data.git_rows, data.resolve, data.accs, health)

    return await per_index(session, repo_id, "owners.knowledge_matrix", build)


__all__ = [
    "OwnerAccumulator",
    "OwnerData",
    "_OwnerAccumulator",
    "aggregate_owners",
    "knowledge_matrix",
    "module_share",
    "owner_data",
    "owner_key",
    "owner_summary",
    "silo_modules",
]
