"""Patch coverage from the coverage an index already stores.

The CLI gate reads a fresh report; every other surface (agent tools, the
REST API, editors) reads what ``coverage add`` or indexing stored. Both end in
:func:`compute_patch_coverage`, so the number cannot differ by surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from ..health.coverage.freshness import coverage_freshness
from .compute import PatchCoverage, PatchScope, compute_patch_coverage


async def stored_patch_coverage(
    session: AsyncSession,
    repository_id: str,
    changed: Mapping[str, Iterable[int]],
    *,
    label: str = "",
    head_commit: str | None = None,
    threshold: float | None = None,
) -> PatchCoverage | None:
    """Patch coverage of *changed* against stored coverage; ``None`` when none is stored.

    *head_commit* is the commit the change ends at; coverage measured anywhere
    else is marked ``stale`` in the scope.
    """
    from repowise.core.persistence.crud import load_coverage_for_repo, load_file_coverage

    rows = await load_coverage_for_repo(session, repository_id, include_covered_lines=False)
    if not rows:
        return None
    measured = {row.file_path for row in rows}
    latest = max(rows, key=lambda r: r.ingested_at)
    wanted = sorted(set(changed) & measured)
    coverage = await load_file_coverage(session, repository_id, file_paths=wanted) if wanted else {}
    return compute_patch_coverage(
        changed,
        coverage,
        threshold=threshold,
        report_paths=measured,
        scope=PatchScope(
            label=label,
            source_formats=tuple(dict.fromkeys(row.source_format for row in rows)),
            report_path_count=len(rows),
            measured_commit=latest.ingested_commit_sha,
            freshness=coverage_freshness(latest.ingested_commit_sha, head_commit),
        ),
    )
