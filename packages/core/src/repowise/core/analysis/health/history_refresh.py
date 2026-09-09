"""Re-score a file's git-derived markers without re-reading its source.

An update only walks the files whose bytes changed, so every other file kept
whatever its history markers said at the last full index — a file could sit at
"bug-fixed 9 times recently" long after the fixes stopped, and its score with
it. These markers read git metadata, which the update refreshes for the whole
repository anyway, so they can be re-evaluated for untouched files at the cost
of a dictionary lookup.

Two history markers are missing here on purpose: ``function_hotspot`` and
``code_age_volatility`` need per-function complexity and a blame index, which
means a parse. They keep their stored findings until the file itself changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .biomarkers.base import BiomarkerResult, FileContext
from .biomarkers.registry import registered_biomarkers
from .governance import GOVERNANCE_BIOMARKERS
from .models import HealthFindingData, Severity
from .rows import detail_map, field
from .scoring import attach_impacts, deduction_split, remap_severities, score_file

#: History markers whose whole input is the file's git metadata.
REFRESHABLE_MARKERS: frozenset[str] = frozenset(
    {
        "change_entropy",
        "churn_risk",
        "co_change_scatter",
        "developer_congestion",
        "hidden_coupling",
        "knowledge_loss",
        "ownership_risk",
        "prior_defect",
    }
)


@dataclass
class RefreshedFile:
    """One file's re-scored findings and the numbers that follow from them."""

    file_path: str
    findings: list[HealthFindingData]
    score: float
    defect_score: float
    maintainability_score: float | None
    performance_score: float | None
    structure_deduction: float
    history_deduction: float


def as_biomarker_result(finding: Any) -> BiomarkerResult:
    """Lift a stored finding back into the shape the scorer takes.

    Everything the scorer reads was stored: the biomarker type, the severity
    that indexes the deduction table, and — for ``coverage_gradient``, the one
    marker whose magnitude is continuous rather than banded — the magnitude
    itself, which the detector writes into its own details rounded to 4dp. So
    a replayed score matches the original well inside the 2dp it is shown at.
    """
    details = detail_map(finding)
    raw = details.get("deduction")
    return BiomarkerResult(
        biomarker_type=field(finding, "biomarker_type", ""),
        severity=Severity(field(finding, "severity", Severity.LOW)),
        function_name=field(finding, "function_name"),
        line_start=field(finding, "line_start"),
        line_end=field(finding, "line_end"),
        details=details,
        reason=field(finding, "reason", "") or "",
        deduction=float(raw) if isinstance(raw, int | float) else None,
    )


def refresh_history(
    *,
    metrics: list[Any],
    findings_by_path: dict[str, list[Any]],
    git_meta_by_path: dict[str, dict],
    languages: dict[str, str],
    repo_active_contributors_90d: int | None = None,
    severity_overrides: dict[str, Severity] | None = None,
) -> list[RefreshedFile]:
    """Re-run the git-only markers over *metrics* and rescore each file.

    Findings outside :data:`REFRESHABLE_MARKERS` are carried through untouched,
    so a file keeps every structural finding the last parse produced. The whole
    set then goes back through the shared scorer rather than having the
    organizational total patched in place: that category is capped across all
    of its markers at once, refreshed and kept alike.
    """
    detectors = [b for b in registered_biomarkers() if b.name in REFRESHABLE_MARKERS]
    out: list[RefreshedFile] = []

    for metric in metrics:
        path = field(metric, "file_path", "")
        git_meta = git_meta_by_path.get(path)
        if not git_meta:
            continue

        ctx = FileContext(
            file_path=path,
            language=languages.get(path, ""),
            nloc=int(field(metric, "nloc", 0) or 0),
            has_test_file=bool(field(metric, "has_test_file", False)),
            module=field(metric, "module"),
            git_meta=git_meta,
            repo_active_contributors_90d=repo_active_contributors_90d,
        )

        fresh: list[BiomarkerResult] = []
        for detector in detectors:
            try:
                fresh.extend(detector.detect(ctx))
            except Exception:
                continue
        fresh = remap_severities(fresh, severity_overrides)

        kept: list[BiomarkerResult] = []
        governance: list[HealthFindingData] = []
        for stored in findings_by_path.get(path, []):
            marker = field(stored, "biomarker_type", "")
            if marker in REFRESHABLE_MARKERS:
                continue
            if marker in GOVERNANCE_BIOMARKERS:
                # Produced by a later pass that deducts nothing. Scoring them
                # here would invent a deduction and charge it to the capped
                # organizational category; dropping them would delete findings
                # this pass has no business deciding about.
                governance.append(_carry(stored, path))
                continue
            kept.append(as_biomarker_result(stored))

        results = kept + fresh
        scores, deductions = score_file(results)
        rescored = attach_impacts(results, deductions)
        for finding in rescored:
            finding.file_path = path
        structure, history = deduction_split(rescored)
        rescored.extend(governance)

        out.append(
            RefreshedFile(
                file_path=path,
                findings=rescored,
                score=round(scores["defect"], 2),
                defect_score=round(scores["defect"], 2),
                maintainability_score=_round_opt(scores["maintainability"]),
                performance_score=_round_opt(scores["performance"]),
                structure_deduction=structure,
                history_deduction=history,
            )
        )
    return out


def _carry(stored: Any, path: str) -> HealthFindingData:
    """A stored finding re-emitted unchanged, so a rewrite does not lose it."""
    return HealthFindingData(
        biomarker_type=field(stored, "biomarker_type", ""),
        severity=Severity(field(stored, "severity", Severity.LOW)),
        file_path=path,
        function_name=field(stored, "function_name"),
        line_start=field(stored, "line_start"),
        line_end=field(stored, "line_end"),
        details=detail_map(stored),
        health_impact=float(field(stored, "health_impact", 0.0) or 0.0),
        reason=field(stored, "reason", "") or "",
        dimension=field(stored, "dimension", "defect") or "defect",
    )


def _round_opt(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def active_contributors(git_meta_by_path: dict[str, dict]) -> int | None:
    """Repo-wide active-contributor count over stored git metadata."""
    from ...ingestion.git_indexer.enrich import count_active_contributors

    try:
        return count_active_contributors(list(git_meta_by_path.values()))
    except Exception:
        return None


def git_meta_rows_to_map(rows: Any) -> dict[str, dict]:
    """``{path: metadata dict}`` from stored ``GitMetadata`` rows.

    The detectors read git metadata as a mapping, which is how the indexer
    hands it to the engine, so ORM rows are flattened to the same shape.
    """
    out: dict[str, dict] = {}
    for row in rows:
        meta = {c.name: getattr(row, c.name, None) for c in row.__table__.columns}
        path = meta.get("file_path")
        if path:
            out[str(path)] = meta
    return out
