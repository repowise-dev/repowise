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
    from repowise.core.persistence.crud import (
        get_coverage_summary,
        load_coverage_for_repo,
        load_file_coverage,
    )

    rows = await load_coverage_for_repo(session, repository_id, include_covered_lines=False)
    if not rows:
        return None
    summary = await get_coverage_summary(session, repository_id, rows=rows)
    measured = {row.file_path for row in rows}
    wanted = sorted(set(changed) & measured)
    coverage = await load_file_coverage(session, repository_id, file_paths=wanted) if wanted else {}
    commit = summary["ingested_commit_sha"]
    return compute_patch_coverage(
        changed,
        coverage,
        threshold=threshold,
        report_paths=measured,
        scope=PatchScope(
            label=label,
            source_formats=(summary["source_format"],) if summary["source_format"] else (),
            mapping_partial=bool(summary["mapping_partial"]),
            measured_commit=commit,
            freshness=coverage_freshness(commit, head_commit),
        ),
    )
