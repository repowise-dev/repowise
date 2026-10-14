"""Per-component figures folded from per-file facts the index already holds.

Sync, no I/O. Every figure carries ``basis`` (``measured``, ``inferred`` or
``unavailable``) and a ``method`` saying what it rests on. The arithmetic is
the existing one: health is :func:`module_rollups` over the component's files,
the bus factor is the repository truck factor (:func:`build_people`) taken
over the component's files. No figure names a person.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.analysis.health.aggregation import module_rollups
from repowise.core.analysis.health.rows import field
from repowise.core.stats_highlights import build_people

_TOP_HOTSPOTS = 5


def rollup_component(
    files: Iterable[str],
    *,
    test_files: set[str],
    git_rows: Mapping[str, Mapping[str, Any]],
    health_rows: Mapping[str, Any],
    dead_rows: Iterable[tuple[str, int | None]],
) -> dict[str, Any]:
    """Figures for one component from rows keyed by file path."""
    paths = list(files)
    in_component = set(paths)
    production = [p for p in paths if p not in test_files]
    return {
        "file_count": len(paths),
        "health": _health(paths, health_rows),
        "hotspots": _hotspots(paths, git_rows),
        "bus_factor": _bus_factor(paths, git_rows),
        "test_reach": _test_reach(production, health_rows),
        "dead_code": _dead_code(production, health_rows, dead_rows, in_component),
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"basis": "unavailable", "reason": reason}


def _health(paths: list[str], health_rows: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        {
            "file_path": p,
            "score": field(health_rows[p], "score", None),
            "nloc": field(health_rows[p], "nloc", 0),
            "is_test": field(health_rows[p], "is_test", None),
            "code_origin": field(health_rows[p], "code_origin", None),
            "module": "component",
        }
        for p in paths
        if p in health_rows
    ]
    rolled = module_rollups(rows)
    if not rolled:
        return _unavailable("no file in this component has a health score")
    top = rolled[0]
    return {
        "average": top["average_health"],
        "scored_files": top["file_count"],
        "worst_path": top["worst_performer_path"],
        "worst_score": top["worst_performer_score"],
        "basis": "measured",
        "method": "NLOC-weighted average of file health scores (1-10)",
    }


def _hotspots(paths: list[str], git_rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = [git_rows[p] for p in paths if p in git_rows]
    if not rows:
        return _unavailable("no git history indexed for this component's files")
    hot = sorted(
        (r for r in rows if r.get("is_hotspot")),
        key=lambda r: (-(r.get("temporal_hotspot_score") or 0.0), r["file_path"]),
    )
    return {
        "count": len(hot),
        "top": [r["file_path"] for r in hot[:_TOP_HOTSPOTS]],
        "basis": "measured",
        "method": "files git history flags as hotspots (high recent churn)",
    }


def _bus_factor(paths: list[str], git_rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = [git_rows[p] for p in paths if p in git_rows]
    people = build_people(rows)
    if people["truck_factor"] is None:
        return _unavailable("no file in this component has a known primary owner")
    return {
        "value": people["truck_factor"],
        "single_owner_files": people["single_owner_files"],
        "basis": "measured",
        "method": "fewest primary owners who together own over half the files",
    }


def _test_reach(production: list[str], health_rows: Mapping[str, Any]) -> dict[str, Any]:
    rows = [health_rows[p] for p in production if p in health_rows]
    if not rows:
        return _unavailable("health analysis has not recorded test pairing for these files")
    reached = sum(1 for r in rows if field(r, "has_test_file", False))
    out: dict[str, Any] = {
        "reached_files": reached,
        "production_files": len(rows),
        "share": round(reached / len(rows), 2),
        "basis": "inferred",
        "method": "a test imports the file, is named for it, or reaches it in the call graph",
    }
    measured = [r for r in rows if field(r, "line_coverage_pct", None) is not None]
    if measured:
        weight = sum(max(int(field(r, "nloc", 0) or 0), 1) for r in measured)
        pct = (
            sum(
                float(field(r, "line_coverage_pct")) * max(int(field(r, "nloc", 0) or 0), 1)
                for r in measured
            )
            / weight
        )
        out["line_coverage"] = {
            "pct": round(pct, 1),
            "measured_files": len(measured),
            "basis": "measured",
            "method": "NLOC-weighted line coverage from ingested coverage reports",
        }
    return out


def _dead_code(
    production: list[str],
    health_rows: Mapping[str, Any],
    dead_rows: Iterable[tuple[str, int | None]],
    in_component: set[str],
) -> dict[str, Any]:
    findings = [(p, lines) for p, lines in dead_rows if p in in_component]
    nloc = sum(int(field(health_rows[p], "nloc", 0) or 0) for p in production if p in health_rows)
    out: dict[str, Any] = {
        "findings": len(findings),
        "files_with_findings": len({p for p, _ in findings}),
        "lines": sum(int(lines or 0) for _, lines in findings),
        "basis": "inferred",
        "method": "static reachability findings; language-gated kinds excluded",
    }
    if nloc:
        out["share_of_nloc"] = round(min(out["lines"] / nloc, 1.0), 3)
    return out
