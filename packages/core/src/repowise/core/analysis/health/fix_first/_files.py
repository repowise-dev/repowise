"""Shared pieces of the Fix-first units: per-file facts, ranks and item parts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.biomarkers.function_hotspot import FunctionHotspotDetector
from repowise.core.analysis.health.biomarkers.prior_defect import PriorDefectDetector
from repowise.core.analysis.health.gain import removal_gain
from repowise.core.analysis.health.queue.eligibility import DeadSpan, dead_target
from repowise.core.analysis.health.queue.order import LEVEL_RANK
from repowise.core.analysis.health.queue.value import (
    MIN_CHURN_COMMITS,
    function_reach,
    history_weight,
    removed,
    shape_value,
    size_value,
    tier,
    worth,
)
from repowise.core.analysis.health.rows import detail_map, field
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.worth import SIZE_MARKERS, measure, measured_nloc, worth_size
from repowise.core.analysis.next_call import ActionCommand

from . import text
from ._parts import (
    CLASS_MARKERS,
    MAX_CONTEXT,
    MAX_STEPS,
    Rows,
    _about,
    _helper_code,
    _num,
    _one_of,
    _quantile_cut,
    _tail,
    _Unit,
    symbol_key,
)
from .model import (
    FIX_EFFORTS,
    FixAction,
    FixContext,
    FixFact,
    FixGain,
    FixItem,
    FixRankFact,
    FixStep,
    fix_id,
)
from .plan_steps import _above_bar_note, _extract_text, _extraction_worth, _plan_body, _span


class _Files:
    """Per-file facts the units share: metrics, heat, history context."""

    def __init__(
        self,
        metrics: Rows,
        history: Mapping[str, list[Any]],
        functions: Mapping[tuple[str, str], list[Any]],
        cuts: tuple[float, float] | None,
        clones: Mapping[str, list[tuple[int, int]]] | None = None,
        extractions: Mapping[tuple[str, str], list[Any]] | None = None,
        dead: Mapping[str, list[DeadSpan]] | None = None,
        function_changes: Mapping[str, int] | None = None,
        blame_skipped: Iterable[str] = (),
        findings: Mapping[str, list[Any]] | None = None,
    ) -> None:
        self.by_path = {field(m, "file_path"): m for m in metrics}
        self.findings = findings or {}
        self.function_changes = function_changes
        self.blame_skipped = frozenset(blame_skipped)
        self.functions = functions
        self.clones = clones or {}
        self.extractions = extractions or {}
        self.dead = dead or {}
        if cuts is None:
            production = [m for m in self.by_path.values() if not field(m, "is_test")]
            cuts = (
                _quantile_cut([field(m, "commit_count_90d") or 0 for m in production]),
                _quantile_cut([field(m, "dependents") or 0 for m in production]),
            )
        self.churn_cut, self.deps_cut = cuts
        self.history = history
        # History that shows a function costs something: bug fixes in its
        # file, or the function changing more than most in the repository.
        self.fixed = {
            p for p, rows in history.items()
            if any(field(f, "biomarker_type") == PriorDefectDetector.name for f in rows)
        }
        self.hotspots = {
            p: {_tail(field(f, "function_name")) for f in rows
                if field(f, "biomarker_type") == FunctionHotspotDetector.name}
            for p, rows in history.items()
        }

    def gain(self, finding: Any) -> float:
        """What fixing *finding* recovers: its file rescored without it.

        Over the file's shown findings, so a hidden or triaged marker sharing
        its category is not counted; the error is that marker's slice.
        """
        path = field(finding, "file_path")
        return removal_gain(self.findings.get(path, [finding]), [(finding, 1.0)])

    def is_test(self, path: str) -> bool | None:
        """The stored flag; ``None`` when the file has no metric row or flag."""
        return field(self.by_path.get(path), "is_test")

    def origin(self, path: str) -> str | None:
        """The stored code origin; ``None`` before it was recorded."""
        return field(self.by_path.get(path), "code_origin")

    def commits(self, path: str) -> int:
        return int(field(self.by_path.get(path), "commit_count_90d") or 0)

    def coverage(self, path: str) -> float | None:
        """Measured line coverage in percent; ``None`` when no report has it."""
        return field(self.by_path.get(path), "line_coverage_pct")

    def dependents(self, path: str) -> int | None:
        return field(self.by_path.get(path), "dependents")

    def nloc(self, path: str) -> int | None:
        return field(self.by_path.get(path), "nloc")

    def central(self, path: str) -> bool:
        return (self.dependents(path) or 0) >= self.deps_cut

    def churning(self, path: str) -> bool:
        return self.commits(path) >= max(self.churn_cut, MIN_CHURN_COMMITS)

    def changes(self, path: str, symbol: str | None, *, function: bool) -> int | None:
        """Commits that changed the function ``symbol`` in ``path``, 0 with no
        stored row; ``None`` when the repository keeps no function history,
        the blame pass skipped the file for size, or the unit is led by no
        function (a class, a file, a duplicated span), so its file's churn
        stands in."""
        key = symbol_key(path, symbol) if function else None
        if self.function_changes is None or key is None or path in self.blame_skipped:
            return None
        return int(self.function_changes.get(key) or 0)

    def evidence(self, path: str, symbol: str | None) -> bool | None:
        """Whether history shows work on ``symbol`` pays: its file changes
        often or has bug fixes, or the function itself is a hotspot.
        ``None`` when the file's history is unknown: no commit count, or a
        repository whose history does not tell files apart."""
        tail = _tail(symbol)
        if self.churning(path) or path in self.fixed or (tail and tail in self.hotspots.get(path, ())):
            return True
        known = field(self.by_path.get(path), "commit_count_90d") is not None
        return False if known and self.churn_cut >= MIN_CHURN_COMMITS else None

    def reach(
        self, path: str, shape: Mapping[str, int], dependents: int | None, *, sized: bool
    ) -> tuple[int | None, bool]:
        """The importers a unit in ``path`` is credited with, and whether that
        makes it central: a function-size unit's are ``queue.value.function_reach``,
        any other unit's are its file's."""
        deps = self.dependents(path) if dependents is None else dependents
        if not sized:
            return deps, self.central(path)
        credited = function_reach(deps, measured_nloc(shape), self.nloc(path))
        if credited == deps:
            return deps, self.central(path)
        return credited, (credited or 0) >= self.deps_cut

    def shape(self, path: str, symbol: str | None) -> dict[str, int]:
        """The measured size of ``symbol`` in ``path``, from its findings."""
        if not symbol:
            return {}
        found = self.functions.get((path, symbol))
        if found is None:
            tail = symbol.rsplit(".", 1)[-1]
            found = next(
                (
                    rows
                    for (p, fn), rows in self.functions.items()
                    if p == path and fn.rsplit(".", 1)[-1] == tail
                ),
                (),
            )
        return measure(found)

    def cloned(self, path: str, shape: Mapping[str, int]) -> bool:
        """Whether a stored duplicate overlaps the function ``shape`` spans."""
        start, end = shape.get("start"), shape.get("end")
        if not start or not end:
            return False
        return any(a <= end and b >= start for a, b in self.clones.get(path, ()))

    def first_step(self, finding: Any) -> FixStep | None:
        """The concrete first edit for a finding with no plan of its own.

        A function-size finding starts at the best stored Extract Method span
        inside the function, else at its deepest nested block. Any other
        finding names its own line or function. ``None`` when there is no
        such edit to name: a class-level finding with no member groups, or a
        size finding with neither fact stored.
        """
        path = field(finding, "file_path")
        marker = field(finding, "biomarker_type") or ""
        function = field(finding, "function_name")
        line = field(finding, "line_start")
        if marker in CLASS_MARKERS:
            return None
        if marker not in SIZE_MARKERS:
            if line or function:
                summary = text.first_sentence(suggestion_for(marker))
                return FixStep(1, summary, path, line, False)
            return None
        tail = _tail(function) or ""
        best = self.extraction(finding)
        if best is not None:
            start, end = _span(best)
            body = _plan_body(best)
            step = _above_bar_note(_extract_text(tail, body, start, end), best)
            return FixStep(1, step, path, start, **_helper_code(body))
        shape = self.shape(path, function)
        start, end = shape.get("deep_start"), shape.get("deep_end")
        if start and end:
            depth = shape.get("max_nesting")
            where = f"where it nests {depth} deep" if depth else "where it nests deepest"
            lines = f"line {start}" if start == end else f"lines {start}-{end}"
            return FixStep(
                1,
                f"Start with {lines}, {where}: return early or move it into a helper",
                path,
                start,
            )
        return None

    def extraction(self, finding: Any) -> Any | None:
        """The best stored Extract Method plan inside a function-size
        finding's function, which :meth:`first_step` lifts first."""
        if field(finding, "biomarker_type") not in SIZE_MARKERS:
            return None
        tail = _tail(field(finding, "function_name")) or ""
        plans = self.extractions.get((field(finding, "file_path"), tail)) or ()
        return max(plans, key=_extraction_worth, default=None)

    def unreachable(self, path: str, symbol: str | None, line: int | None) -> bool:
        """Whether a sure dead-code finding covers ``symbol`` (at ``line``) in
        ``path``: the whole file, the line inside the finding's span, or, for a
        finding stored with no lines, the same name as written (``Old.run`` is
        not ``New.run``)."""
        return dead_target(self.dead, path, symbol, line)

    def why(
        self,
        path: str,
        *,
        measured: str | None,
        fallback: str,
        dependents: int | None = None,
        cloned: bool = False,
        changes: int | None = None,
        function: bool = False,
    ) -> str:
        """One sentence: the problem, then why it matters here (how often the
        function changed, who imports the file, how often it changes, how much
        of it tests run)."""
        deps = dependents if dependents is not None else self.dependents(path)
        head = measured or fallback.rstrip(". ")
        if cloned:
            head += f", {text.CLONED}"
        if changes is not None and changes >= MIN_CHURN_COMMITS:
            head += f"; {text.own_changes(changes)}"
        exposure = text.exposure(
            deps, self.commits(path), self.coverage(path), function=function
        )
        return f"{head}{exposure}."

    def context(self, path: str, symbol: str | None = None) -> tuple[FixContext, ...]:
        """History is context: shown beside the item, never lifting its band.
        An item on ``symbol`` shows its file's rows and its own, never another
        function's."""
        out: list[FixContext] = []
        commits = self.commits(path)
        if commits:
            people = field(self.by_path.get(path), "contributor_count")
            out.append(FixContext("recent changes", text.changes(commits, people)))
        if self.is_test(path):
            out.append(FixContext("file kind", "test"))
        ranked = sorted(self.history.get(path, ()), key=lambda f: -_num(field(f, "health_impact")))
        for f in ranked:
            if not _about(field(f, "function_name"), symbol):
                continue
            marker = field(f, "biomarker_type") or ""
            sentence = text.history_fact(marker, detail_map(f), field(f, "function_name"))
            if sentence:
                out.append(FixContext(text.HISTORY_LABEL.get(marker, "history"), sentence))
        return tuple(out[:MAX_CONTEXT])

    def common_facts(
        self, path: str, dependents: int | None = None, *, function: bool = False
    ) -> list[FixFact]:
        out = []
        deps = dependents if dependents is not None else self.dependents(path)
        if deps:
            label = "files that import its file" if function else "files that import it"
            out.append(FixFact(label, str(deps)))
        commits = self.commits(path)
        if commits:
            out.append(FixFact("changes in 90 days", str(commits)))
        return out


def _finish(
    *,
    kind: str,
    source_id: str,
    value: int,
    ready: bool,
    worth: float,
    confidence: str,
    effort: str,
    improves: str,
    rank_inputs: Callable[[], list[FixRankFact]],
    fields: Callable[[], dict[str, Any]],
    may_lead: bool = True,
    low: str | None = None,
    cold_value: int | None = None,
) -> _Unit:
    confidence = _one_of(confidence, LEVEL_RANK, "low")
    effort = _one_of(effort, FIX_EFFORTS, "M")
    unit_tier, why_tier = tier(value if cold_value is None else cold_value, confidence, ready, low)
    item_id = fix_id(kind, source_id)

    def write(rank: int) -> FixItem:
        return FixItem(
            id=item_id,
            rank=rank,
            tier=unit_tier,
            kind=kind,
            improves=improves,
            value=value,
            why_ranked=(
                # A later item ranks by value only among later items.
                FixRankFact("value" if low is None else "value within later", str(value)),
                *rank_inputs(),
                FixRankFact("worth in band", f"{worth:.0f}"),
                FixRankFact("tier", why_tier),
            ),
            **fields(),
        )

    return _Unit(
        item_id, kind, unit_tier, value, worth, confidence, effort, improves, write, may_lead
    )


@dataclass(frozen=True, slots=True)
class _ShapeRank:
    """What a unit led by a function's shape is valued and tiered on: one rule
    for a refactoring item, a finding item and the stored judgement."""

    shape: dict[str, int]
    central: bool
    churning: bool
    #: Commits that changed the lead function (``_Files.changes``).
    changes: int | None
    cloned: bool
    low: str | None
    gain: float
    ccn_removed: int
    dependents: int | None
    #: The importers the value counts (``_Files.reach``): ``dependents``, or a
    #: function's share of them in a file far larger than it.
    credited: int | None

    @property
    def value(self) -> int:
        return shape_value(self.gain, self.shape, self.cloned, central=self.central)

    @property
    def cold_value(self) -> int:
        return shape_value(self.gain, self.shape, self.cloned, central=False)

    @property
    def tier(self) -> str:
        return tier(self.cold_value, "medium", False, self.low)[0]

    @property
    def history(self) -> float:
        return history_weight(self.changes, self.churning)

    def facts(self) -> list[FixRankFact]:
        size = size_value(self.shape, self.central) if self.low is None else worth_size(self.shape)
        return [
            FixRankFact("health gain", f"{self.gain:.2f}"),
            FixRankFact("complexity removed", str(self.ccn_removed)),
            FixRankFact("problem size", str(size)),
            *_reach_facts(self.dependents, self.central, self.credited),
            _history_fact(self.changes, self.churning),
            FixRankFact("duplicate inside", "yes" if self.cloned else "no"),
        ]


def _finish_shaped(rank: _ShapeRank, **unit: Any) -> _Unit:
    """:func:`_finish` for a unit whose value, worth and rank facts come from ``rank``."""
    return _finish(
        value=rank.value,
        cold_value=rank.cold_value,
        worth=worth(removed(rank.ccn_removed, rank.gain), rank.credited, rank.history),
        low=rank.low,
        rank_inputs=rank.facts,
        **unit,
    )


def _reach_facts(deps: int | None, central: bool, credited: int | None) -> list[FixRankFact]:
    """What reach adds: importers lift the band when central. A function
    credited with only its share of its file's importers says so."""
    share = f", {credited} counted for its share of the file" if credited != deps else ""
    return [
        FixRankFact(
            "files that import it", f"{deps or 0}{' (top fifth)' if central else ''}{share}"
        ),
    ]


def _history_fact(changes: int | None, churning: bool) -> FixRankFact:
    """What history adds, which only orders inside the band: the function's
    own changes, or, with no function history, whether its file changes often."""
    if changes is None:
        return FixRankFact("changes often", "yes" if churning else "no")
    return FixRankFact("function changed in", text.plural(changes, "commit"))


def _size_facts(shape: Mapping[str, int]) -> list[FixFact]:
    size_text = text.size_line(shape)
    return [FixFact("size", size_text)] if size_text else []


def _action(summary: str, steps: tuple[FixStep, ...], mechanical: bool) -> FixAction:
    return FixAction(summary, steps[:MAX_STEPS], len(steps), mechanical)


def _health_gain(gain: float, *, ceiling: bool) -> FixGain:
    return FixGain("health_points", round(gain, 3), text.health_gain(gain, ceiling=ceiling))


def _opportunity_call(label: str, opportunity_id: str) -> ActionCommand:
    return ActionCommand.call(label, "get_health", {"opportunity_id": opportunity_id})
