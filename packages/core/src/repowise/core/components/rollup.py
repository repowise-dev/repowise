"""Per-component figures folded from per-file facts the index already holds.

Sync, no I/O. Every figure carries ``basis`` (``measured``, ``inferred`` or
``unavailable``) and a ``method`` saying what it rests on. The arithmetic is
the existing one: health is :func:`module_rollups` over the component's files,
the bus factor is the repository truck factor (:func:`build_people`) taken
over the component's files. Health and dead code leave out files in languages
:data:`LANGUAGE_GATES` holds back on that layer, the same registry the
``ungated`` SQL filter reads, and say how many. No figure names a person.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.analysis.finding_registry import gate_for
from repowise.core.analysis.health.aggregation import module_rollups
from repowise.core.analysis.health.rows import field
from repowise.core.analysis.owners import people_resolver
from repowise.core.stats_highlights import build_people

_TOP_HOTSPOTS = 5
#: Below this many owned files a truck factor is an anecdote, not a measure.
_MIN_OWNED_FILES = 5


def rollup_component(
    files: Iterable[str],
    *,
    test_files: set[str],
    git_rows: Mapping[str, Mapping[str, Any]],
    health_rows: Mapping[str, Any],
    dead_rows: Iterable[tuple[str, int | None]],
    dead_code_ran: bool,
) -> dict[str, Any]:
    """Figures for one component from rows keyed by file path."""
    paths = list(files)
    production = [p for p in paths if p not in test_files]
    return {
        "file_count": len(paths),
        "health": _health(paths, health_rows),
        "hotspots": _hotspots(paths, git_rows),
        "bus_factor": _bus_factor(paths, git_rows),
        "test_reach": _test_reach(production, health_rows),
        "dead_code": _dead_code(production, health_rows, dead_rows, dead_code_ran),
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"basis": "unavailable", "reason": reason}


def _gated_note(count: int) -> str:
    return f"; {count} file(s) in gated languages excluded" if count else ""


def _health(paths: list[str], health_rows: Mapping[str, Any]) -> dict[str, Any]:
    present = [p for p in paths if p in health_rows]
    gated = {p for p in present if gate_for("health", p)}
    rows = [
        {
            "file_path": p,
            "score": field(health_rows[p], "score", None),
            "nloc": field(health_rows[p], "nloc", 0),
            "is_test": field(health_rows[p], "is_test", None),
            "code_origin": field(health_rows[p], "code_origin", None),
            "module": "component",
        }
        for p in present
        if p not in gated
    ]
    rolled = module_rollups(rows)
    if not rolled:
        if gated:
            out = _unavailable("every scored file is in a language health is held back on")
            out["gated_files"] = len(gated)
            return out
        return _unavailable("no file in this component has a health score")
    top = rolled[0]
    return {
        "average": top["average_health"],
        "scored_files": top["file_count"],
        "gated_files": len(gated),
        "worst_path": top["worst_performer_path"],
        "worst_score": top["worst_performer_score"],
        "basis": "measured",
        "method": "NLOC-weighted average of file health scores (1-10)" + _gated_note(len(gated)),
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
    # Owned the way build_people counts it: a primary owner who is not a bot.
    resolve = people_resolver(rows)
    owned = 0
    for m in rows:
        name = m.get("primary_owner_name")
        if name and not resolve.is_bot(resolve(name, m.get("primary_owner_email"))):
            owned += 1
    out: dict[str, Any] = {
        "value": people["truck_factor"],
        "owned_files": owned,
        "file_count": len(paths),
        "coverage": round(owned / len(paths), 2) if paths else 0.0,
        "single_owner_files": people["single_owner_files"],
        "basis": "measured",
        "method": "fewest primary owners who together own over half the owned files",
    }
    if owned < _MIN_OWNED_FILES:
        out["basis"] = "inferred"
        out["note"] = f"only {owned} owned file(s); too few for a stable figure"
    return out


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
        "method": (
            "a test imports the file (directly or through a re-export barrel) or is "
            "named for it, the file holds inline tests, or a test reaches it in the "
            "call graph"
        ),
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
    ran: bool,
) -> dict[str, Any]:
    if not ran:
        return _unavailable("dead-code analysis has not run for this repository")
    gated = {p for p in production if gate_for("dead_code", p)}
    served = [p for p in production if p not in gated]
    if not served:
        if not gated:
            return _unavailable("the component has no production files")
        out = _unavailable("every production file is in a language dead code is held back on")
        out["gated_files"] = len(gated)
        return out
    in_served = set(served)
    findings = [(p, lines) for p, lines in dead_rows if p in in_served]
    nloc = sum(int(field(health_rows[p], "nloc", 0) or 0) for p in served if p in health_rows)
    out = {
        "findings": len(findings),
        "files_with_findings": len({p for p, _ in findings}),
        "lines": sum(int(lines or 0) for _, lines in findings),
        "gated_files": len(gated),
        "basis": "inferred",
        "method": "static reachability findings" + _gated_note(len(gated)),
    }
    if nloc:
        out["share_of_nloc"] = round(min(out["lines"] / nloc, 1.0), 3)
    return out
