"""Governance-layer findings: ungoverned hotspot, stale governance, contradictory decision.

Pure, DB-free function — takes the output of ``get_decision_health_summary`` (plus the
full decision list) and returns a list of :class:`HealthFindingData` ready to be
persisted by ``replace_governance_findings``.

Design rationale
----------------
The health-analysis phase in the orchestrator runs *before* decisions are
persisted, so governance findings cannot be wired into the per-file biomarker
engine.  Instead this module runs as a lightweight additive pass *after*
decisions are upserted, writing rows to ``health_findings`` without touching
``HealthFileMetric.score``.  The score pass has already completed; re-running
it here would require a second metrics pass.  Governance quality is therefore
surfaced through the findings surface (``get_risk`` top_biomarkers,
``get_context`` health block, CLAUDE.md Critical biomarkers) rather than the
numeric score.
"""

from __future__ import annotations

from typing import Any

from .models import HealthFindingData, Severity
from .scoring import severity_deduction

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_MAX_FILES_PER_DECISION = 10
"""Maximum number of file findings emitted per stale/conflict decision.

Keeps the finding table bounded for large mono-repos where a single
architectural decision might reference hundreds of files.
"""

# Impact values anchored to the severity → deduction table in scoring.py.
_IMPACT_MEDIUM = round(severity_deduction(Severity.MEDIUM), 3)  # 0.7
_IMPACT_HIGH = round(severity_deduction(Severity.HIGH), 3)  # 1.2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_governance_findings(
    *,
    health_summary: dict[str, Any],
    decisions: list[Any],
) -> list[HealthFindingData]:
    """Derive governance ``HealthFindingData`` rows from a decision health summary.

    Parameters
    ----------
    health_summary:
        The ``dict`` returned by
        ``repowise.core.persistence.crud.get_decision_health_summary``.
        Expected keys: ``ungoverned_hotspots`` (list[str]),
        ``stale_decisions`` (list[DecisionRecord]),
        ``conflicts`` (list[dict]).
    decisions:
        The full list of ``DecisionRecord`` ORM rows for the repository
        (used to resolve ``affected_files_json`` for conflict findings
        without hitting the DB).

    Returns
    -------
    list[HealthFindingData]
        Deterministic, bounded, deduped list — safe to call multiple times
        with the same inputs and get the same output.
    """
    findings: list[HealthFindingData] = []

    # Build a fast id → decision map so conflict resolution is O(1).
    decision_by_id: dict[str, Any] = {d.id: d for d in decisions}

    findings.extend(_ungoverned_hotspot_findings(health_summary))
    findings.extend(_stale_governance_findings(health_summary))
    findings.extend(_contradictory_decision_findings(health_summary, decision_by_id))

    return findings


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _ungoverned_hotspot_findings(health_summary: dict[str, Any]) -> list[HealthFindingData]:
    """One MEDIUM finding per ungoverned churn hotspot."""
    out: list[HealthFindingData] = []
    for path in health_summary.get("ungoverned_hotspots", []):
        out.append(
            HealthFindingData(
                biomarker_type="ungoverned_hotspot",
                severity=Severity.MEDIUM,
                file_path=path,
                function_name=None,
                line_start=None,
                line_end=None,
                details={"is_hotspot": True},
                health_impact=_IMPACT_MEDIUM,
                reason="Churn hotspot with no governing architectural decision.",
            )
        )
    return out


def _stale_governance_findings(health_summary: dict[str, Any]) -> list[HealthFindingData]:
    """One HIGH finding per (file, highest-staleness active decision) pair.

    A file governed by multiple stale decisions gets one finding for the
    stale decision with the highest staleness score — keeps the finding
    table lean.
    """
    import json

    # file_path → best (staleness_score, decision) seen so far
    best: dict[str, tuple[float, Any]] = {}

    for decision in health_summary.get("stale_decisions", []):
        try:
            affected = json.loads(decision.affected_files_json or "[]")
        except (ValueError, TypeError):
            affected = []

        staleness = float(decision.staleness_score or 0.0)
        for fp in affected[:_MAX_FILES_PER_DECISION]:
            prev = best.get(fp)
            if prev is None or staleness > prev[0]:
                best[fp] = (staleness, decision)

    out: list[HealthFindingData] = []
    for fp, (staleness, decision) in sorted(best.items()):
        out.append(
            HealthFindingData(
                biomarker_type="stale_governance",
                severity=Severity.HIGH,
                file_path=fp,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "decision_id": decision.id,
                    "decision_title": decision.title,
                    "staleness_score": round(staleness, 3),
                },
                health_impact=_IMPACT_HIGH,
                reason=(
                    f"Governing decision '{decision.title}' is stale "
                    f"(staleness_score={staleness:.2f})."
                ),
            )
        )
    return out


def _contradictory_decision_findings(
    health_summary: dict[str, Any],
    decision_by_id: dict[str, Any],
) -> list[HealthFindingData]:
    """One HIGH finding per (file, conflict-pair) combination.

    Conflict edges are decision-level; we fan them out to the union of
    affected files of both decisions.  A (file, src_id, dst_id) triple is
    only emitted once (dedup set).
    """
    import json

    seen: set[tuple[str, str, str]] = set()
    out: list[HealthFindingData] = []

    for conflict in health_summary.get("conflicts", []):
        src_id = conflict["src"]["id"]
        dst_id = conflict["dst"]["id"]
        src_title = conflict["src"]["title"]
        dst_title = conflict["dst"]["title"]

        src_decision = decision_by_id.get(src_id)
        dst_decision = decision_by_id.get(dst_id)

        src_files: list[str] = []
        dst_files: list[str] = []
        if src_decision is not None:
            try:
                src_files = json.loads(src_decision.affected_files_json or "[]")
            except (ValueError, TypeError):
                src_files = []
        if dst_decision is not None:
            try:
                dst_files = json.loads(dst_decision.affected_files_json or "[]")
            except (ValueError, TypeError):
                dst_files = []

        # Emit for the union of both sets (cap each side)
        all_files = list(
            dict.fromkeys(src_files[:_MAX_FILES_PER_DECISION] + dst_files[:_MAX_FILES_PER_DECISION])
        )

        for fp in all_files:
            key = (fp, src_id, dst_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                HealthFindingData(
                    biomarker_type="contradictory_decision",
                    severity=Severity.HIGH,
                    file_path=fp,
                    function_name=None,
                    line_start=None,
                    line_end=None,
                    details={
                        "src_decision_id": src_id,
                        "dst_decision_id": dst_id,
                        "src_title": src_title,
                        "dst_title": dst_title,
                    },
                    health_impact=_IMPACT_HIGH,
                    reason=f"Contradicts decision '{dst_title}'.",
                )
            )

    return out
