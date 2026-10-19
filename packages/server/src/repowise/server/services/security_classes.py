"""Which population a security finding's file belongs to: shipped source,
tests, or docs.

Read from the file's stored ``code_origin`` (the same classification every
health surface narrows on), falling back to the path-only origin for a file
with no health row, such as a history finding on a deleted file. Prose files
carry no origin of their own, so the engine's doc-path rule names them. Test
wins over docs: ``tests/fixtures/leak.md`` is test material. Config counts as
source on purpose: a secret in one ships.

The Security page lede and Overview both read these counts, so they agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.finding_registry import stored_origin
from repowise.core.code_origin import path_origin
from repowise.core.ingestion.git_indexer.fix_shape import is_doc_path
from repowise.core.persistence.models import HealthFileMetric, SecurityFinding

PathClass = Literal["source", "test", "docs"]

#: Paths per ``IN`` read, well under every backend's bound-parameter limit.
_CHUNK = 500


def path_class(path: str, origin: str) -> PathClass:
    """The population of *path*, given its code origin."""
    if origin == "test":
        return "test"
    if origin == "docs_example" or is_doc_path(path):
        return "docs"
    return "source"


async def path_classes(
    session: AsyncSession, repo_id: str, paths: Iterable[str]
) -> dict[str, PathClass]:
    """``{path: class}`` for *paths*, from their stored origins."""
    wanted = sorted(set(paths))
    origins: dict[str, str] = {}
    for i in range(0, len(wanted), _CHUNK):
        rows = await session.execute(
            select(
                HealthFileMetric.file_path, HealthFileMetric.code_origin, HealthFileMetric.is_test
            ).where(
                HealthFileMetric.repository_id == repo_id,
                HealthFileMetric.file_path.in_(wanted[i : i + _CHUNK]),
            )
        )
        for path, origin, is_test in rows.all():
            origins[path] = stored_origin(origin, is_test)
    return {p: path_class(p, origins.get(p) or path_origin(p)) for p in wanted}


async def security_class_counts(session: AsyncSession, repo_id: str) -> dict[str, object]:
    """Every stored finding counted by class, plus the high ones in source."""
    groups = (
        await session.execute(
            select(SecurityFinding.file_path, SecurityFinding.severity, func.count())
            .where(SecurityFinding.repository_id == repo_id)
            .group_by(SecurityFinding.file_path, SecurityFinding.severity)
        )
    ).all()
    classes = await path_classes(session, repo_id, (path for path, _, _ in groups))
    counts: dict[str, int] = {"source": 0, "test": 0, "docs": 0}
    source_high = 0
    for path, severity, n in groups:
        cls = classes[path]
        counts[cls] += n
        if cls == "source" and severity == "high":
            source_high += n
    return {"total": sum(counts.values()), "class_counts": counts, "source_high": source_high}
