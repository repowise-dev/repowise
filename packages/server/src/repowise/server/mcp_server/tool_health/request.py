"""What one get_health call asked for, normalized once before any read."""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.refactoring.serving import (
    CANONICAL_VIEWS as _REFACTORING_VIEWS,
)
from repowise.core.analysis.health.refactoring.serving import (
    DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT,
)
from repowise.core.analysis.health.scoring import ALL_DIMENSIONS

# ``include`` names that land under a different response key, so ``only`` can
# use the same name. ``signals`` has no top-level key (it merges into
# ``metrics[].signals``), so it is deliberately absent.
_ONLY_ALIASES = {
    "biomarkers": "findings",
    "accuracy": "defect_accuracy",
    "refactoring": "refactoring_plans",
}

DEFAULT_LIMIT = 20
"""Rows per ranked list when the caller passes no ``limit``."""

PLANS_PAGE_CAP = 25
"""Refactoring plans one response emits; ``cursor`` pages on."""

FIX_FIRST_CAP = 5
"""Items in the bare dashboard's ``fix_first`` block, however large ``limit`` is.
The rest are one ``only=["fix_first"]`` page or ``get_health(fix_id=...)`` away."""

FIX_FIRST_PAGE_CAP = 25
"""Items one ``only=["fix_first"]`` page emits when ``limit`` is passed: up to
this, ``cursor`` for the next page. With no ``limit`` a page is
:data:`FIX_FIRST_CAP`, so the call costs what the dashboard block does. Measured at about 16k chars for 25 items on a large
repository, inside the default 24k budget; the budget trims the tail beyond."""

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
        # Opts into provisional finding types, each labelled "unverified".
        "unverified",
        # The shared legend for deficit points and percentiles, in ``_meta``.
        "semantics",
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
    #: ``None`` when the caller passed none; resolved to :data:`DEFAULT_LIMIT`.
    limit: int | None
    cursor: int
    refactoring_view: str
    refactoring_type: str | None
    refactoring_confidence: str | None
    refactoring_effort: str | None
    refactoring_scope: str | None
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
    withheld_types: frozenset[str] = field(init=False)
    #: The caller passed ``limit``: a response grows past a default only when asked.
    limit_explicit: bool = field(init=False)

    def __post_init__(self) -> None:
        self.limit_explicit = self.limit is not None
        # ``0`` means totals and no rows, as on the REST coverage route.
        self.limit = max(DEFAULT_LIMIT if self.limit is None else self.limit, 0)
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
        # Finding types the registry keeps off this surface, dropped at the read
        # so no list, lead or total counts one.
        self.withheld_types = excluded_types(include_provisional="unverified" in self.include_set)
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

    @property
    def plans_cap(self) -> int:
        """How many refactoring plans one response emits: ``limit``, up to
        :data:`PLANS_PAGE_CAP`. Plans are compact rows, so a full page fits the budget."""
        return min(self.limit, PLANS_PAGE_CAP)

    @property
    def pages_fix_first(self) -> bool:
        """``fix_first`` named in ``only``: the queue pages by ``limit`` and
        ``cursor`` instead of the bare dashboard's fixed head."""
        return "fix_first" in self.only_set

    @property
    def fix_first_cap(self) -> int:
        """Fix-first items this response emits: a named page takes ``limit`` up
        to :data:`FIX_FIRST_PAGE_CAP` when one was passed, else the dashboard's head."""
        if self.pages_fix_first and self.limit_explicit:
            return min(self.limit, FIX_FIRST_PAGE_CAP)
        return min(self.limit, FIX_FIRST_CAP)

    @property
    def fix_first_cursor(self) -> int:
        """Where the Fix-first page starts: the bare dashboard always leads."""
        return self.cursor if self.pages_fix_first else 0

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
        return self.wants("performance_opportunities") or self.wants("performance_summary")

    @property
    def wants_refactoring_opportunities(self) -> bool:
        return self.wants("refactoring_opportunities") or self.wants("refactoring_summary")

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
            or self.wants("test_worst_files")
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
        return "refactoring" in self.include_set and "refactoring_plans" in self.only_set
