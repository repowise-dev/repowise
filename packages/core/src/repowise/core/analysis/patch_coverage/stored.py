"""Patch coverage from the coverage an index already stores.

The CLI gate reads a fresh report; every other surface (agent tools, the
REST API, editors) reads what ``coverage add`` or indexing stored. Both end in
:func:`compute_patch_coverage`, so the number cannot differ by surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from ..health.coverage.freshness import coverage_freshness, working_tree_freshness
from .compute import PatchCoverage, PatchScope, compute_patch_coverage
from .risk import IndexFacts

if TYPE_CHECKING:
    from ..health.coverage.discovery import CoverageConfig


async def stored_patch_coverage(
    session: AsyncSession,
    repository_id: str,
    changed: Mapping[str, Iterable[int]],
    *,
    label: str = "",
    head_commit: str | None = None,
    threshold: float | None = None,
    config: CoverageConfig | None = None,
    working_tree_mtime: float | None = None,
) -> PatchCoverage | None:
    """Patch coverage of *changed* against stored coverage; ``None`` when none is stored.

    *head_commit* is the commit the change ends at; coverage measured anywhere
    else is marked ``stale`` in the scope. *config* is the repository's
    ``coverage:`` block: its ``ignore``, ``min_coverable_lines`` and ``gates``
    apply, while ``fail_under`` does not; only the CLI gate passes *threshold*.
    Path-scoped gates are judged only on coverage measured at the change's
    head and with no ``gate_errors`` (invalid ``coverage.gates`` entries), the
    only coverage and config the CLI gate itself judges; otherwise they read
    ``no_data`` and the scope carries the errors.
    For a change that ends in the working tree, pass *working_tree_mtime* (the
    newest modification time of the changed files) instead of *head_commit*: no
    commit names that code, so freshness is whether the last ingest came after
    it (``working_tree_freshness``).
    """
    from repowise.core.persistence.crud import (
        get_coverage_summary,
        load_coverage_for_repo,
        load_file_coverage,
    )

    from ..health.coverage.discovery import CoverageConfig

    rows = await load_coverage_for_repo(session, repository_id, include_covered_lines=False)
    if not rows:
        return None
    summary = await get_coverage_summary(session, repository_id, rows=rows)
    measured = {row.file_path for row in rows}
    wanted = sorted(set(changed) & measured)
    coverage = await load_file_coverage(session, repository_id, file_paths=wanted) if wanted else {}
    commit = summary["ingested_commit_sha"]
    paths = summary["report_paths"]
    if working_tree_mtime is not None:
        freshness = working_tree_freshness(summary["ingested_at"], working_tree_mtime)
    else:
        freshness = coverage_freshness(commit, head_commit)
    cfg = config or CoverageConfig()
    return compute_patch_coverage(
        changed,
        coverage,
        threshold=threshold,
        min_coverable_lines=cfg.min_coverable_lines,
        ignore=cfg.ignore,
        gates=cfg.gates,
        judge_gates=freshness == "current" and not cfg.gate_errors,
        report_paths=measured,
        scope=PatchScope(
            label=label,
            source_formats=tuple(summary["source_formats"]),
            report_path_count=paths["total"] if paths else None,
            unmatched_report_path_count=paths["unmatched"] + paths["ambiguous"] if paths else None,
            mapping_partial=bool(summary["mapping_partial"]),
            measured_commit=commit,
            freshness=freshness,
            config_errors=cfg.gate_errors,
        ),
    )


async def read_index_facts(
    session: AsyncSession, repository_id: str, paths: Iterable[str]
) -> dict[str, IndexFacts]:
    """``{path: IndexFacts}`` for the paths the index has a git row for.

    A path with no row (a file the change adds) is absent, so its risk falls
    back to git.
    """
    from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
    from repowise.core.persistence.crud import get_git_metadata_bulk, get_node_degree_counts_bulk

    meta = await get_git_metadata_bulk(session, repository_id, sorted(set(paths)))
    if not meta:
        return {}
    degrees = await get_node_degree_counts_bulk(
        session, repository_id, list(meta), edge_types=sorted(FILE_DEPENDENCY_EDGE_TYPES)
    )
    return {
        path: IndexFacts(
            hotspot=bool(row.is_hotspot),
            bug_magnet=bool(row.bug_magnet),
            dependents=degrees[path]["in_degree"] if path in degrees else None,
        )
        for path, row in meta.items()
    }
