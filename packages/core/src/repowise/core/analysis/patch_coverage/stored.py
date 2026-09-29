"""Patch coverage from the coverage an index already stores.

The CLI gate reads a fresh report; every other surface (agent tools, the
REST API, editors) reads what ``coverage add`` or indexing stored. Both end in
:func:`compute_patch_coverage`, so the number cannot differ by surface.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..health.coverage.discovery import CoverageScope
from ..health.coverage.freshness import coverage_freshness, working_tree_freshness
from .compute import PatchCoverage, PatchScope, compute_patch_coverage
from .delta import ProjectDelta, ProjectTotals, incomparable_reasons
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
    it (``working_tree_freshness``). :func:`attach_history_delta` adds the
    project delta.
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


async def attach_history_delta(
    session: AsyncSession, repository_id: str, pc: PatchCoverage, base_commit: str | None
) -> PatchCoverage:
    """*pc* with ``project``: the ingest at *base_commit* against the newest one.

    Only for stored coverage current for the change (the newest ingest is then
    the head's figure); history basis, never gated here. *pc* unchanged otherwise.
    """
    if not base_commit or pc.scope.freshness != "current":
        return pc
    return replace(pc, project=await history_delta(session, repository_id, base_commit))


def ingest_totals(row: Any) -> ProjectTotals | None:
    """A stored ingest's repo-wide figures; ``None`` for a row written before them."""
    if row.covered_lines is None or row.total_lines is None:
        return None
    return ProjectTotals(row.covered_lines, row.total_lines)


def ingest_scope(row: Any) -> CoverageScope | None:
    """What a stored ingest measured; ``None`` for a row written before scopes were kept."""
    try:
        return CoverageScope.from_dict(json.loads(row.scope_json)) if row.scope_json else None
    except ValueError:
        return None


async def history_delta(
    session: AsyncSession,
    repository_id: str,
    base_commit: str,
    *,
    max_drop: float | None = None,
) -> ProjectDelta | None:
    """The ingest at *base_commit* against the newest ingest.

    ``None`` when no ingest was measured at *base_commit*, or when it is the
    head ingest itself (a working-tree change measured over its own base).
    """
    from repowise.core.persistence.crud import load_ingest_at_commit, load_newest_ingest

    base = await load_ingest_at_commit(session, repository_id, base_commit)
    head = await load_newest_ingest(session, repository_id)
    if base is None or head is None or base.id == head.id:
        return None
    return ProjectDelta(
        base=ingest_totals(base),
        head=ingest_totals(head),
        basis="history",
        base_commit=base_commit,
        head_commit=head.ingested_commit_sha,
        max_drop=max_drop,
        incomparable=incomparable_reasons(ingest_scope(base), ingest_scope(head)),
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
