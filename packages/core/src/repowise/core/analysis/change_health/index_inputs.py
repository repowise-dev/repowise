"""What the index knows that a file's bytes do not, for scoring files outside a full pass.

A pass over a few files from disk has no git history, coverage, test pairing or
blame of its own. The index holds all four from its last run, so a file scored
with them reads as the next ``repowise update`` would score it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...persistence.models import HealthFileMetric, HealthFinding
from ..health.history_refresh import BLAME_MARKERS, git_meta_rows_to_map
from .index_context import index_dependents


@dataclass(frozen=True)
class IndexInputs:
    """The index's history, coverage, test pairing, blame findings, dependents
    and clone state."""

    git_meta: dict[str, dict] = field(default_factory=dict)
    coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    tested_files: frozenset[str] = frozenset()
    blame_findings: dict[str, list[Any]] = field(default_factory=dict)
    dependents: dict[str, int] | None = None
    #: The index's ``.repowise``, whose clone pair state lets a file's clones
    #: with files the pass does not read still count.
    clone_index_dir: Path | None = None

    @property
    def has_history(self) -> bool:
        return bool(self.git_meta)


async def load_index_inputs(
    session: AsyncSession, repository_id: str, clone_index_dir: Path | None = None
) -> IndexInputs:
    """Every stored input a file's score reads beyond its own bytes."""
    from ...persistence.crud import get_all_git_metadata, load_coverage_map

    git_rows = await get_all_git_metadata(session, repository_id)
    tested = await session.execute(
        select(HealthFileMetric.file_path).where(
            HealthFileMetric.repository_id == repository_id,
            HealthFileMetric.has_test_file.is_(True),
        )
    )
    return IndexInputs(
        git_meta=git_meta_rows_to_map(git_rows.values()),
        coverage=await load_coverage_map(session, repository_id),
        tested_files=frozenset(tested.scalars().all()),
        blame_findings=await _blame_findings(session, repository_id),
        dependents=await index_dependents(session, repository_id),
        clone_index_dir=clone_index_dir,
    )


async def _blame_findings(session: AsyncSession, repository_id: str) -> dict[str, list[Any]]:
    """Stored blame-based findings by path, replayed since no blame walk runs here."""
    rows = await session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.biomarker_type.in_(BLAME_MARKERS),
        )
    )
    out: dict[str, list[Any]] = {}
    for finding in rows.scalars():
        out.setdefault(finding.file_path, []).append(finding)
    return out
