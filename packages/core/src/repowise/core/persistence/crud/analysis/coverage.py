"""CRUD operations for coverage files (repowise persistence layer)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, overload

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import CoverageFile, CoverageIngest, _new_uuid

if TYPE_CHECKING:
    from repowise.core.analysis.health.coverage.discovery import CoverageProvenance
    from repowise.core.analysis.health.coverage.freshness import FreshnessStatus
    from repowise.core.analysis.health.coverage.model import FileCoverage
from .._shared import _BATCH_SIZE


async def save_coverage_files(
    session: AsyncSession,
    repository_id: str,
    files: list[Any],
    *,
    source_format: str,
    ingested_commit_sha: str | None = None,
    provenance: CoverageProvenance | None = None,
) -> None:
    """Replace coverage rows for *repository_id* with *files*, and record the ingest.

    Mirrors the delete-then-insert pattern used by the health writers.
    *files* is a list of ``FileCoverage`` dataclasses (or dicts with the
    same shape). *provenance*, from a writer that resolved the report
    itself, supplies the formats, the path counts and ``mapping_partial``
    (fewer than half the report's files mapped to the repo tree, #1746: a
    property of the ingest, stamped on every row). Without it the ingest
    records ``source_format`` alone and its path counts stay unknown.
    """
    from repowise.core.analysis.health.coverage.discovery import CoverageProvenance

    p = provenance or CoverageProvenance()
    for model in (CoverageFile, CoverageIngest):
        existing = await session.execute(select(model).where(model.repository_id == repository_id))
        for row in existing.scalars().all():
            await session.delete(row)
    await session.flush()

    session.add(
        CoverageIngest(
            id=_new_uuid(),
            repository_id=repository_id,
            source_formats_json=json.dumps(list(p.source_formats) or [source_format]),
            report_path_count=p.report_path_count,
            matched_path_count=p.matched_path_count,
            unmatched_path_count=p.unmatched_path_count,
            ambiguous_path_count=p.ambiguous_path_count,
            unmatched_sample_json=json.dumps(list(p.unmatched_sample)),
            mapping_partial=p.mapping_partial,
            ingested_commit_sha=ingested_commit_sha,
        )
    )

    for i in range(0, len(files), _BATCH_SIZE):
        for f in files[i : i + _BATCH_SIZE]:
            session.add(
                CoverageFile(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    source_format=source_format,
                    ingested_commit_sha=ingested_commit_sha,
                    mapping_partial=p.mapping_partial,
                    **_row_columns(f),
                )
            )
        await session.flush()


#: Columns the ingest sets for every row, so a per-file input never overrides them.
_INGEST_COLUMNS = frozenset(
    {"id", "repository_id", "source_format", "ingested_commit_sha", "mapping_partial"}
)


def _row_columns(f: Any) -> dict[str, Any]:
    """Per-file column values from a ``FileCoverage`` or a dict of the same shape."""
    if hasattr(f, "file_path"):
        return {
            "file_path": f.file_path,
            "line_coverage_pct": float(f.line_coverage_pct),
            "branch_coverage_pct": (
                float(f.branch_coverage_pct) if f.branch_coverage_pct is not None else None
            ),
            "covered_lines_json": json.dumps(list(f.covered_lines or [])),
            "total_coverable_lines": int(f.total_coverable_lines or 0),
            "covered_line_count": _covered_count(f),
            "coverable_lines_json": json.dumps(list(getattr(f, "coverable_lines", None) or [])),
        }
    data = dict(f)
    for key in ("covered_lines", "coverable_lines"):
        if key in data:
            data[f"{key}_json"] = json.dumps(list(data.pop(key) or []))
    columns = {
        k: v for k, v in data.items() if k not in _INGEST_COLUMNS and hasattr(CoverageFile, k)
    }
    if columns.get("covered_line_count") is None:
        columns["covered_line_count"] = _derived_count(
            columns.get("line_coverage_pct"), columns.get("total_coverable_lines")
        )
    return columns


#: Every column of ``CoverageFile`` except the two line-set blobs
#: (``covered_lines_json``, ``coverable_lines_json``). They dominate the table:
#: the covered set alone was 467 KB of the 549 KB stored for this repo's 1,401
#: rows, and only line-level readers need them.
_COVERAGE_SCALAR_COLUMNS = (
    CoverageFile.file_path,
    CoverageFile.source_format,
    CoverageFile.line_coverage_pct,
    CoverageFile.branch_coverage_pct,
    CoverageFile.total_coverable_lines,
    CoverageFile.covered_line_count,
    CoverageFile.mapping_partial,
    CoverageFile.ingested_at,
    CoverageFile.ingested_commit_sha,
)


@overload
async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = ...,
    include_covered_lines: Literal[True] = ...,
) -> list[CoverageFile]: ...


@overload
async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = ...,
    include_covered_lines: Literal[False],
) -> list[Any]: ...


async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
    include_covered_lines: bool = True,
) -> list[Any]:
    """Coverage rows for a repo, optionally scoped to *file_paths*.

    ``include_covered_lines=False`` returns ``Row`` objects carrying every
    column except the two line-set blobs. They are attribute-accessed exactly
    like the ORM entities, so a caller that reads named fields needs no change
    — but a caller that touches ``covered_lines_json`` or
    ``coverable_lines_json`` must ask for them.
    """
    q = (
        select(CoverageFile)
        if include_covered_lines
        else select(*_COVERAGE_SCALAR_COLUMNS)
    ).where(CoverageFile.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(CoverageFile.file_path.in_(file_paths))
    result = await session.execute(q)
    if include_covered_lines:
        return list(result.scalars().all())
    return list(result.all())


def _line_list(raw: str | None) -> list[int]:
    try:
        return [int(n) for n in json.loads(raw)] if raw else []
    except (ValueError, TypeError):
        return []


def _covered_count(row: Any) -> int:
    """Covered lines in a row or ``FileCoverage``: its count, else derived.

    Parsed reports carry the count. Derivation from the rounded percentage is
    left for rows written before the column and for sources that state only a
    percentage; it is exact below 10,000 coverable lines, where two-decimal
    rounding cannot move the product by half a line.
    """
    count = getattr(row, "covered_line_count", None)
    if count is not None:
        return int(count)
    return _derived_count(row.line_coverage_pct, row.total_coverable_lines)


def _derived_count(pct: float | None, total: int | None) -> int:
    return round(float(pct or 0.0) / 100.0 * int(total or 0))


def coverage_row_dict(row: Any, *, include_covered_lines: bool) -> dict[str, Any]:
    """A stored coverage row as every surface sends it.

    ``include_covered_lines=False`` omits the per-line array, and is required
    for a row read with ``include_covered_lines=False``: that row carries no
    ``covered_lines_json`` at all. ``covered_line_count`` is sent either way.
    """
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
    }
    if include_covered_lines:
        out["covered_lines"] = _line_list(row.covered_lines_json)
    out["total_coverable_lines"] = row.total_coverable_lines
    out["covered_line_count"] = _covered_count(row)
    out["ingested_at"] = row.ingested_at.isoformat() if row.ingested_at else None
    out["ingested_commit_sha"] = row.ingested_commit_sha
    return out


def coverage_by_module(rows: list[Any]) -> list[dict[str, Any]]:
    """Stored rows rolled up by directory, worst covered first.

    *rows* must be every row for the repo, for the same reason as
    :func:`get_coverage_summary`.
    """
    modules: dict[str, dict[str, int]] = {}
    for r in rows:
        mod = r.file_path.rsplit("/", 1)[0] if "/" in r.file_path else "(root)"
        bucket = modules.setdefault(mod, {"covered": 0, "total": 0, "files": 0})
        bucket["files"] += 1
        bucket["total"] += r.total_coverable_lines
        bucket["covered"] += _covered_count(r)
    out = [
        {
            "module": name,
            "files": v["files"],
            "covered_lines": v["covered"],
            "total_lines": v["total"],
            "line_coverage_pct": round(v["covered"] / v["total"] * 100.0, 2) if v["total"] else 0.0,
        }
        for name, v in modules.items()
    ]
    out.sort(key=lambda x: x["line_coverage_pct"])
    return out


def file_coverage_from_row(row: Any) -> FileCoverage:
    """A stored row as the parsers' ``FileCoverage``, the one row-to-model conversion."""
    from repowise.core.analysis.health.coverage.model import FileCoverage

    return FileCoverage(
        file_path=row.file_path,
        line_coverage_pct=row.line_coverage_pct,
        branch_coverage_pct=row.branch_coverage_pct,
        covered_lines=_line_list(row.covered_lines_json),
        total_coverable_lines=row.total_coverable_lines or 0,
        coverable_lines=_line_list(getattr(row, "coverable_lines_json", None)),
        covered_line_count=getattr(row, "covered_line_count", None),
    )


async def load_file_coverage(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
) -> dict[str, FileCoverage]:
    """Stored coverage as ``{path: FileCoverage}``, line sets included."""
    rows = await load_coverage_for_repo(session, repository_id, file_paths=file_paths)
    return {row.file_path: file_coverage_from_row(row) for row in rows}


async def load_coverage_map(session: AsyncSession, repository_id: str) -> dict[str, dict]:
    """Stored coverage in the shape ``HealthAnalyzer`` takes as ``coverage_map``."""
    from repowise.core.analysis.health.coverage.model import coverage_map_entry

    # Health scoring never reads the executable-line set, so its blob is not
    # loaded (it is at least as large as the covered set).
    result = await session.execute(
        select(*_COVERAGE_SCALAR_COLUMNS, CoverageFile.covered_lines_json).where(
            CoverageFile.repository_id == repository_id
        )
    )
    return {
        row.file_path: coverage_map_entry(file_coverage_from_row(row), row.source_format)
        for row in result.all()
    }


def empty_coverage_summary() -> dict[str, Any]:
    """The summary's shape when nothing is stored: no measurement, not zero percent."""
    return {
        "file_count": 0,
        "covered_lines": 0,
        "total_lines": 0,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "source_format": None,
        "source_formats": [],
        "mapping_partial": None,
        "ingested_at": None,
        "ingested_commit_sha": None,
        "report_paths": None,
        "freshness": None,
    }


def _report_paths(ingest: CoverageIngest | None) -> dict[str, Any] | None:
    """How the report's own file entries mapped; ``None`` when the ingest did not record it."""
    if ingest is None or ingest.report_path_count is None:
        return None
    try:
        sample = [str(p) for p in json.loads(ingest.unmatched_sample_json or "[]")]
    except (ValueError, TypeError):
        sample = []
    return {
        "total": ingest.report_path_count,
        "matched": ingest.matched_path_count or 0,
        "unmatched": ingest.unmatched_path_count or 0,
        "ambiguous": ingest.ambiguous_path_count or 0,
        "unmatched_sample": sample,
    }


def _source_formats(ingest: CoverageIngest | None, fallback: str | None) -> list[str]:
    if ingest is not None:
        try:
            formats = [str(f) for f in json.loads(ingest.source_formats_json or "[]")]
        except (ValueError, TypeError):
            formats = []
        if formats:
            return formats
    return [fallback] if fallback else []


async def get_coverage_summary(
    session: AsyncSession,
    repository_id: str,
    *,
    rows: list[Any] | None = None,
    reference_commit: str | None = None,
) -> dict[str, Any]:
    """Repo-level coverage aggregate. Returns an empty shape when no rows.

    *rows* lets a caller that has already loaded the coverage table hand it
    over instead of paying for a second full read. It must be exactly what this
    function would have read itself — ``load_coverage_for_repo(repository_id)``,
    i.e. **every** row for the repo. Handing over a subset (a ``file_paths=``
    read, say) would silently report that subset's coverage as the repo's.

    *reference_commit* is the commit the caller is describing (the indexed
    tree, usually); ``freshness`` says whether the coverage was measured there
    (``coverage_freshness``). ``report_paths`` is how the report's own entries
    mapped to the repository, ``None`` for an ingest that did not record it.
    """
    from repowise.core.analysis.health.coverage.freshness import coverage_freshness

    if rows is None:
        rows = await load_coverage_for_repo(
            session, repository_id, include_covered_lines=False
        )
    if not rows:
        return empty_coverage_summary()
    ingest = (
        await session.execute(
            select(CoverageIngest).where(CoverageIngest.repository_id == repository_id)
        )
    ).scalar_one_or_none()
    covered = 0
    total = 0
    branch_pcts: list[float] = []
    branch_weights: list[int] = []
    for r in rows:
        covered += _covered_count(r)
        total += r.total_coverable_lines
        if r.branch_coverage_pct is not None:
            branch_pcts.append(r.branch_coverage_pct)
            branch_weights.append(max(r.total_coverable_lines, 1))
    line_pct = (covered / total * 100.0) if total else 0.0
    branch_pct: float | None
    if branch_pcts:
        wsum = sum(branch_weights)
        branch_pct = sum(p * w for p, w in zip(branch_pcts, branch_weights, strict=True)) / wsum
    else:
        branch_pct = None
    latest = max(rows, key=lambda r: r.ingested_at)
    # A partial ingest is a whole-table property: every row of the latest
    # delete-then-insert batch carries the same flag, so any row reports the
    # table's. Rows written before the flag existed read False (column
    # default), which is correct for complete legacy ingests.
    mapping_partial = bool(getattr(latest, "mapping_partial", False))
    status: FreshnessStatus = coverage_freshness(latest.ingested_commit_sha, reference_commit)
    return {
        "file_count": len(rows),
        "covered_lines": covered,
        "total_lines": total,
        "line_coverage_pct": round(line_pct, 2),
        "branch_coverage_pct": round(branch_pct, 2) if branch_pct is not None else None,
        "source_format": latest.source_format,
        "source_formats": _source_formats(ingest, latest.source_format),
        "mapping_partial": mapping_partial,
        "ingested_at": latest.ingested_at,
        "ingested_commit_sha": latest.ingested_commit_sha,
        "report_paths": _report_paths(ingest),
        "freshness": {"status": status, "indexed_commit": reference_commit},
    }
