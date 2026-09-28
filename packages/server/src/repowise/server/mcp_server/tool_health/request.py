"""What one get_health call asked for, normalized once before any read."""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.analysis.health.scoring import ALL_DIMENSIONS
from repowise.server.services.refactoring_health import CANONICAL_VIEWS as _REFACTORING_VIEWS
from repowise.server.services.refactoring_health import DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT

# ``include`` names that land under a different response key, so ``only`` can
# use the same name. ``signals`` has no top-level key (it merges into
# ``metrics[].signals``), so it is deliberately absent.
_ONLY_ALIASES = {
    "biomarkers": "findings",
    "accuracy": "defect_accuracy",
    "refactoring": "refactoring_plans",
}

_RANKED_DIMENSIONS_DEFAULT = {"defect", "maintainability"}
"""Dimensions the impact-ranked findings list carries when none is asked for."""

_KNOWN_INCLUDES = frozenset(
    {
        "biomarkers",
        "refactoring",
        "trend",
        "coverage",
        "accuracy",
        "signals",
        "churn_complexity",
        "doc_drift",
        "performance",
        "defect",
        "maintainability",
        "advisory",
    }
)


@dataclass
class HealthRequest:
    """The call's arguments, clamped and resolved, plus what they imply.

    Built from the raw tool arguments; ``__post_init__`` derives the rest, so
    every block downstream reads one normalized value instead of re-deriving it.
    """

    targets: list[str] | None
    include: list[str] | None
    only: list[str] | None
    repo: str | None
    limit: int
    cursor: int
    refactoring_view: str
    refactoring_type: str | None
    refactoring_confidence: str | None
    refactoring_effort: str | None
    performance_view: str | None
    performance_context: str | None
    performance_boundary: str | None
    performance_confidence: str | None
    performance_actionability: str | None
    performance_sort: str | None
    scope: str
    counts: str
    include_set: set[str] = field(init=False)
    unknown_include_keys: list[str] = field(init=False)
    only_list: list[str] = field(init=False)
    only_set: set[str] = field(init=False)
    dimension_filter: set[str] = field(init=False)
    ranked_dimensions: set[str] = field(init=False)
    raw_targets: list[str] = field(init=False)
    module_targets: list[str] = field(init=False)
    file_targets: list[str] = field(init=False)

    def __post_init__(self) -> None:
        # ``0`` means totals and no rows, as on the REST coverage route.
        self.limit = max(self.limit, 0)
        self.cursor = max(self.cursor, 0)
        if self.refactoring_view not in _REFACTORING_VIEWS:
            self.refactoring_view = _REFACTORING_VIEW_DEFAULT
        self.include_set = set(self.include or [])
        self.unknown_include_keys = sorted(self.include_set - _KNOWN_INCLUDES)
        self.only_list = [_ONLY_ALIASES.get(k, k) for k in (self.only or [])]
        self.only_set = set(self.only_list)
        # Resolved before the reads, so the filter decides which rows are
        # eligible for the impact cap rather than filtering an already-capped list.
        self.dimension_filter = self.include_set & set(ALL_DIMENSIONS)
        # Performance findings carry zero impact, so an impact-ranked list
        # leaves them out unless asked for; the performance blocks rank them.
        self.ranked_dimensions = self.dimension_filter or _RANKED_DIMENSIONS_DEFAULT
        # ``module:foo`` targets expand later into the module's files.
        self.raw_targets = list(self.targets or [])
        self.module_targets = [
            t.split(":", 1)[1] for t in self.raw_targets if t.startswith("module:")
        ]
        # Stored paths are POSIX-separated; accept Windows separators.
        self.file_targets = [
            t.replace("\\", "/") for t in self.raw_targets if not t.startswith("module:")
        ]

    def wants(self, block: str) -> bool:
        """True when ``block`` survives the ``only`` projection.

        Consulted before expensive optional work, so the projection gates the
        work as well as the payload.
        """
        return not self.only_set or block in self.only_set

    @property
    def wants_findings(self) -> bool:
        return self.wants("findings") or self.wants("top_findings")

    @property
    def wants_test_findings(self) -> bool:
        return self.wants("test_findings")

    @property
    def wants_performance_opportunities(self) -> bool:
        return (
            self.wants("performance_opportunities")
            or self.wants("recommendation_lede")
            or self.wants("performance_summary")
        )

    @property
    def wants_refactoring_opportunities(self) -> bool:
        return (
            self.wants("refactoring_opportunities")
            or self.wants("recommendation_lede")
            or self.wants("refactoring_summary")
        )

    @property
    def needs_test_paths(self) -> bool:
        """Everything downstream of the test/production split, in one place.

        Keep this list exhaustive. The read it gates scans graph nodes, but a
        missing entry is worse: the split collapses for that projection, so a
        projection changes what a surviving key holds.
        """
        return (
            self.wants_findings
            or self.wants_test_findings
            or self.wants("worst_files")
            or self.wants("high_leverage_files")
            or self.wants("metrics")
            or ("refactoring" in self.include_set and self.wants("suggestion_legend"))
        )

    @property
    def plans_requested(self) -> bool:
        """Structured refactoring plans (Extract Class, ...), loaded only when asked for.

        ``include=["refactoring"]`` leads with composed opportunities, and
        emitting plans too would ship the same work twice.
        """
        return "refactoring" in self.include_set and (
            "refactoring_plans" in self.only_set
            # The cross-pillar lede quotes one plan.
            or self.wants_lede
        )

    @property
    def wants_lede(self) -> bool:
        return {"performance", "refactoring"} <= self.include_set and self.wants(
            "recommendation_lede"
        )
