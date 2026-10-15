"""The ``findings`` and ``dead_code`` datasets: one row per open finding.

Health findings are the ones get_health lists (the registry's shown types,
language gates and file holds, on files the health exclude rule keeps); dead
code is the open findings get_dead_code reads, tiered by the engine's own
confidence floors.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.server.chat_datasets import SUMS, DatasetField, LoadContext, register_dataset
from repowise.server.chat_datasets.files import files_index, place, unique_suffixes

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
    files = await files_index(ctx)
    # Files the health exclude rule drops carry no listed finding either.
    found = [f for f in plain_rows(await ctx.session.execute(q)) if f.file_path in files]
    placed = place([f.file_path for f in found], files)
    return [
        {
            "kind": f.biomarker_type,
            "severity": f.severity,
            "dimension": f.dimension or "defect",
            "path": f.file_path,
            "file": files[f.file_path]["file"],
            "package": placed[f.file_path][0],
            "directory": placed[f.file_path][1],
        }
        for f in found
    ]


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

    found = await get_dead_code_findings(ctx.session, ctx.repository.id)
    files = await files_index(ctx)
    placed = place([f.file_path for f in found], files)
    labels = unique_suffixes(placed)
    return [
        {
            "kind": f.kind,
            "tier": _tier(f.confidence or 0.0),
            "confidence": f.confidence,
            "symbol": f.symbol_name,
            "lines": f.lines,
            "safe_to_delete": bool(f.safe_to_delete),
            "path": f.file_path,
            "file": files[f.file_path]["file"] if f.file_path in files else labels[f.file_path],
            "package": placed[f.file_path][0],
            "directory": placed[f.file_path][1],
        }
        for f in found
    ]
