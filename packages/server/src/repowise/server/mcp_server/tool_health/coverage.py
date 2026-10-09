"""The coverage block for get_health: row serialization and measurement decay."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.coverage import decay_since, measurement_ref
from repowise.core.persistence.crud import coverage_row_dict
from repowise.server.mcp_server.tool_health.paging import Pager

#: The newest ingests the trend carries; ``history_total`` counts the complete ones kept.
HISTORY_POINTS = 10


def _coverage_block(
    rows: list[Any],
    summary: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    scoped: bool,
    pager: Pager,
    repo_path: str,
) -> dict[str, Any]:
    """Per-file coverage rows, the stored repo-wide summary, and its trend.

    ``history`` is the REST route's shape (one point per complete ingest, oldest
    first), cut to the newest :data:`HISTORY_POINTS`.

    Drop the bulky covered-lines arrays from dashboard mode; full
    detail is available in targeted mode.
    """
    if scoped:
        selected_coverage = pager.bound(rows, "coverage.files")
        coverage_payload = [
            coverage_row_dict(r, include_covered_lines=True) for r in selected_coverage
        ]
        _attach_coverage_decay(coverage_payload, selected_coverage, repo_path)
    else:
        # Built narrow: these rows were read without the column (see ``loading``).
        full_coverage_payload = [coverage_row_dict(r, include_covered_lines=False) for r in rows]
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
    if history:
        block["history"] = history[-HISTORY_POINTS:]
        block["history_total"] = len(history)
        block["history_emitted"] = len(block["history"])
        if len(history) > HISTORY_POINTS:
            block["history_reduced_reason"] = "limit"
    return block


def _attach_coverage_decay(payload: list[dict[str, Any]], rows: list[Any], repo_path: str) -> None:
    """Add a ``decay`` block to each coverage row, in place.

    The stored percentage is a measurement taken at one commit and never
    recomputed, so on a file under active development it can describe code that
    no longer exists. ``decay`` says how much of that measurement still holds:
    ``confirmed`` covered lines are unchanged since the report, ``invalidated``
    ones have moved and are now unknown rather than uncovered.

    Targeted mode only: dashboard mode skips ``covered_lines_json`` at the read,
    and computing drift there would undo that saving.

    Absent, not zero, when the measurement cannot be placed in history: a zero
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
            "drifted": d.is_drifted,
            "measured_at_commit": ref[:12],
        }
