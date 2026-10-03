"""Code-health wire models: findings, trends, churn, badge and the work queue.

These mirror the dicts ``routers/code_health/serializers.py`` builds. Where a
serializer emits ``None`` it means "no signal", never zero, so those fields are
nullable; where it coerces to a number they are not.
"""

from __future__ import annotations

from typing import Any, Literal

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
    #: ``"unverified"`` for a provisional finding type, else ``None``.
    verification: str | None = None
    #: Why the finding can wait, on a lower-priority finding; else ``None``.
    lower_priority: str | None = None


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
    #: Stored documentation drift findings at this snapshot; ``None`` before recorded.
    doc_drift_count: int | None = None


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
    #: A test file, labelled on the row; ``scope=production`` leaves these out.
    is_test: bool = False
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
    #: How many of those are still open. Equal to ``finding_count`` under
    #: the default status filter; lower once dismissed work is shown.
    open_finding_count: int = 0
    biomarkers: list[str] = []
    #: S | M | L | XL
    effort_bucket: str
    impact_per_effort: float


class HealthWorkQueueResponse(BaseModel):
    targets: list[HealthWorkItem] = []
    #: Files matching the filters, before the page slice.
    total: int = 0
    #: Findings across those files, so the page can size the work, not just
    #: the file count it is paging through.
    finding_total: int = 0
    #: Files left out because every finding on them is a history marker
    #: (``history=exclude``, the default). Not counted in ``total``.
    history_only_excluded: int = 0
    offset: int = 0
    limit: int = 0


class ImpactEffortPoint(BaseModel):
    """One file on the impact / effort plane."""

    file_path: str
    #: Lines the planned change spans (``effort_basis="plan"``) or the file's
    #: code lines (``"file"``, when no plan recovers anything).
    effort_lines: int
    effort_basis: str
    #: Health points the plan credits, or the open findings' deduction.
    recoverable_health: float
    #: The tier of the file's first Fix-first item, when it holds one.
    tier: str | None = None
    #: That item's place in the Fix-first list, from 1.
    fix_rank: int | None = None


class ImpactEffortResponse(BaseModel):
    """Every file the work queue's filters keep, up to ``cap``."""

    points: list[ImpactEffortPoint] = []
    #: Points in this response; less than ``total`` only past ``cap``.
    plotted: int = 0
    #: Files the filters keep, history-only files excluded.
    total: int = 0
    cap: int = 0
    #: Fixed quadrant midlines from core, never derived from the data.
    effort_midline_lines: int = 0
    gain_midline_points: float = 0.0
    #: Files left out because every finding on them is a history marker.
    history_only_excluded: int = 0


class RelatedWorkRequest(BaseModel):
    """The files to look up, repo-relative. Validated by the route."""

    file_paths: list[str]


class RelatedWorkItem(BaseModel):
    """One row another lens holds for the file, compact enough to list."""

    lens: Literal["findings", "fix_first", "refactoring", "performance", "dead_code"]
    id: str
    #: Biomarker, refactoring type, Fix-first kind or dead-code kind.
    kind: str | None = None
    title: str | None = None
    symbol: str | None = None
    severity: str | None = None
    #: Fix-first tier, refactoring effort, performance actionability, or the
    #: dead-code verdict (``safe_to_delete`` / ``review``).
    tier: str | None = None
    rank: int | None = None
    line: int | None = None
    #: Sent only when the stored row carries them.
    code_origin: str | None = None
    deprecated: bool | None = None


class RelatedWorkLens(BaseModel):
    items: list[RelatedWorkItem] = []
    #: Every row the lens holds for the file; ``items`` stops at the limit.
    total: int = 0


class RelatedWorkFile(BaseModel):
    file_path: str
    #: Keyed by lens; a lens with nothing for the file is absent.
    lenses: dict[str, RelatedWorkLens] = {}


class RelatedWorkResponse(BaseModel):
    """``RelatedWork.as_dict()``: files in request order."""

    files: list[RelatedWorkFile] = []
    per_lens_limit: int = 0
