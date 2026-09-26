"""The coverage block for get_health: row serialization and measurement decay."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.analysis.health.coverage import decay_since, measurement_ref
from repowise.server.mcp_server.tool_health.paging import Pager


def _coverage_block(
    rows: list[Any],
    summary: dict[str, Any],
    *,
    scoped: bool,
    pager: Pager,
    repo_path: str,
) -> dict[str, Any]:
    """Per-file coverage rows plus the stored repo-wide summary.

    Drop the bulky covered-lines arrays from dashboard mode; full
    detail is available in targeted mode.
    """
    if scoped:
        selected_coverage = pager.bound(rows, "coverage.files")
        coverage_payload = [_serialize_coverage_row(r) for r in selected_coverage]
        _attach_coverage_decay(coverage_payload, selected_coverage, repo_path)
    else:
        # Built narrow, not built wide and subtracted from. These rows came
        # back without the column at all (see the read in ``loading``).
        full_coverage_payload = [_serialize_coverage_row(r, covered_lines=False) for r in rows]
        coverage_payload = pager.bound(full_coverage_payload, "coverage.files")
    # ``ingested_at`` is a datetime on the summary too — coerce.
    if summary.get("ingested_at") is not None:
        summary = {**summary, "ingested_at": summary["ingested_at"].isoformat()}
    block: dict[str, Any] = {
        "summary": summary,
        "files": coverage_payload,
        "files_total": len(rows),
        "files_emitted": len(coverage_payload),
    }
    if len(coverage_payload) < len(rows):
        block["files_reduced_reason"] = "limit"
    return block


def _attach_coverage_decay(payload: list[dict[str, Any]], rows: list[Any], repo_path: str) -> None:
    """Add a ``decay`` block to each coverage row, in place.

    The stored percentage is a measurement taken at one commit and never
    recomputed, so on a file under active development it can describe code that
    no longer exists. ``decay`` says how much of that measurement still holds:
    ``confirmed`` covered lines are unchanged since the report, ``invalidated``
    ones have moved and are now unknown rather than uncovered.

    Targeted mode only. Dashboard mode declines ``covered_lines_json`` at the
    read (see the load above), and re-reading every blob to compute drift for a
    list nobody drilled into would undo that saving.

    Silent when the measurement cannot be placed in history, when git cannot
    read the range, or when the report predates every commit. A missing block
    means "not checked", which is why it is absent rather than zero: a zero
    would read as a freshness claim.
    """
    if not rows:
        return
    first = rows[0]
    ref = measurement_ref(
        repo_path,
        getattr(first, "ingested_commit_sha", None),
        getattr(first, "ingested_at", None),
    )
    if ref is None:
        return
    covered_by_file = {
        entry["file_path"]: set(entry.get("covered_lines") or [])
        for entry in payload
        if entry.get("covered_lines")
    }
    decays = decay_since(repo_path, ref, covered_by_file)
    if not decays:
        return
    for entry in payload:
        d = decays.get(entry["file_path"])
        if d is None:
            continue
        entry["decay"] = {
            "measured_lines": d.measured,
            "confirmed_lines": d.confirmed,
            "invalidated_lines": d.invalidated,
            "drift_pct": d.drift_pct,
            "stale": d.is_stale,
            "measured_at_commit": ref[:12],
        }


def _serialize_coverage_row(row: Any, *, covered_lines: bool = True) -> dict[str, Any]:
    """One coverage row. ``covered_lines=False`` omits the per-line array.

    The narrow form is not the wide form minus a key: a row read with
    ``include_covered_lines=False`` carries no ``covered_lines_json`` at all, so
    touching it would raise rather than merely waste the parse.
    """
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
    }
    # Inserted here rather than appended, so the wide form stays byte-identical
    # to what callers already receive.
    if covered_lines:
        try:
            out["covered_lines"] = (
                json.loads(row.covered_lines_json) if row.covered_lines_json else []
            )
        except Exception:
            out["covered_lines"] = []
    out["total_coverable_lines"] = row.total_coverable_lines
    out["ingested_at"] = row.ingested_at.isoformat() if row.ingested_at else None
    out["ingested_commit_sha"] = row.ingested_commit_sha
    return out
