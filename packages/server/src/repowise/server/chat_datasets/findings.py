"""The ``findings`` and ``dead_code`` datasets: one row per open finding.

Health findings are the ones get_health lists (the registry's shown types,
language gates and file holds, on files the health exclude rule keeps); dead
code is the open findings get_dead_code reads, tiered by the engine's own
confidence floors.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.dead_code.serving import is_safe, serve_findings
from repowise.core.analysis.health.rows import field
from repowise.server.chat_datasets import SUMS, DatasetField, LoadContext, register_dataset
from repowise.server.chat_datasets.files import by_path, place, unique_suffixes

_PLACE_FIELDS = {
    "path": DatasetField("text", "the file's full repository path"),
    "file": DatasetField("text", "the file's shortest unique path suffix, for labels"),
    "package": DatasetField("text", "the package the file belongs to"),
    "directory": DatasetField("text", "a directory one level inside the package"),
}


@register_dataset(
    "findings",
    description="One row per open health finding (what get_health lists).",
    fields={
        "kind": DatasetField("text", "the finding type (a health biomarker)"),
        "severity": DatasetField("text", "critical, high, medium or low"),
        "dimension": DatasetField("text", "defect, maintainability, performance or advisory"),
        **_PLACE_FIELDS,
    },
    basis=("inferred", "health engine"),
    example={"x": "severity", "aggregate": "count"},
    hierarchy=("package", "directory", "file"),
    noun="open findings",
)
async def load_findings(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.core.persistence.crud.analysis.plain_rows import plain_rows
    from repowise.core.persistence.crud.analysis.shown import shown
    from repowise.core.persistence.models import HealthFinding

    q = select(
        HealthFinding.file_path,
        HealthFinding.biomarker_type,
        HealthFinding.severity,
        HealthFinding.dimension,
    ).where(
        HealthFinding.repository_id == ctx.repository.id,
        HealthFinding.status == "open",
        shown("health", HealthFinding.biomarker_type, HealthFinding.file_path),
    )
    return findings_rows(plain_rows(await ctx.session.execute(q)), await ctx.rows("files"))


def findings_rows(found: Iterable[Any], files: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The ``findings`` dataset's rows over open, shown health findings.

    *found* holds ``file_path``, ``biomarker_type``, ``severity`` and
    ``dimension``; *files* is the ``files`` dataset's rows.
    """
    index = by_path(files)
    # Files the health exclude rule drops carry no listed finding either.
    kept = [f for f in found if field(f, "file_path") in index]
    placed = place([field(f, "file_path") for f in kept], index)
    rows = []
    for f in kept:
        path = field(f, "file_path")
        rows.append(
            {
                "kind": field(f, "biomarker_type"),
                "severity": field(f, "severity"),
                "dimension": field(f, "dimension") or "defect",
                "path": path,
                "file": index[path]["file"],
                "package": placed[path][0],
                "directory": placed[path][1],
            }
        )
    return rows


def _tier(confidence: float) -> str:
    from repowise.core.analysis.dead_code.serving import TIER_FLOORS

    return next(t for t, floor in TIER_FLOORS.items() if confidence >= floor)


@register_dataset(
    "dead_code",
    description="One row per open dead-code finding (what get_dead_code reads).",
    fields={
        "kind": DatasetField(
            "text", "unreachable_file, unused_export, unused_internal or zombie_package"
        ),
        "tier": DatasetField("text", "confidence tier: high, medium or low"),
        "confidence": DatasetField("number", "the engine's confidence, 0 to 1", ("avg", "min", "max")),
        "symbol": DatasetField("text", "the unused symbol; null for a whole file or package"),
        "lines": DatasetField("number", "lines the finding covers; null when uncounted", SUMS, unit="lines"),
        "safe_to_delete": DatasetField("bool", "the engine judged it safe to delete"),
        **_PLACE_FIELDS,
    },
    basis=("inferred", "reachability graph"),
    example={"x": "kind", "series": "tier", "aggregate": "count"},
    hierarchy=("package", "directory", "file"),
    noun="dead-code findings",
)
async def load_dead_code(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.core.persistence.crud.analysis.dead_code import get_dead_code_findings
    from repowise.server.mcp_server._helpers import _cross_repo_cut

    found = await get_dead_code_findings(ctx.session, ctx.repository.id)
    served = serve_findings(found, **_cross_repo_cut(ctx.alias))
    return dead_code_rows(served, await ctx.rows("files"))


def dead_code_rows(found: Iterable[Any], files: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The ``dead_code`` dataset's rows over open dead-code findings.

    *found* holds ``file_path``, ``kind``, ``confidence``, ``symbol_name``,
    ``lines`` and ``safe_to_delete`` (``serve_findings`` rows, so the
    cross-repo cut applies); *files* is the ``files`` dataset's rows.
    """
    found = list(found)
    index = by_path(files)
    placed = place([field(f, "file_path") for f in found], index)
    labels = unique_suffixes(placed)
    rows = []
    for f in found:
        path = field(f, "file_path")
        confidence = field(f, "confidence")
        rows.append(
            {
                "kind": field(f, "kind"),
                "tier": _tier(confidence or 0.0),
                "confidence": confidence,
                "symbol": field(f, "symbol_name"),
                "lines": field(f, "lines"),
                "safe_to_delete": is_safe(f),
                "path": path,
                "file": index[path]["file"] if path in index else labels[path],
                "package": placed[path][0],
                "directory": placed[path][1],
            }
        )
    return rows
