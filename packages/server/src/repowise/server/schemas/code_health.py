"""Code-health wire models: findings, trends, churn, badge and the work queue.

These mirror the dicts ``routers/code_health/serializers.py`` builds. Where a
serializer emits ``None`` it means "no signal", never zero, so those fields are
nullable; where it coerces to a number they are not.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class HealthFindingResponse(BaseModel):
    """One biomarker finding, as the table and drawer read it."""

    id: str
    file_path: str
    biomarker_type: str
    severity: str
    function_name: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    health_impact: float
    reason: str | None = None
    #: Open by design: the payload differs per biomarker.
    details: dict[str, Any] = {}
    status: str
    #: defect | maintainability | performance. Rows predating the split read
    #: as ``defect``.
    dimension: str = "defect"


class HealthFindingWithSymbolResponse(HealthFindingResponse):
    """A listed finding, resolved to the symbol a tool can look up.

    ``None`` when the finding is file-level, or no symbol matched its span,
    so the UI degrades to the file page.
    """

    symbol_id: str | None = None


class ChurnComplexityPoint(BaseModel):
    """One file on the churn-vs-complexity scatter.

    Every figure is coerced by the producer, so none is nullable here: a zero
    means zero, not "no signal".
    """

    file_path: str
    commit_count_90d: int
    max_ccn: int
    nloc: int
    score: float
    churn_percentile: float


class ChurnComplexityResponse(BaseModel):
    points: list[ChurnComplexityPoint] = []
    #: Points before the request's limit, so a slice is not read as the whole.
    total: int = 0


class FileTrendPointResponse(BaseModel):
    taken_at: str | None = None
    score: float
    #: ``score`` with the floor undone; equal to it unless the file was on it.
    unclamped_score: float


class FileHealthTrendResponse(BaseModel):
    """One file's score over time. ``points`` is empty on thin history."""

    file_path: str
    points: list[FileTrendPointResponse] = []
    current: float | None = None
    previous: float | None = None
    delta: float | None = None
    unclamped_delta: float | None = None
    declining: bool = False
    snapshot_count: int = 0


class HealthTrendKpiRow(BaseModel):
    """One snapshot in the repo-level history, newest first."""

    taken_at: str | None = None
    #: ``None`` under a narrowed scope, which recorded only the average.
    hotspot_health: float | None = None
    average_health: float
    worst_performer_path: str | None = None
    worst_performer_score: float | None = None
    #: The headline's two halves in deduction points, and the maintainability
    #: pillar, at this snapshot. ``None`` before each was recorded and under a
    #: narrowed scope, so a series starts partway along the axis rather than
    #: reading an unrecorded point as a zero.
    structure_average: float | None = None
    history_average: float | None = None
    maintainability_average: float | None = None


class HealthTrendSummary(BaseModel):
    #: ``None`` under a narrowed scope: only the average was recorded for both
    #: populations, and a repo-wide hotspot figure under a production label
    #: would describe files the rest of the response has dropped.
    current_hotspot_health: float | None = None
    current_average_health: float
    previous_hotspot_health: float | None = None
    previous_average_health: float | None = None
    hotspot_delta: float | None = None
    average_delta: float | None = None
    #: The newest reading's two halves, in deduction points.
    current_structure_deduction: float | None = None
    current_history_deduction: float | None = None


class HealthTrendAlert(BaseModel):
    #: ``declining`` and ``predicted_decline`` are regressions; ``history_drag``
    #: is a fall the code shape did not cause and there is nothing to act on.
    kind: str
    metric: str
    current: float
    baseline: float | None = None
    delta: float
    message: str
    #: Which half of the headline moved, and each half's share of ``delta``.
    driver: str | None = None
    structure_delta: float | None = None
    history_delta: float | None = None


class HealthFileDelta(BaseModel):
    """One file's movement between the last two snapshots."""

    file_path: str
    before: float
    after: float
    delta: float


class HealthTrendResponse(BaseModel):
    history: list[HealthTrendKpiRow] = []
    summary: HealthTrendSummary
    alerts: list[HealthTrendAlert] = []
    #: Largest movement first, in either direction.
    file_deltas: list[HealthFileDelta] = []
    #: The count behind the slice, so the UI can say "N of M".
    file_deltas_total: int = 0
    snapshot_count: int = 0
    #: Which half of the repository these figures describe.
    scope: str = "all"


class HealthBadgeResponse(BaseModel):
    """Shields-compatible badge fields for the JSON endpoint.

    ``schemaVersion`` is camelCase because the Shields endpoint protocol
    requires that exact key; without it every embedded badge renders as
    "invalid response" instead of the score.
    """

    schemaVersion: int = 1  # noqa: N815
    label: str
    message: str
    color: str
    band: str


class HealthWorkItem(BaseModel):
    """One file in the triage queue, ranked by impact over effort."""

    file_path: str
    score: float
    nloc: int
    module: str | None = None
    primary_biomarker: str
    primary_severity: str
    primary_reason: str | None = None
    primary_function: str | None = None
    primary_line_start: int | None = None
    primary_line_end: int | None = None
    primary_suggestion: str | None = None
    primary_finding_id: str
    #: Pre-clamp deduction magnitude over the findings that passed the filters.
    total_impact: float
    finding_count: int
    biomarkers: list[str] = []
    #: S | M | L | XL
    effort_bucket: str
    impact_per_effort: float


class HealthWorkQueueResponse(BaseModel):
    targets: list[HealthWorkItem] = []
    total: int = 0
