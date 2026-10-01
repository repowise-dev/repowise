"""Build the Fix-first queue from plain rows, with no store behind them.

Every input is a sequence of rows, a row being a mapping or any object with the
named attributes (``health.rows.field``). The SQL loader in
``repowise.core.persistence.crud.analysis.fix_first`` narrows its reads and
hands the rows here; a caller holding the same rows in memory calls
:func:`build_fix_first` directly. The builder applies the whole rule, so a
caller may pass unfiltered rows.

Row shapes (field names are the SQL columns):

``metrics``
    ``file_path``, ``score``, ``nloc``, ``is_test``, ``code_origin``,
    ``line_coverage_pct``, ``analyzed_commit``, ``updated_at``, plus
    ``commit_count_90d`` and ``contributor_count`` (git) and ``dependents``
    (graph in-degree).
``findings``
    Hidden types are never items. ``file_path``, ``biomarker_type``,
    ``severity``, ``function_name``,
    ``line_start``, ``line_end``, ``reason``, ``health_impact``, ``public_id``,
    ``dimension``, ``status`` (absent = open), and ``details`` /
    ``details_json`` (``ccn``, ``nloc``, ``max_nesting``) for the numbers a
    sentence quotes, and ``deprecated`` for a function on its way out.
``refactoring``
    The ``refactoring_opportunities`` columns, ``details`` (or
    ``details_json``) carrying ``steps``, ``validation_profiles``,
    ``dependents`` and ``lead_finding_ids``. A row under the minimum worth may
    omit its details.
``performance``
    The ``performance_opportunities`` columns, ``details`` (or
    ``details_json``) carrying ``facets``, ``plan`` and ``fix_rationale``. A
    row with no plan, or an expected one, may omit its details.
``plans``
    Plan rows named by a refactoring step: ``public_id``, ``evidence`` /
    ``evidence_json`` and ``plan`` / ``plan_json`` (an extract-method ``span``,
    ``params``, ``returns``, ``suggested_name``; a move's destination; a
    split's named groups; a helper's ``occurrences``). A plan row that also
    carries ``refactoring_type`` ``extract_method``, ``file_path`` and
    ``target_symbol`` can give a finding with no plan of its own its first
    concrete step. Every Extract Helper plan's occurrences say where verified
    duplicates sit.
"""

from __future__ import annotations

import functools
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.perf.causal import code_context
from repowise.core.analysis.health.perf.opportunity_rank import (
    DEFAULT_QUEUE_CONTEXTS,
    default_queue_exclusion,
)
from repowise.core.analysis.health.refactoring.extract_helper import _is_generated_path
from repowise.core.analysis.health.rows import detail_map, field, json_field
from repowise.core.analysis.health.scoring import biomarker_dimension
from repowise.core.analysis.health.suggestions import suggestion_for

from . import text
from .model import (
    EFFORT_RANK,
    FIX_EFFORTS,
    FIX_EXCLUSIONS,
    FIX_IMPROVES,
    LEVEL_RANK,
    TIER_RANK,
    FixAction,
    FixConfidence,
    FixContext,
    FixEffortEstimate,
    FixFact,
    FixFirstQueue,
    FixGain,
    FixItem,
    FixNextCall,
    FixRankFact,
    FixRisk,
    FixSource,
    FixStep,
    FixTarget,
    FixTest,
    FixTotals,
    FixVerify,
    fix_id,
)

Rows = Iterable[Any]

#: A refactoring whose credited gain is under this is not worth an item. The
#: refactoring model credits only the share of a finding a plan removes and
#: drops trivial spans itself (minimum worth), so this floor is on the file.
MIN_WORTH = 0.5
#: Credited health gain cut points for value 1, 2 and 3.
GAIN_CUTS = (0.5, 1.5, 3.0)
#: Problem-size cut points for value 1 to 4. Health credit is calibrated per
#: finding and saturates, so a function far past every bar would rank with a
#: tidy one without these. Each cut is a multiple of the detector's own bar.
#: CCN 20 is twice the complex_method bar; 40, 80 and 150 double on from it.
SIZE_CCN = (20, 40, 80, 150)
#: 100 lines is past the large_method bar; 800 is a module living in one body.
SIZE_NLOC = (100, 200, 400, 800)
#: Nesting 5 is one past the nested_complexity bar; 8 is unreadable.
SIZE_NESTING = (5, 6, 8, 99)
#: A critical finding or a brain method is at least this size.
SIZE_SEVERE = 2
#: Measured size (CCN, lines or nesting alone, before the severity floor and
#: the hot-file bonus) from which an item leads as "break up", naming the whole
#: problem: CCN 40, 200 lines or nesting 6.
SIZE_BREAK_UP = 2
#: The highest value any unit reaches.
VALUE_MAX = 4
#: A file in the top fifth of production files by churn or dependents is hot.
HOT_QUANTILE = 0.8
MAX_FACTS = 5
MAX_TESTS = 5
MAX_STEPS = 5
MAX_CONTEXT = 3
#: In the first HEAD places no kind takes more than HEAD_PER_KIND, unless the
#: other kinds have nothing at value 2 or above.
HEAD = 5
HEAD_PER_KIND = 3
DEFAULT_LIMIT = 10
#: Findings that measure one function's size; a unit led by one of them, or by
#: an Extract Method step, is a function-level complexity unit.
SIZE_MARKERS = frozenset(
    {"complex_method", "nested_complexity", "brain_method", "large_method", "bumpy_road"}
)
#: A function whose largest dispatch on one value holds this share of its
#: decision points is usually fine as it is. Fitted on the dev labels only:
#: share >= 0.6 held 9 labelled complexity rows, 8 of them rejected.
DISPATCH_SHARE = 0.6
#: A function-level complexity unit needs this many code lines or this CCN.
#: Picked on the dev labels (67 complexity rows): 30 / 15 drops 13 rejected
#: small functions and 4 accepted ones; no cut that keeps every accepted row
#: drops more than 4 rejected.
SMALL_NLOC = 30
SMALL_CCN = 15
#: Class-level findings: a fix names member groups, which only a plan holds.
CLASS_MARKERS = frozenset({"low_cohesion", "god_class"})
#: Kinds the baseline raters found not worth doing: a refactoring led by one
#: of these steps, or a plan-less finding led by one of these markers, is no
#: candidate (it stays in the refactoring tab). Worth over rater labels, dev
#: repos first, then all 17 baseline repos.
LOW_VALUE_KINDS: dict[str, str] = {
    "extract_class": "dev 0/6, all 0/14",
    "move_method": "dev 0/14, all 0/34",
    "low_cohesion": "dev 0/26, all 0/46",
    "large_method": "dev 0/4, all 0/10",
    # Thinly measured: one held-out item (two labels), none on the dev repos.
    # A low-value maintainability nudge that otherwise fills a small repo's
    # whole top three; revisit when more of it is labelled.
    "primitive_obsession": "dev 0/0, all 0/2",
}


@dataclass(frozen=True, slots=True)
class _Unit:
    """What ranking reads, and how to write the item once it is shown.

    Items are written only for the ranks a caller keeps: the copy, facts and
    verify block cost more than the ranking, and most units are never shown.
    """

    id: str
    kind: str
    tier: str
    value: int
    score: float
    confidence: str
    effort: str
    improves: str
    write: Callable[[int], FixItem]
    #: False for a performance cause whose marker has not cleared the bar for
    #: leading (``opportunity_rank.may_lead``): listed, never first.
    may_lead: bool = True


_VENDORED = frozenset({"vendor", "third_party", "thirdparty", "node_modules"})
#: Stored code origins that do not ship, by the exclusion each counts as.
_ORIGIN_EXCLUSION = {
    "test": "test",
    "vendored": "vendored",
    "docs_example": "docs_example",
    "generated": "generated",
    "tooling": "tooling",
}


def _open(row: Any) -> bool:
    return (field(row, "status") or "open") == "open"


def _num(value: Any) -> float:
    return float(value or 0.0)


#: The shared path rules, memoised: they are pure on the path and the test
#: check dominates a cold build.
_code_context = functools.lru_cache(maxsize=65536)(code_context)


def _path_exclusion(
    path: str, is_test: bool | None, context: str | None = None, origin: str | None = None
) -> str | None:
    """Why a file is out of the queue, or ``None`` when it ships.

    The stored ``code_origin`` decides first: it read the file's head, so it
    knows a vendored library or a docs tutorial the path alone does not.
    Then the path rules. Code-shape work reads :func:`code_context`, where a
    CLI ships. A performance fix passes ``production``: its stored execution
    context is judged by the performance default queue, so only the path
    rules apply. The stored ``is_test`` flag also marks a test.
    """
    if origin in _ORIGIN_EXCLUSION:
        return _ORIGIN_EXCLUSION[origin]
    ctx = context or _code_context(path)
    if is_test or ctx == "test":
        return "test"
    parts = set(path.lower().split("/")[:-1])
    if parts & _VENDORED:
        return "vendored"
    if _is_generated_path(path):
        return "generated"
    # ``unknown`` under a directory is docs, examples or demos: code that does
    # not ship. A root-level file stays eligible.
    if ctx == "tooling" or (ctx == "unknown" and parts):
        return "tooling"
    return None


def hot_cut_offset(count: int) -> int:
    """Where the top fifth starts in ``count`` ascending values."""
    return min(int(count * HOT_QUANTILE), count - 1)


def hot_cut(value_at_offset: float | None) -> float:
    """The value at :func:`hot_cut_offset`, never below 1 (zero is not hot)."""
    return math.inf if value_at_offset is None else max(value_at_offset, 1)


def _quantile_cut(values: list[int]) -> float:
    """The smallest value in the top fifth of production files."""
    if not values:
        return math.inf
    return hot_cut(sorted(values)[hot_cut_offset(len(values))])


def _gain_value(gain: float, hot: bool) -> int:
    return min(3, sum(gain >= cut for cut in GAIN_CUTS) + int(hot))


def _magnitude(shape: Mapping[str, int]) -> int:
    """How far the measured CCN, size or nesting sits past its bar, 0 to 4."""
    return max(
        sum(shape.get("ccn", 0) >= c for c in SIZE_CCN),
        sum(shape.get("nloc", 0) >= c for c in SIZE_NLOC),
        sum(shape.get("max_nesting", 0) >= c for c in SIZE_NESTING),
    )


def _size_value(shape: Mapping[str, int], hot: bool) -> int:
    """How big the problem is, 0 to 4; a hot file counts one more."""
    base = max(_magnitude(shape), SIZE_SEVERE if shape.get("severe") else 0)
    return min(VALUE_MAX, base + int(hot and base > 0))


def _risk(files_touched: int, dependents: int | None) -> FixRisk:
    deps = dependents or 0
    if files_touched >= 5 or deps >= 20:
        level = "high"
    elif files_touched >= 2 or deps >= 5:
        level = "medium"
    else:
        level = "low"
    parts = [f"touches {text.plural(files_touched, 'file')}"]
    if dependents:
        parts.append(text.imports_it(dependents))
    return FixRisk(level, dependents, files_touched, "; ".join(parts).capitalize() + ".")


def _verify(profile: Mapping[str, Any] | None) -> FixVerify:
    """The stored validation profile, shown short. Never recomputed here."""
    if not profile:
        return FixVerify((), 0, None, "unknown")
    via = profile.get("via")
    how = text.TEST_VIA.get(via or "")
    reason = f"reaches the changed code {how}" if how else "covers it"
    tests = tuple(FixTest(str(t), reason) for t in (profile.get("tests") or [])[:MAX_TESTS])
    commands = profile.get("commands") or []
    basis = profile.get("basis")
    return FixVerify(
        tests,
        int(profile.get("total") or len(tests)),
        commands[0] if commands else None,
        basis if basis in ("measured", "inferred") else "unknown",
    )


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
    ) -> None:
        self.by_path = {field(m, "file_path"): m for m in metrics}
        self.functions = functions
        self.clones = clones or {}
        self.extractions = extractions or {}
        if cuts is None:
            production = [m for m in self.by_path.values() if not field(m, "is_test")]
            cuts = (
                _quantile_cut([field(m, "commit_count_90d") or 0 for m in production]),
                _quantile_cut([field(m, "dependents") or 0 for m in production]),
            )
        self.churn_cut, self.deps_cut = cuts
        self.history = history

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

    def hot(self, path: str) -> bool:
        return self.commits(path) >= self.churn_cut or (self.dependents(path) or 0) >= self.deps_cut

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
        return _measure(found)

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
        tail = (function or "").rsplit(".", 1)[-1]
        plans = self.extractions.get((path, tail)) or ()
        best = max(plans, key=_extraction_worth, default=None)
        if best is not None:
            start, end = _span(best)
            body = _plan_body(best)
            into = text.signature(
                body.get("suggested_name"),
                list(body.get("params") or []),
                list(body.get("returns") or []),
            )
            return FixStep(1, f"Extract lines {start}-{end} of {tail} into {into}", path, start)
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

    def unit_exclusion(self, path: str, symbol: str | None, complexity: bool) -> str | None:
        """Why a unit on ``symbol`` is not a candidate, or ``None``.

        A deprecated function is on its way out. A complexity unit on a
        function that is mostly one dispatch on one value is usually fine as
        it is, unless a duplicate also sits in it; one on a small function is
        not worth an item.
        """
        shape = self.shape(path, symbol)
        if shape.get("deprecated"):
            return "deprecated"
        if not complexity:
            return None
        if shape.get("dispatch_pct", 0) >= DISPATCH_SHARE * 100 and not self.cloned(path, shape):
            return "inherent_dispatch"
        if _small(shape):
            return "small_function"
        return None

    def why(
        self,
        path: str,
        *,
        measured: str | None,
        fallback: str,
        dependents: int | None = None,
        cloned: bool = False,
    ) -> str:
        """One sentence: the problem, then why it matters here (who imports
        the file, how often it changes, how much of it tests run)."""
        deps = dependents if dependents is not None else self.dependents(path)
        head = measured or fallback.rstrip(". ")
        if cloned:
            head += f", {text.CLONED}"
        return f"{head}{text.exposure(deps, self.commits(path), self.coverage(path))}."

    def context(self, path: str) -> tuple[FixContext, ...]:
        """History is context: shown beside the item, never ranked on."""
        out: list[FixContext] = []
        commits = self.commits(path)
        if commits:
            people = field(self.by_path.get(path), "contributor_count")
            out.append(FixContext("recent changes", text.changes(commits, people)))
        if self.is_test(path):
            out.append(FixContext("file kind", "test"))
        ranked = sorted(self.history.get(path, ()), key=lambda f: -_num(field(f, "health_impact")))
        for f in ranked:
            marker = field(f, "biomarker_type") or ""
            sentence = text.history_fact(marker, detail_map(f), field(f, "function_name"))
            if sentence:
                out.append(FixContext(text.HISTORY_LABEL.get(marker, "history"), sentence))
        return tuple(out[:MAX_CONTEXT])

    def common_facts(self, path: str, dependents: int | None = None) -> list[FixFact]:
        out = []
        deps = dependents if dependents is not None else self.dependents(path)
        if deps:
            out.append(FixFact("files that import it", str(deps)))
        commits = self.commits(path)
        if commits:
            out.append(FixFact("changes in 90 days", str(commits)))
        return out


def _tier(value: int, confidence: str, ready: bool) -> tuple[str, str]:
    if value >= 2 and LEVEL_RANK.get(confidence, 0) >= 1 and ready:
        return "now", "worth doing, and the plan is safe to start"
    if value >= 2:
        return "next", "worth doing; the fix needs judgment"
    return "later", "smaller payoff"


def _finish(
    *,
    kind: str,
    source_id: str,
    value: int,
    ready: bool,
    score: float,
    confidence: str,
    effort: str,
    improves: str,
    rank_inputs: Callable[[], list[FixRankFact]],
    fields: Callable[[], dict[str, Any]],
    may_lead: bool = True,
) -> _Unit:
    confidence = confidence if confidence in LEVEL_RANK else "low"
    effort = effort if effort in FIX_EFFORTS else "M"
    tier, why_tier = _tier(value, confidence, ready)
    item_id = fix_id(kind, source_id)

    def write(rank: int) -> FixItem:
        return FixItem(
            id=item_id,
            rank=rank,
            tier=tier,
            kind=kind,
            improves=improves,
            why_ranked=(
                FixRankFact("value", str(value)),
                *rank_inputs(),
                FixRankFact("tier", why_tier),
            ),
            **fields(),
        )

    return _Unit(item_id, kind, tier, value, score, confidence, effort, improves, write, may_lead)


# --- refactoring ----------------------------------------------------------------


def _plan_body(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    body = json_field(plan, "plan", None) or json_field(plan, "plan_json", {})
    return body if isinstance(body, dict) else {}


def _evidence(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    found = json_field(plan, "evidence", None) or json_field(plan, "evidence_json", {})
    return found if isinstance(found, dict) else {}


def _span(plan: Any) -> tuple[int | None, int | None]:
    span = _plan_body(plan).get("span")
    if isinstance(span, dict):
        return span.get("start"), span.get("end")
    return None, None


def _refactor_step(order: int, step: Mapping[str, Any], plan: Any) -> FixStep:
    kind = step.get("refactoring_type") or ""
    path = step.get("file_path") or ""
    sym = text.short_symbol(step.get("target_symbol")) or text.basename(path)
    start, end = _span(plan)
    if kind == "extract_method":
        body = _plan_body(plan)
        into = text.signature(
            body.get("suggested_name"),
            list(body.get("params") or []),
            list(body.get("returns") or []),
        )
        where = f"lines {start}-{end} of {sym}" if start and end else f"part of {sym}"
        line_text = f"Extract {where} into {into}"
    elif kind == "extract_helper":
        line_text = f"Replace the duplicate in {sym} with a call to one shared helper"
    elif kind == "extract_class":
        groups = [g for g in _plan_body(plan).get("groups") or [] if g.get("methods")]
        members = sorted(groups, key=lambda g: len(g["methods"]))[0]["methods"] if groups else []
        line_text = (
            f"Move {', '.join(members[:4])}"
            + (f" and {len(members) - 4} more" if len(members) > 4 else "")
            + f" out of {sym} into a new class"
            if members
            else f"Move a cohesive group of {sym}'s methods into a new class"
        )
    elif kind == "split_file":
        names = [g.get("name") for g in _plan_body(plan).get("groups") or [] if g.get("name")]
        line_text = (
            f"Split {text.basename(path)} into {', '.join(names[:4])}"
            + (f" and {len(names) - 4} more" if len(names) > 4 else "")
            if names
            else f"Split {text.basename(path)} along its independent groups"
        )
    elif kind == "break_cycle":
        edge = _cut_edge(plan)
        line_text = (
            f"Cut the import of {text.basename(edge['to'])} in "
            f"{text.basename(edge['from'])} (line {edge['line']})"
            if edge
            else f"Break the import cycle at {text.basename(path)}"
        )
    elif kind == "move_method":
        dest = _plan_body(plan).get("to_class") or text.basename(
            _plan_body(plan).get("to_file") or ""
        )
        line_text = (
            f"Move {sym} to {dest}" if dest else f"Move {sym} to the class it uses most"
        )
    else:
        line_text = f"Apply the {text.humanize(kind)} step to {sym}"
    mechanical = (step.get("applicability") or {}).get("classification") == "mechanical"
    return FixStep(order, line_text, path, start or step.get("line_start"), mechanical)


def _cut_edge(plan: Any) -> dict[str, Any] | None:
    """The first cut edge whose import line is stored."""
    for edge in _plan_body(plan).get("cut_edges") or ():
        if isinstance(edge, dict) and edge.get("line") and edge.get("from") and edge.get("to"):
            return edge
    return None


def _concrete(step: Mapping[str, Any], plan: Any) -> bool:
    """Whether a refactoring step names an edit someone can make: lines to
    lift, a destination, the import to cut, or named groups."""
    kind = step.get("refactoring_type") or ""
    body = _plan_body(plan)
    if kind == "extract_method":
        start, end = _span(plan)
        return bool(start and end)
    if kind == "extract_helper":
        return any(
            isinstance(o, dict) and o.get("line_start") for o in body.get("occurrences") or ()
        )
    if kind == "move_method":
        return bool(body.get("to_class") or body.get("to_file"))
    if kind == "break_cycle":
        return _cut_edge(plan) is not None
    if kind in ("split_file", "extract_class"):
        members = "symbols" if kind == "split_file" else "methods"
        return any(
            isinstance(g, dict) and g.get(members) and (g.get("name") or kind == "extract_class")
            for g in body.get("groups") or ()
        )
    return bool(step.get("line_start"))


def _extraction_worth(plan: Any) -> tuple[int, int]:
    evidence = _evidence(plan)
    return int(evidence.get("ccn_removed") or 0), int(evidence.get("slice_nloc") or 0)


def _extractions(plans: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    """Extract Method plans with a span, by (file, function name)."""
    out: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for plan in plans:
        symbol = field(plan, "target_symbol")
        if field(plan, "refactoring_type") != "extract_method" or not symbol:
            continue
        start, end = _span(plan)
        if start and end:
            out[(field(plan, "file_path"), text.short_symbol(symbol).rsplit(".", 1)[-1])].append(
                plan
            )
    return dict(out)


def _refactor_unit(
    row: Any,
    details: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    gain: float,
    plans: Mapping[str, Any],
    files: _Files,
) -> _Unit:
    path = field(row, "file_path")
    lead = steps[0]
    lead_type = lead.get("refactoring_type") or field(row, "lead_refactoring_type") or ""
    sym = text.short_symbol(lead.get("target_symbol")) or text.basename(path)
    marker = field(row, "lead_biomarker") or (
        lead_type if lead_type in ("split_file", "break_cycle") else None
    )
    mechanical_n = sum(
        (s.get("applicability") or {}).get("classification") == "mechanical" for s in steps
    )
    mechanical = mechanical_n == len(steps)
    confidence = field(row, "confidence") or "medium"
    hot = files.hot(path)
    dimension = biomarker_dimension(marker) if marker else "maintainability"
    shape = files.shape(path, lead.get("target_symbol"))
    size = _size_value(shape, hot)
    cloned = lead_type == "extract_method" and files.cloned(path, shape)

    def fields() -> dict[str, Any]:
        lead_plan = plans.get(lead.get("plan_id"))
        start, end = _span(lead_plan)
        body = _plan_body(lead_plan)
        if (
            _magnitude(shape) >= SIZE_BREAK_UP
            and lead_type == "extract_method"
            and start
            and end
        ):
            # The helper's signature is in the step; the title keeps its name,
            # or drops it when the problem and the span fill the line.
            title = (
                f"Start breaking up {sym} ({text.size_brief(shape)}): first lift lines "
                f"{start}-{end}"
            )
            into = f" into {body.get('suggested_name') or 'a helper'}"
            if len(title + into) <= text.TITLE_MAX:
                title += into
        else:
            key = (
                "extract_method_span"
                if lead_type == "extract_method" and start and end
                else lead_type
            )
            title = text.REFACTOR_TITLE.get(key, "Refactor {file}").format(
                sym=sym,
                file=text.basename(path),
                start=start,
                end=end,
                name=body.get("suggested_name") or "a helper",
            )
            if len(steps) > 1:
                title += f" (+{text.plural(len(steps) - 1, 'more step')})"
        dependents = details.get("dependents")
        if dependents is None:
            dependents = files.dependents(path)
        fix_steps = tuple(
            _refactor_step(i + 1, s, plans.get(s.get("plan_id"))) for i, s in enumerate(steps)
        )
        size_text = text.size_line(shape)
        facts = [
            FixFact("health recoverable", f"+{gain:.1f}", "inferred"),
            *([FixFact("size", size_text)] if size_text else []),
            FixFact("steps", f"{len(steps)} ({mechanical_n} mechanical)"),
            *files.common_facts(path, dependents),
        ]
        nloc = files.nloc(path)
        if nloc:
            facts.append(FixFact("file size", f"{nloc} lines"))
        profiles = {p.get("id"): p for p in details.get("validation_profiles") or []}
        profile = profiles.get(lead.get("validation_profile_id")) or next(
            iter(profiles.values()), None
        )
        plan_ids = tuple(s.get("plan_id") for s in steps if s.get("plan_id"))
        return {
            "title": text.clip(title),
            "target": FixTarget(
                path,
                sym if lead.get("target_symbol") else None,
                fix_steps[0].line,
                lead.get("line_end"),
            ),
            "why": files.why(
                path,
                measured=_refactor_measure(lead_type, sym, path, lead, lead_plan, files),
                fallback=text.problem(marker, sym),
                dependents=dependents,
                cloned=cloned,
            ),
            "facts": tuple(facts[:MAX_FACTS]),
            "action": FixAction(
                f"{text.plural(len(steps), 'step')}, {mechanical_n} mechanical",
                fix_steps[:MAX_STEPS],
                len(fix_steps),
                mechanical,
            ),
            "gain": FixGain(
                "health_points", round(gain, 3), text.health_gain(gain, ceiling=False)
            ),
            "effort": FixEffortEstimate(effort_bucket, "sized by the stored plan"),
            "risk": _risk(int(field(row, "affected_files_total") or 1), dependents),
            "confidence": FixConfidence(
                confidence if confidence in LEVEL_RANK else "medium",
                f"{mechanical_n} of {text.plural(len(steps), 'step')} proven mechanical "
                "by the plan",
            ),
            "verify": _verify(profile),
            "context": files.context(path),
            "source": FixSource(
                field(row, "opportunity_id"),
                plan_ids,
                tuple(details.get("lead_finding_ids") or ()),
            ),
            "next_call": FixNextCall(
                "get_health", {"opportunity_id": field(row, "opportunity_id")}
            ),
        }

    effort_bucket = field(row, "effort_bucket") or "M"
    effort_bucket = effort_bucket if effort_bucket in FIX_EFFORTS else "M"
    return _finish(
        kind="refactor",
        source_id=field(row, "opportunity_id"),
        value=_lifted(max(_gain_value(gain, hot), size), cloned),
        ready=mechanical or confidence == "high",
        score=_num(field(row, "rank_score")),
        confidence=confidence if confidence in LEVEL_RANK else "medium",
        effort=effort_bucket,
        improves=dimension if dimension in FIX_IMPROVES else "maintainability",
        rank_inputs=lambda: [
            FixRankFact("health gain", f"{gain:.2f}"),
            FixRankFact("problem size", str(size)),
            FixRankFact("hot file", "yes" if hot else "no"),
            FixRankFact("duplicate inside", "yes" if cloned else "no"),
        ],
        fields=fields,
    )


def _lifted(value: int, cloned: bool) -> int:
    """A complexity unit with a duplicate in the same function is one step
    more worth doing: raters accepted that shape almost every time."""
    return min(VALUE_MAX, value + 1) if cloned else value


def _refactor_measure(
    kind: str, sym: str, path: str, step: Mapping[str, Any], plan: Any, files: _Files
) -> str | None:
    """The numbers behind a refactoring, from its plan evidence or its findings."""
    evidence = _evidence(plan)
    name = text.basename(path)
    if kind == "split_file" and evidence.get("file_nloc"):
        return (
            f"{name}: {text.plural(int(evidence['file_nloc']), 'line')} in "
            f"{evidence.get('group_count') or 'several'} loosely coupled groups"
        )
    if kind == "break_cycle" and evidence.get("cycle_size"):
        return f"{name} is in an import cycle of {evidence['cycle_size']} files"
    if kind == "extract_class" and evidence.get("method_count"):
        return (
            f"{sym}: {evidence['method_count']} methods in "
            f"{evidence.get('lcom4') or 'several'} groups that share little state"
        )
    if kind == "extract_helper" and evidence.get("duplicated_lines"):
        return (
            f"{sym}: {evidence['duplicated_lines']} lines duplicated across "
            f"{evidence.get('occurrence_count') or 2} places"
        )
    return text.measured(sym, files.shape(path, step.get("target_symbol")))


# --- performance ----------------------------------------------------------------


def _has_plan(row: Any) -> bool:
    return field(row, "plan_state") == "available" and bool(field(row, "fix_strategy"))


#: Causes whose cost is real only in shipped code over a loop that grows: a
#: string built in a bounded loop, or in a script, costs nothing a user sees.
GROWS_ONLY_MARKERS = frozenset({"string_concat_in_loop"})


def _perf_worth(row: Any) -> bool:
    """Whether a planned cause is worth an item at all."""
    if field(row, "biomarker_type") not in GROWS_ONLY_MARKERS:
        return True
    facets = detail_map(row).get("facets") or {}
    return (
        field(row, "execution_context") == "production"
        and facets.get("loop_magnitude") == "grows_with_data"
    )


def _perf_value(row: Any, facets: Mapping[str, Any]) -> int:
    """0 to :data:`VALUE_MAX`. A production, entry-reachable database or
    network call in a loop that grows with the data is the costliest kind of
    work Fix first holds, so it shares the top band with the largest
    functions; capped below it, it never reached a top ten that size fills."""
    production = field(row, "execution_context") == "production"
    grows = facets.get("loop_magnitude") == "grows_with_data"
    unknown = facets.get("loop_magnitude") in (None, "unknown")
    if (
        production
        and facets.get("exposure") == "entry_reachable"
        and grows
        and field(row, "boundary_kind") in ("db", "network")
    ):
        return VALUE_MAX
    value = 2 if production and (grows or unknown) else 1
    # No traffic data: a loop of unknown size that no entry point reaches is
    # most often an admin or maintenance path, where an N+1 is cheap.
    if unknown and facets.get("exposure") != "entry_reachable":
        value -= 1
    return value


def _perf_step_text(step: Mapping[str, Any], path: str) -> str:
    """A plan step's action, naming its function once: an action that already
    says the name is not followed by it again."""
    action = step.get("action") or ""
    name = text.scope_name(step.get("symbol"), path)
    if not name or name.rsplit(".", 1)[-1] in action:
        return action
    return f"{action} ({name})"


def _site_symbol(symbol: str | None) -> str | None:
    """The function a call site sits in; ``None`` for none, or a file's top
    level, which is no symbol to jump to."""
    short = text.short_symbol(symbol)
    if not short or short.rsplit(".", 1)[-1] == "__module__":
        return None
    return short


def _perf_unit(
    rows: list[Any], files: _Files, symbol_lines: Mapping[str, int] | None = None
) -> _Unit:
    lead = min(rows, key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")))
    details = detail_map(lead)
    facets = details.get("facets") or {}
    plan = details.get("plan") or {}
    path = field(lead, "file_path") or ""
    symbol = field(lead, "intervention_symbol")
    exposure = facets.get("exposure")
    magnitude = facets.get("loop_magnitude")
    plan_steps = plan.get("steps") or []
    mechanical = bool(plan_steps) and all(
        s.get("applicability") == "mechanical" for s in plan_steps
    )
    ready = (
        field(lead, "actionability_state") == "plan_ready" or field(lead, "fix_safety") == "proven"
    )
    effort = plan.get("effort_bucket")

    def fields() -> dict[str, Any]:
        # A cause with no intervention symbol is named by the function its plan edits.
        first = plan_steps[0] if plan_steps else {}
        lines = symbol_lines or {}
        # The lead observation: the first call site the plan names. The item
        # points at the loop around it, else at the call itself.
        site = next((s for s in plan_steps if s.get("line")), None)
        name = (
            text.scope_name(symbol, path)
            or text.scope_name(first.get("symbol"), path)
            or text.basename(path)
        )
        module = name.startswith("module scope of ")
        noun = text.BOUNDARY_NOUN.get(field(lead, "boundary_kind") or "")
        call_sites = int(field(lead, "affected_call_sites_total") or 0)
        files_n = int(field(lead, "affected_files_total") or 1)
        amplification = facets.get("amplification")
        shaped = text.PERF_SHAPE.get(field(lead, "biomarker_type") or "")
        if shaped:
            title = shaped[0].format(name=name)
        elif noun and call_sites > 1:
            title = f"Batch the {noun} calls loops make {'in' if module else 'through'} {name}"
        elif noun:
            title = f"Move the {noun} call in {name} out of its loop"
        else:
            title = f"Fix the repeated work in {name}"
        what, gain_text = text.perf_cost(
            name,
            field(lead, "biomarker_type"),
            field(lead, "boundary_kind"),
            amplification,
            magnitude,
        )
        reach = []
        if call_sites > 1:
            reach.append(f"{call_sites} call sites in {text.plural(files_n, 'file')} reach it")
        if exposure == "entry_reachable":
            reach.append("an entry point reaches it")
        if magnitude == "grows_with_data":
            reach.append("the loop grows with the data")
        facts = [
            FixFact("call sites", str(call_sites)),
            FixFact("files", str(files_n)),
            FixFact(
                "reachable from an entry point",
                text.REACH_ANSWER.get(exposure or "", "unknown"),
                "inferred" if exposure in ("entry_reachable", "not_entry_reachable") else "unknown",
            ),
            FixFact(
                "loop size",
                text.loop_size(magnitude),
                "inferred" if magnitude in ("grows_with_data", "bounded") else "unknown",
            ),
        ]
        sinks = sorted({field(r, "terminal_sink") for r in rows if field(r, "terminal_sink")})
        if len(sinks) > 1:
            facts.append(FixFact("sinks this fix covers", str(len(sinks))))
        steps = tuple(
            FixStep(
                int(s.get("order") or i + 1),
                _perf_step_text(s, path),
                s.get("file_path") or path,
                s.get("line") or lines.get(s.get("symbol") or ""),
                s.get("applicability") == "mechanical",
            )
            for i, s in enumerate(plan_steps)
        )
        strategy = field(lead, "fix_strategy") or ""
        return {
            "title": text.clip(title),
            "target": (
                FixTarget(
                    site.get("file_path") or path,
                    _site_symbol(site.get("symbol")),
                    site.get("loop_line") or site.get("line"),
                )
                if site
                else FixTarget(
                    path,
                    name if (symbol or first.get("symbol")) and not module else None,
                    steps[0].line if steps else None,
                )
            ),
            "why": f"{what}; {', '.join(reach) or 'the loop size is unknown'}.",
            "facts": tuple(facts[:MAX_FACTS]),
            "action": FixAction(
                text.FIX_STRATEGY.get(strategy, text.humanize(strategy).capitalize()),
                steps[:MAX_STEPS],
                len(steps),
                mechanical,
            ),
            "gain": FixGain("performance", None, gain_text),
            "effort": FixEffortEstimate(
                effort if effort in FIX_EFFORTS else "M",
                "sized by the stored plan" if effort else "not sized; the plan has no estimate",
            ),
            "risk": _risk(files_n, None),
            "confidence": FixConfidence(
                confidence,
                details.get("fix_rationale")
                or f"{text.humanize(field(lead, 'actionability_state'))} plan",
            ),
            "verify": _verify(plan.get("validation")),
            "context": files.context(path),
            "source": FixSource(field(lead, "opportunity_id")),
            "next_call": FixNextCall(
                "get_health", {"opportunity_id": field(lead, "opportunity_id")}
            ),
        }

    confidence = facets.get("actionability_confidence") or "low"
    confidence = confidence if confidence in LEVEL_RANK else "low"
    return _finish(
        kind="perf_fix",
        may_lead=details.get("may_lead") is not False,
        source_id=f"{path}::{symbol or path}",
        value=_perf_value(lead, facets),
        ready=ready or mechanical,
        score=_num(field(lead, "rank_score")),
        confidence=confidence,
        effort=effort or "M",
        improves="performance",
        rank_inputs=lambda: [
            FixRankFact("runs in", field(lead, "execution_context") or "unknown"),
            FixRankFact("entry reachable", text.REACH_ANSWER.get(exposure or "", "unknown")),
            FixRankFact("loop size", text.loop_size(magnitude)),
            FixRankFact(
                "boundary", text.BOUNDARY_NOUN.get(field(lead, "boundary_kind") or "", "none")
            ),
        ],
        fields=fields,
    )


# --- findings with no plan ----------------------------------------------------------


def _finding_unit(lead: Any, files: _Files, first: FixStep) -> _Unit:
    path = field(lead, "file_path")
    marker = field(lead, "biomarker_type") or ""
    function = field(lead, "function_name")
    impact = _num(field(lead, "health_impact"))
    hot = files.hot(path)
    public_id = field(lead, "public_id")
    dimension = biomarker_dimension(marker)
    shape = files.shape(path, function)
    size = _size_value(shape, hot)
    cloned = marker in SIZE_MARKERS and files.cloned(path, shape)

    def fields() -> dict[str, Any]:
        where = function or text.basename(path)
        summary = text.first_sentence(suggestion_for(marker))
        line = field(lead, "line_start")
        if _magnitude(shape) >= SIZE_BREAK_UP and function and marker in SIZE_MARKERS:
            title = f"Break up {where} ({text.size_brief(shape)})"
        else:
            title = text.FINDING_TITLE.get(marker, "Address the finding in {where}").format(
                where=where
            )
        return {
            "title": text.clip(title),
            "target": FixTarget(path, function, line, field(lead, "line_end")),
            "why": files.why(
                path,
                measured=text.measured(where, shape),
                fallback=text.problem(marker, where),
                cloned=cloned,
            ),
            "facts": tuple(
                [
                    FixFact("finding", text.marker_label(marker)),
                    *([FixFact("size", size_text)] if (size_text := text.size_line(shape)) else []),
                    FixFact("severity", field(lead, "severity") or "unknown"),
                    *files.common_facts(path),
                ][:MAX_FACTS]
            ),
            "action": FixAction(summary, (first,), 1, False),
            "gain": FixGain(
                "health_points", round(impact, 3), text.health_gain(impact, ceiling=True)
            ),
            "effort": FixEffortEstimate("M", "not sized; no stored plan covers this finding"),
            "risk": _risk(1, files.dependents(path)),
            "confidence": FixConfidence(
                "medium", "Measured from the code; no stored plan has checked a fix."
            ),
            "verify": _verify(None),
            "context": files.context(path),
            "source": FixSource(None, (), (public_id,) if public_id else ()),
            "next_call": FixNextCall(
                "get_health", {"targets": [path], "include": ["biomarkers"]}
            ),
        }

    return _finish(
        kind="finding",
        source_id=public_id or f"{path}::{marker}::{function or ''}",
        value=_lifted(max(_gain_value(impact, hot), size), cloned),
        ready=False,
        score=impact,
        confidence="medium",
        effort="M",
        improves=dimension if dimension in FIX_IMPROVES else "defect",
        rank_inputs=lambda: [
            FixRankFact("health gain", f"{impact:.2f}"),
            FixRankFact("problem size", str(size)),
            FixRankFact("hot file", "yes" if hot else "no"),
            FixRankFact("duplicate inside", "yes" if cloned else "no"),
        ],
        fields=fields,
    )


def _small(shape: Mapping[str, int]) -> bool:
    """Under both size floors. A function whose size findings carry no line
    count is measured by its span, which bounds its code lines from above; one
    with neither is not judged small."""
    nloc = shape.get("nloc")
    if not nloc and shape.get("start") and shape.get("end"):
        nloc = shape["end"] - shape["start"] + 1
    if not nloc:
        return False
    return nloc < SMALL_NLOC and shape.get("ccn", 0) < SMALL_CCN


def _finding_exclusion(finding: Any, files: _Files) -> str | None:
    if field(finding, "biomarker_type") in LOW_VALUE_KINDS:
        return "low_value_kind"
    reason = files.unit_exclusion(
        field(finding, "file_path"),
        field(finding, "function_name"),
        complexity=field(finding, "biomarker_type") in SIZE_MARKERS,
    )
    if reason is None and files.first_step(finding) is None:
        return "no_concrete_step"
    return reason


def _clone_spans(plans: Iterable[Any]) -> dict[str, list[tuple[int, int]]]:
    """Where verified duplicates sit, by file: the occurrences an Extract
    Helper plan names. Those come from clone pairs the detector verified
    token by token and by shared identifier names, with test, generated and
    short spans already dropped. ``dry_violation`` is not read: it also
    pairs import blocks and data literals, which is why it is hidden.
    """
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for plan in plans:
        for occ in _plan_body(plan).get("occurrences") or ():
            if isinstance(occ, dict) and occ.get("line_start") and occ.get("line_end"):
                out[occ.get("file") or ""].append((occ["line_start"], occ["line_end"]))
    return dict(out)


# --- order ------------------------------------------------------------------------


def _order(units: list[_Unit]) -> list[_Unit]:
    ranked = sorted(
        units,
        key=lambda u: (
            -u.value,
            TIER_RANK[u.tier],
            -LEVEL_RANK.get(u.confidence, 0),
            EFFORT_RANK.get(u.effort, 1),
            -u.score,
            u.id,
        ),
    )
    head: list[_Unit] = []
    taken: Counter[str] = Counter()
    while ranked and len(head) < HEAD:
        pick = ranked[0]
        if taken[pick.kind] >= HEAD_PER_KIND:
            pick = next(
                (
                    u
                    for u in ranked
                    if u.value >= 2
                    and u.kind != pick.kind
                    and taken[u.kind] < HEAD_PER_KIND
                ),
                pick,
            )
        ranked.remove(pick)
        head.append(pick)
        taken[pick.kind] += 1
    out = head + ranked
    # A cause that may not lead stays in the list, behind the first that may.
    first = next((i for i, u in enumerate(out) if u.may_lead), 0)
    if first:
        out.insert(0, out.pop(first))
    return out


def _by_function(findings: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    out: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for f in findings:
        name = field(f, "function_name")
        if name:
            out[(field(f, "file_path"), name)].append(f)
    return dict(out)


def _measure(findings: Iterable[Any]) -> dict[str, int]:
    """A function's largest measured CCN, size and nesting across its findings.

    ``severe`` is set when any of them is critical or a brain method,
    ``deprecated`` when any sits in a function marked deprecated,
    ``dispatch_pct`` is the largest stored dispatch share in percent,
    ``start`` / ``end`` span the function as its size findings place it, and
    ``deep_start`` / ``deep_end`` are its deepest nested block's lines.
    """
    shape: dict[str, int] = {}
    for f in findings:
        if field(f, "severity") == "critical" or field(f, "biomarker_type") == "brain_method":
            shape["severe"] = 1
        details = detail_map(f)
        if details.get("deprecated"):
            shape["deprecated"] = 1
        deepest = details.get("deepest_block")
        if isinstance(deepest, dict) and deepest.get("start") and "deep_start" not in shape:
            shape["deep_start"] = int(deepest["start"])
            shape["deep_end"] = int(deepest.get("end") or deepest["start"])
        share = details.get("dispatch_share")
        if isinstance(share, (int, float)):
            shape["dispatch_pct"] = max(shape.get("dispatch_pct", 0), round(share * 100))
        if field(f, "biomarker_type") in SIZE_MARKERS:
            start, end = field(f, "line_start"), field(f, "line_end")
            if start and end:
                shape["start"] = min(shape.get("start", start), start)
                shape["end"] = max(shape.get("end", end), end)
        for k in ("ccn", "nloc", "max_nesting", "lcom4", "method_count"):
            v = details.get(k)
            if isinstance(v, (int, float)) and v > shape.get(k, 0):
                shape[k] = int(v)
    return shape


def _basis(metrics: Rows) -> dict[str, str | None]:
    stamped = [(str(at), at, m) for m in metrics if (at := field(m, "updated_at"))]
    if not stamped:
        return {"analyzed_commit": None, "health_analyzed_at": None}
    _key, at, latest = max(stamped, key=lambda s: s[0])
    return {
        "analyzed_commit": field(latest, "analyzed_commit"),
        "health_analyzed_at": at.isoformat() if hasattr(at, "isoformat") else str(at),
    }


def build_fix_first(
    *,
    metrics: Rows = (),
    findings: Rows = (),
    refactoring: Rows = (),
    performance: Rows = (),
    plans: Rows = (),
    limit: int | None = DEFAULT_LIMIT,
    scope: str = "production",
    basis: Mapping[str, str | None] | None = None,
    item_id: str | None = None,
    hot_cuts: tuple[float, float] | None = None,
    symbol_lines: Mapping[str, int] | None = None,
) -> FixFirstQueue:
    """One ranked queue of what to fix, from stored rows (shapes in the module docstring).

    ``scope="all"`` keeps test files (labelled in ``context``); ``limit=None``
    keeps every eligible item. ``basis`` is the analysis stamp when the caller
    read it apart; otherwise it comes from the metrics' ``updated_at``.
    ``item_id`` keeps just that item, at its rank, for a lookup by id.
    ``hot_cuts`` are the (churn, dependents) thresholds for a hot file when the
    caller measured them over more files than it passed in ``metrics``; see
    :func:`hot_cut` for the rule. ``symbol_lines`` maps a symbol id
    (``path::name``) to its first line, for a performance plan step that
    names a function but stored no line.
    """
    metrics = list(metrics)
    findings = list(findings)
    keep_tests = scope == "all"
    excluded = dict.fromkeys(FIX_EXCLUSIONS, 0)
    hidden = excluded_types()

    by_file: dict[str, list[Any]] = defaultdict(list)
    for f in findings:
        if (
            _num(field(f, "health_impact")) > 0
            and field(f, "biomarker_type") not in hidden
            and field(f, "dimension") != "performance"
            and _open(f)
        ):
            by_file[field(f, "file_path")].append(f)
    split = {path: split_by_origin(rows) for path, rows in by_file.items()}
    plans = list(plans)
    files = _Files(
        metrics,
        {p: hist for p, (_shape, hist) in split.items() if hist},
        _by_function(f for shape, _hist in split.values() for f in shape),
        hot_cuts,
        _clone_spans(plans),
        _extractions(plans),
    )

    def out_of_scope(path: str, context: str | None = None) -> bool:
        reason = _path_exclusion(path, files.is_test(path), context, files.origin(path))
        if reason is None or (reason == "test" and keep_tests):
            return False
        excluded[reason] += 1
        return True

    plan_rows = {field(p, "public_id"): p for p in plans}
    units: list[_Unit] = []
    planned_files: set[str] = set()
    for row in sorted(
        (r for r in refactoring if _open(r)),
        key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")),
    ):
        path = field(row, "file_path")
        if out_of_scope(path):
            continue
        if _num(field(row, "recoverable_health")) < MIN_WORTH:
            excluded["below_min_worth"] += 1
            continue
        details = detail_map(row)
        steps = list(details.get("steps") or [])
        gain = _num(field(row, "recoverable_health"))
        if not steps:
            excluded["below_min_worth"] += 1
            continue
        reason = (
            "low_value_kind"
            if steps[0].get("refactoring_type") in LOW_VALUE_KINDS
            else files.unit_exclusion(
                path,
                steps[0].get("target_symbol"),
                complexity=steps[0].get("refactoring_type") == "extract_method",
            )
        )
        if reason is None and not _concrete(steps[0], plan_rows.get(steps[0].get("plan_id"))):
            reason = "no_concrete_step"
        if reason is not None:
            excluded[reason] += 1
            continue
        units.append(_refactor_unit(row, details, steps, gain, plan_rows, files))
        # Only a plan that became an item speaks for the file's findings; an
        # excluded one leaves them to compete on their own.
        planned_files.add(path)

    # Ceiling: grouped by the intervention as stored, until persistence writes
    # one row per intervention.
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in performance:
        if _open(row):
            path = field(row, "file_path") or ""
            groups[(path, field(row, "intervention_symbol") or path)].append(row)
    # The performance default queue decides which causes are work (context and
    # actionability, one predicate for every surface); Fix first adds its path
    # rules and needs a stored plan to quote.
    perf_contexts = DEFAULT_QUEUE_CONTEXTS | {"test"} if keep_tests else DEFAULT_QUEUE_CONTEXTS
    for (path, _symbol), rows in groups.items():
        if out_of_scope(path, "production"):
            continue
        rows.sort(key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")))
        reasons = [default_queue_exclusion(r, perf_contexts) for r in rows]
        queued = [r for r, reason in zip(rows, reasons, strict=True) if reason is None]
        ready = [r for r in queued if _has_plan(r)]
        if not ready:
            excluded["no_plan" if queued else reasons[0] or "no_plan"] += 1
            continue
        worth = [r for r in ready if _perf_worth(r)]
        if not worth:
            excluded["below_min_worth"] += 1
            continue
        units.append(_perf_unit(worth, files, symbol_lines))

    for path, (shape, history) in split.items():
        if path in planned_files:
            continue
        lead = primary_finding(shape)
        if lead is None and not history:
            continue  # advisory only: nothing to fix, nothing to count
        if out_of_scope(path):
            continue
        if lead is None:
            excluded["history_only"] += 1
            continue
        # A finding that is no candidate leaves the file's others to compete;
        # the file is counted under its own lead's reason when none is left.
        reasons = {id(f): _finding_exclusion(f, files) for f in shape}
        eligible = primary_finding([f for f in shape if reasons[id(f)] is None])
        if eligible is None:
            excluded[reasons[id(lead)] or next(r for r in reasons.values() if r)] += 1
            continue
        units.append(_finding_unit(eligible, files, files.first_step(eligible)))

    ordered = _order(units)
    if item_id is not None:
        shown = [(i, u) for i, u in enumerate(ordered) if u.id == item_id]
    else:
        keep = ordered if limit is None else ordered[: max(limit, 0)]
        shown = list(enumerate(keep))
    by_improves = Counter(u.improves for u in units)
    return FixFirstQueue(
        items=tuple(u.write(rank) for rank, u in shown),
        totals=FixTotals(
            candidates=len(units) + sum(excluded.values()),
            eligible=len(units),
            shown=len(shown),
            excluded=excluded,
        ),
        by_improves={k: by_improves.get(k, 0) for k in FIX_IMPROVES},
        basis=dict(basis) if basis is not None else _basis(metrics),
    )


__all__ = [
    "CLASS_MARKERS",
    "DEFAULT_LIMIT",
    "DISPATCH_SHARE",
    "GAIN_CUTS",
    "GROWS_ONLY_MARKERS",
    "HEAD",
    "HEAD_PER_KIND",
    "LOW_VALUE_KINDS",
    "MIN_WORTH",
    "SIZE_MARKERS",
    "SMALL_CCN",
    "SMALL_NLOC",
    "build_fix_first",
    "hot_cut",
    "hot_cut_offset",
]
