"""``include=["what_if"]``: what fixing a finding would recover, per file and for the repo.

A file's score is ``clamp(10 - structure - history)``, where history is capped by
``history_cap(structure)``: removing structure also lowers what history may
deduct. The stored history is already capped, so ``min(history, history_cap(s))``
is exactly the history left at a lower structure ``s``. With no structure left
that gives the file's ceiling, the most an edit can reach. A finding's ``points``
are its ``health_impact``, an upper bound when its category sits at its cap
(the others scale back up). Headline and hotspot gains weight the file gain by
the file's nloc share of each average.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.models import split_by_origin
from repowise.core.analysis.health.scoring import (
    ADVISORY_DIMENSION,
    SCORE_FLOOR,
    SCORE_MAX,
    history_cap,
)
from repowise.server.mcp_server.tool_health.loading import HealthData
from repowise.server.mcp_server.tool_health.request import HealthRequest

#: Files and repo-level fixes the block lists, whatever ``limit`` is.
WHAT_IF_CAP = 10

#: Findings listed per file.
FINDINGS_PER_FILE = 3

#: Dimensions whose findings never move the score.
_NO_SCORE = frozenset({"performance", ADVISORY_DIMENSION})


@dataclass(frozen=True)
class _Weights:
    """Each average's total weight, to turn a file gain into a headline gain."""

    average: float
    hotspot: float
    hotspots: frozenset[str]

    def gains(self, path: str, nloc: int, file_gain: float) -> dict[str, float]:
        weight = max(nloc, 1)
        out = {"file": round(file_gain, 2), "average": _share(file_gain, weight, self.average)}
        if path in self.hotspots:
            out["hotspot"] = _share(file_gain, weight, self.hotspot)
        return out


@dataclass(frozen=True)
class _FileScore:
    """One file's two halves, and its score at any lower structure."""

    structure: float
    history: float

    def at(self, structure: float) -> float:
        history = min(self.history, history_cap(structure))
        return _clamp(SCORE_MAX - structure - history)

    @property
    def now(self) -> float:
        return self.at(self.structure)


def what_if_block(data: HealthData, req: HealthRequest) -> dict[str, Any]:
    """The worst files' recoverable points and the repo's best single fixes."""
    weights = _weights(data)
    by_file = _structure_findings(data.findings.lead_rows)
    worst = data.metric_rows[: min(req.limit, WHAT_IF_CAP)]
    files = [_file_row(m, by_file.get(m.file_path, []), weights) for m in worst]
    fixes = sorted(
        (fix for row in files for fix in row.pop("_fixes")),
        key=lambda f: (-f["gain"]["average"], -f["gain"]["file"], f["file_path"]),
    )
    return {
        "target_score": TARGET_SCORE,
        "files": files,
        "top_fixes": fixes[: min(req.limit, WHAT_IF_CAP)],
    }


def _weights(data: HealthData) -> _Weights:
    """Over ``pop.all_metrics``, the rows ``kpis`` averages, tests included."""
    scored = [m for m in data.pop.all_metrics if m.score is not None]
    hotspots = frozenset(data.hotspot_paths)
    return _Weights(
        average=sum(max(m.nloc, 1) for m in scored),
        hotspot=sum(max(m.nloc, 1) for m in scored if m.file_path in hotspots),
        hotspots=hotspots,
    )


def _structure_findings(rows: list[Any]) -> dict[str, list[Any]]:
    """Code-shape findings that move the score, by file, largest first."""
    out: dict[str, list[Any]] = defaultdict(list)
    for f in split_by_origin(rows)[0]:
        if (f.health_impact or 0.0) > 0 and getattr(f, "dimension", None) not in _NO_SCORE:
            out[f.file_path].append(f)
    for findings in out.values():
        findings.sort(key=lambda f: -f.health_impact)
    return out


def _file_row(m: Any, findings: list[Any], weights: _Weights) -> dict[str, Any]:
    # A row written before the split was stored: its findings are the structure half.
    stored = getattr(m, "structure_deduction", None)
    halves = _FileScore(
        structure=stored if stored is not None else sum(f.health_impact for f in findings),
        history=getattr(m, "history_deduction", None) or 0.0,
    )
    ceiling = halves.at(0.0)
    fixes = [_fix(m, f, halves, weights) for f in findings]
    row: dict[str, Any] = {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "is_hotspot": m.file_path in weights.hotspots,
        "structure_deduction": round(halves.structure, 2),
        "history_deduction": round(halves.history, 2),
        "ceiling": round(ceiling, 2),
        "fix_all_structure": weights.gains(m.file_path, m.nloc, ceiling - halves.now),
        "findings": fixes[:FINDINGS_PER_FILE],
        "_fixes": fixes,
    }
    if ceiling < TARGET_SCORE:
        # History alone holds the file below target: no edit reaches it.
        row["target_out_of_reach"] = True
    return row


def _fix(m: Any, f: Any, halves: _FileScore, weights: _Weights) -> dict[str, Any]:
    points = min(f.health_impact, halves.structure)
    file_gain = halves.at(halves.structure - points) - halves.now
    fix: dict[str, Any] = {
        "file_path": m.file_path,
        "biomarker_type": f.biomarker_type,
        "points": round(points, 2),
        "gain": weights.gains(m.file_path, m.nloc, file_gain),
    }
    if f.function_name:
        fix["function_name"] = f.function_name
    if f.line_start is not None:
        fix["line_start"] = f.line_start
    return fix


def _clamp(score: float) -> float:
    return max(SCORE_FLOOR, min(SCORE_MAX, score))


def _share(file_gain: float, weight: int, total: float) -> float:
    return round(file_gain * weight / total, 3) if total else 0.0
