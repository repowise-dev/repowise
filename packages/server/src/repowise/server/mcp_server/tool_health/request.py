"""What one get_health call asked for, normalized once before any read."""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.analysis.health.scoring import ALL_DIMENSIONS
from repowise.server.services.refactoring_health import CANONICAL_VIEWS as _REFACTORING_VIEWS
from repowise.server.services.refactoring_health import DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT

# ``include`` and ``only`` were different vocabularies: the block a caller
# switches on with ``include=["biomarkers"]`` lands under the key ``findings``,
# so the obvious ``only=["biomarkers"]`` projected it away again. Alias the three
# that have a 1:1 key rather than make the caller learn two names for one block.
# ``signals`` is deliberately absent — it has no top-level key to alias to, it
# merges into ``metrics[].signals``, so it stays reported in ``unknown_only_keys``.
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
        # ``0`` means none, matching the ``module_limit`` convention on the REST
        # coverage route. It used to clamp up to 1, so the documented way to ask for
        # "the totals, none of the rows" silently returned a row.
        self.limit = max(self.limit, 0)
        self.cursor = max(self.cursor, 0)
        if self.refactoring_view not in _REFACTORING_VIEWS:
            self.refactoring_view = _REFACTORING_VIEW_DEFAULT
        self.include_set = set(self.include or [])
        self.unknown_include_keys = sorted(self.include_set - _KNOWN_INCLUDES)
        self.only_list = [_ONLY_ALIASES.get(k, k) for k in (self.only or [])]
        self.only_set = set(self.only_list)
        # Resolved before the reads, not after them. Applied to the finished
        # response, a dimension filter narrowed a list that had already been capped
        # by impact — and performance findings carry low impact by construction, so
        # ``include=["biomarkers", "performance"]`` filtered a defect-heavy head down
        # to nothing while the total still reported the whole repo. The filter now
        # decides which rows are eligible for the cap in the first place.
        self.dimension_filter = self.include_set & set(ALL_DIMENSIONS)
        # The ranked findings list is ordered by health impact, and every
        # performance finding carries zero impact by construction, so leaving it
        # in an unfiltered list appends rows that can never rank and cannot be
        # compared against the ones above them. Asking for the dimension still
        # returns it, and the performance blocks rank the same evidence by cause.
        self.ranked_dimensions = self.dimension_filter or _RANKED_DIMENSIONS_DEFAULT
        # Split ``module:foo`` targets out of the path list. A target that
        # matches one or more modules is expanded into the set of files
        # belonging to those modules.
        self.raw_targets = list(self.targets or [])
        self.module_targets = [
            t.split(":", 1)[1] for t in self.raw_targets if t.startswith("module:")
        ]
        # Stored paths are POSIX-separated. Normalize so a Windows caller passing
        # ``packages\core\x.py`` matches instead of coming back ``no_such_path``.
        self.file_targets = [
            t.replace("\\", "/") for t in self.raw_targets if not t.startswith("module:")
        ]

    def wants(self, block: str) -> bool:
        """True when ``block`` survives the ``only`` projection.

        ``only`` used to be applied to the finished response, so the cheapest
        documented call — ``only=["directive"]`` — still paid for every block it
        then discarded. Consulted before the expensive optional work so the
        projection gates the work as well as the payload.
        """
        return not self.only_set or block in self.only_set

    @property
    def wants_findings(self) -> bool:
        # The serialized-rows read is the expensive optional one; skip it when no
        # block that carries findings survives the projection.
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

        Keep this list exhaustive. The read it gates is not free (the column list
        is narrow but the predicate is not indexed, so it scans this repo's graph
        nodes — ~55 ms warm on a 35k-node index), and ``only=["directive"]`` /
        ``["kpis"]`` / ``["modules"]`` serialize no metric row and no finding.
        But a *missing* entry here is worse than the read: it makes the split
        collapse for that projection, which is the same "a projection changed
        what a surviving key holds" defect this change exists to close. Adding
        ``suggestion_legend`` was not optional — the legend derives from the split
        heads, and leaving it out silently reverted that fix.
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

        Only when a caller names the plan list. ``include=["refactoring"]``
        leads with composed opportunities now, and emitting both would ship
        two representations of the same work in one response - 52k chars on
        this repo, past the expanded budget, most of it duplicated. The
        documented ``only=["refactoring_plans"]`` call is unchanged.
        """
        return "refactoring" in self.include_set and (
            "refactoring_plans" in self.only_set
            # The cross-pillar lede quotes one plan beside one performance
            # opportunity, and only when both pillars were asked for.
            or self.wants_lede
        )

    @property
    def wants_lede(self) -> bool:
        return {"performance", "refactoring"} <= self.include_set and self.wants(
            "recommendation_lede"
        )
