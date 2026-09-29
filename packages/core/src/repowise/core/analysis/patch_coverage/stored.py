"""Patch coverage from the coverage an index already stores.

The CLI gate reads a fresh report; every other surface (agent tools, the
REST API, editors) reads what ``coverage add`` or indexing stored. Both end in
:func:`compute_patch_coverage`, so the number cannot differ by surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from ..health.coverage.freshness import coverage_freshness
from .compute import PatchCoverage, PatchScope, compute_patch_coverage

if TYPE_CHECKING:
    from ..health.coverage.discovery import PathGate


async def stored_patch_coverage(
    session: AsyncSession,
    repository_id: str,
    changed: Mapping[str, Iterable[int]],
    *,
    label: str = "",
    head_commit: str | None = None,
    threshold: float | None = None,
    min_coverable_lines: int | None = None,
    ignore: Sequence[str] = (),
    gates: Sequence[PathGate] = (),
    config_errors: Sequence[str] = (),
) -> PatchCoverage | None:
    """Patch coverage of *changed* against stored coverage; ``None`` when none is stored.

    *head_commit* is the commit the change ends at; coverage measured anywhere
    else is marked ``stale`` in the scope. *min_coverable_lines*, *ignore* and
    *gates* come from ``coverage:`` config; only the CLI gate passes *threshold*.
    Path-scoped gates are judged only on coverage measured at the change's
    head and with no *config_errors* (invalid ``coverage.gates`` entries),
    the only coverage and config the CLI gate itself judges; otherwise they
    read ``no_data`` and the scope carries the errors.
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
    paths = summary["report_paths"]
    freshness = coverage_freshness(commit, head_commit)
    return compute_patch_coverage(
        changed,
        coverage,
        threshold=threshold,
        min_coverable_lines=min_coverable_lines,
        ignore=ignore,
        gates=gates,
        judge_gates=freshness == "current" and not config_errors,
        report_paths=measured,
        scope=PatchScope(
            label=label,
            source_formats=tuple(summary["source_formats"]),
            report_path_count=paths["total"] if paths else None,
            unmatched_report_path_count=paths["unmatched"] + paths["ambiguous"] if paths else None,
            mapping_partial=bool(summary["mapping_partial"]),
            measured_commit=commit,
            freshness=freshness,
            config_errors=tuple(config_errors),
        ),
    )
