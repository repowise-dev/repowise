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
    sentence quotes, ``deprecated`` for a function on its way out and
    ``gated_off`` for one a constant-false flag switches off.
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
``dead_code``
    ``dead_code_findings`` rows: ``kind``, ``file_path``, ``symbol_name``,
    ``start_line``, ``end_line``, ``confidence``, ``safe_to_delete``,
    ``status``. A unit an open row of :data:`DEAD_CONFIDENCE` or more (or one
    safe to delete) covers is ``unreachable``: the fix is to delete it.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.complexity.dispatch import DISPATCH_SHARE
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.queue.counts import COVERED_BY_PLAN, NOT_FILE_LEAD, Judgement
from repowise.core.analysis.health.queue.eligibility import (
    DEAD_CONFIDENCE,
    DEFAULT_QUEUE_CONTEXTS,
    UNAUDITED_KINDS,
    DeadSpan,
    Tally,
    Verdict,
    dead_spans,
    dead_target,
    finding_verdict,
    path_verdict,
    perf_fix_verdict,
    perf_low_priority,
    refactor_verdict,
)
from repowise.core.analysis.health.queue.order import LEVEL_RANK, order
from repowise.core.analysis.health.queue.value import (
    perf_confidence,
    perf_ready,
    perf_value,
    perf_worth,
    removed,
    shape_value,
    size_value,
    tier,
    worth,
)
from repowise.core.analysis.health.refactoring.render import NAME_PLACEHOLDER
from repowise.core.analysis.health.rows import detail_map, field, json_field
from repowise.core.analysis.health.scoring import biomarker_dimension
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.worth import (
    SIZE_MARKERS,
    WORTH_MAGNITUDE,
    execution_role,
    low_priority,
    magnitude,
    measure,
    worth_size,
)
from repowise.core.analysis.next_call import ActionCommand

from . import text
from .model import (
    FIX_EFFORTS,
    FIX_EXCLUSIONS,
    FIX_IMPROVES,
    FixAction,
    FixConfidence,
    FixContext,
    FixEffortEstimate,
    FixFact,
    FixFirstQueue,
    FixGain,
    FixItem,
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

#: Measured size (CCN, lines or nesting alone, before the severity floor and
#: the hot-file bonus) from which an item leads as "break up", naming the whole
#: problem: CCN 40, 200 lines or nesting 6.
SIZE_BREAK_UP = WORTH_MAGNITUDE
#: A file in the top fifth of production files by dependents is central (one
#: value step); by churn it changes often (orders within a band only).
HOT_QUANTILE = 0.8
MAX_FACTS = 5
MAX_TESTS = 5
MAX_STEPS = 5
MAX_CONTEXT = 3
DEFAULT_LIMIT = 10
#: Class-level findings: a fix names member groups, which only a plan holds.
CLASS_MARKERS = frozenset({"low_cohesion", "god_class"})


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
    worth: float
    confidence: str
    effort: str
    improves: str
    write: Callable[[int], FixItem]
    #: False for a performance cause whose marker has not cleared the bar for
    #: leading (``opportunity_rank.may_lead``): listed, never first.
    may_lead: bool = True


def _open(row: Any) -> bool:
    return (field(row, "status") or "open") == "open"


def _num(value: Any) -> float:
    return float(value or 0.0)


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


#: Validation profile for a finding with no stored plan: (path, function, line start, line end).
Validate = Callable[[str, Any, Any, Any], Mapping[str, Any] | None]


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
        None if tests else profile.get("prerequisite"),
    )


def _step_command(check: Mapping[str, Any] | None, item_command: str | None) -> str | None:
    """A step's own command (a validation profile, or a plan step's ``verify``),
    kept only when it differs from the item's."""
    commands = (check or {}).get("commands") or []
    return commands[0] if commands and commands[0] != item_command else None


def _helper_code(body: Mapping[str, Any]) -> dict[str, str | None]:
    """An Extract Method plan's rendered helper header and call, when it wrote them."""
    symbol = body.get("new_symbol") or {}
    site = body.get("call_site") or {}
    return {
        "signature": symbol.get("signature_text") if isinstance(symbol, dict) else None,
        "call": site.get("new_text") if isinstance(site, dict) else None,
    }


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
    ) -> None:
        self.by_path = {field(m, "file_path"): m for m in metrics}
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
        return self.commits(path) >= self.churn_cut

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
                is_async=bool(body.get("needs_async", False)),
            )
            return FixStep(
                1,
                f"Extract lines {start}-{end} of {tail} into {into}",
                path,
                start,
                **_helper_code(body),
            )
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
    ) -> str:
        """One sentence: the problem, then why it matters here (who imports
        the file, how often it changes, how much of it tests run)."""
        deps = dependents if dependents is not None else self.dependents(path)
        head = measured or fallback.rstrip(". ")
        if cloned:
            head += f", {text.CLONED}"
        return f"{head}{text.exposure(deps, self.commits(path), self.coverage(path))}."

    def context(self, path: str) -> tuple[FixContext, ...]:
        """History is context: shown beside the item, never lifting its band."""
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
    confidence = confidence if confidence in LEVEL_RANK else "low"
    effort = effort if effort in FIX_EFFORTS else "M"
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


def _refactor_step(
    order: int, step: Mapping[str, Any], plan: Any, command: str | None = None
) -> FixStep:
    kind = step.get("refactoring_type") or ""
    path = step.get("file_path") or ""
    sym = text.short_symbol(step.get("target_symbol")) or text.basename(path)
    start, end = _span(plan)
    body = _plan_body(plan)
    code: dict[str, str | None] = {}
    if kind == "extract_method":
        code = _helper_code(body)
        into = text.signature(
            body.get("suggested_name"),
            list(body.get("params") or []),
            list(body.get("returns") or []),
            is_async=bool(body.get("needs_async", False)),
        )
        where = f"lines {start}-{end} of {sym}" if start and end else f"part of {sym}"
        line_text = f"Extract {where} into {into}"
    elif kind == "extract_helper":
        line_text = f"Replace the duplicate in {sym} with a call to one shared helper"
    elif kind == "extract_class":
        groups = [g for g in body.get("groups") or [] if g.get("methods")]
        members = sorted(groups, key=lambda g: len(g["methods"]))[0]["methods"] if groups else []
        line_text = (
            f"Move {', '.join(members[:4])}"
            + (f" and {len(members) - 4} more" if len(members) > 4 else "")
            + f" out of {sym} into a new class"
            if members
            else f"Move a cohesive group of {sym}'s methods into a new class"
        )
    elif kind == "split_file":
        names = [g.get("name") for g in body.get("groups") or [] if g.get("name")]
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
            else _uncut_cycle_text(path, plan)
        )
    elif kind == "move_method":
        dest = body.get("to_class") or text.basename(body.get("to_file") or "")
        line_text = (
            f"Move {sym} to {dest}" if dest else f"Move {sym} to the class it uses most"
        )
    else:
        line_text = f"Apply the {text.humanize(kind)} step to {sym}"
    mechanical = (step.get("applicability") or {}).get("classification") == "mechanical"
    return FixStep(
        order, line_text, path, start or step.get("line_start"), mechanical, **code, command=command
    )


def _uncut_cycle_text(path: str, plan: Any) -> str:
    """A cycle step with no import line to cut, labelled when it is idiomatic."""
    if _plan_body(plan).get("idiom"):
        return (
            f"Optional: {text.basename(path)} is in an import cycle within one "
            "directory, which is idiomatic in this language"
        )
    return f"Break the import cycle at {text.basename(path)}"


def _cut_edge(plan: Any) -> dict[str, Any] | None:
    """The first cut edge whose import line is stored.

    An idiomatic cycle (one directory of a language that compiles mutual
    references in one pass) has no edge to cut, so it never reads as a must-do.
    """
    if _plan_body(plan).get("idiom"):
        return None
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
    steps, held_back = _audited_steps(steps)
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
    central, churning = files.central(path), files.churning(path)
    dimension = biomarker_dimension(marker) if marker else "maintainability"
    shape = files.shape(path, lead.get("target_symbol"))
    size = size_value(shape, central)
    ccn_removed = sum(_extraction_worth(plans.get(s.get("plan_id")))[0] for s in steps)
    deps = details.get("dependents")
    deps = files.dependents(path) if deps is None else deps
    cloned = lead_type == "extract_method" and files.cloned(path, shape)
    low = low_priority(marker, shape, function_size=lead_type == "extract_method")

    def fields() -> dict[str, Any]:
        lead_plan = plans.get(lead.get("plan_id"))
        start, end = _span(lead_plan)
        body = _plan_body(lead_plan)
        if (
            magnitude(shape) >= SIZE_BREAK_UP
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
            into = f" into {body.get('suggested_name') or NAME_PLACEHOLDER}"
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
                name=body.get("suggested_name") or NAME_PLACEHOLDER,
            )
            if len(steps) > 1:
                title += f" (+{text.plural(len(steps) - 1, 'more step')})"
        dependents = deps
        profiles = {p.get("id"): p for p in details.get("validation_profiles") or []}
        profile = profiles.get(lead.get("validation_profile_id")) or next(
            iter(profiles.values()), None
        )
        verify = _verify(profile)
        fix_steps = tuple(
            _refactor_step(
                i + 1,
                s,
                plans.get(s.get("plan_id")),
                _step_command(profiles.get(s.get("validation_profile_id")), verify.command),
            )
            for i, s in enumerate(steps)
        )
        size_text = text.size_line(shape)
        facts = [
            FixFact("health recoverable", f"+{gain:.1f}", "inferred"),
            *([FixFact("size", size_text)] if size_text else []),
            FixFact("steps", f"{len(steps)} ({mechanical_n} mechanical)"),
            *([FixFact("steps held back", text.held_back(held_back))] if held_back else []),
            *files.common_facts(path, dependents),
        ]
        nloc = files.nloc(path)
        if nloc:
            facts.append(FixFact("file size", f"{nloc} lines"))
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
            "verify": verify,
            "context": files.context(path),
            "source": FixSource(
                field(row, "opportunity_id"),
                plan_ids,
                tuple(details.get("lead_finding_ids") or ()),
            ),
            "next_call": ActionCommand.call(
                "The full plan: ordered steps, validation and evidence",
                "get_health",
                {"opportunity_id": field(row, "opportunity_id")},
            ),
        }

    effort_bucket = field(row, "effort_bucket") or "M"
    effort_bucket = effort_bucket if effort_bucket in FIX_EFFORTS else "M"
    return _finish(
        kind="refactor",
        source_id=field(row, "opportunity_id"),
        value=shape_value(gain, shape, cloned, central=central),
        cold_value=shape_value(gain, shape, cloned, central=False),
        ready=mechanical or confidence == "high",
        worth=worth(removed(ccn_removed, gain), deps, churning),
        confidence=confidence if confidence in LEVEL_RANK else "medium",
        effort=effort_bucket,
        improves=dimension if dimension in FIX_IMPROVES else "maintainability",
        rank_inputs=lambda: [
            FixRankFact("health gain", f"{gain:.2f}"),
            FixRankFact("complexity removed", str(ccn_removed)),
            FixRankFact("problem size", str(size if low is None else worth_size(shape))),
            *_reach_facts(deps, central, churning),
            FixRankFact("duplicate inside", "yes" if cloned else "no"),
        ],
        fields=fields,
        low=low,
    )


def _reach_facts(deps: int | None, central: bool, churning: bool) -> list[FixRankFact]:
    """What reach and history add: importers lift the band when central,
    churn only orders inside it."""
    return [
        FixRankFact("files that import it", f"{deps or 0}{' (top fifth)' if central else ''}"),
        FixRankFact("changes often", "yes" if churning else "no"),
    ]


def _audited_steps(steps: list[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """The steps an item may carry, and how many it held back: steps of a
    kind not yet audited stay in the full plan, never in the item."""
    kept = [s for s in steps if s.get("refactoring_type") not in UNAUDITED_KINDS]
    return kept, len(steps) - len(kept)


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
        idiom = " within one directory (idiomatic)" if evidence.get("idiom") else ""
        return f"{name} is in an import cycle of {evidence['cycle_size']} files{idiom}"
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


def _perf_step_text(step: Mapping[str, Any], path: str) -> str:
    """A plan step's action, naming its function once: an action that already
    says the name is not followed by it again."""
    action = step.get("action") or ""
    name = text.scope_name(step.get("symbol"), path)
    # A whole word that is not an attribute: ``get`` is not named by a quoted
    # ``session.get`` call, nor by ``getattr``.
    short = name.rsplit(".", 1)[-1] if name else ""
    if not name or re.search(rf"(?<![\w.]){re.escape(short)}(?!\w)", action):
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
        verify = _verify(plan.get("validation"))
        steps = tuple(
            FixStep(
                int(s.get("order") or i + 1),
                _perf_step_text(s, path),
                s.get("file_path") or path,
                s.get("line") or lines.get(s.get("symbol") or ""),
                s.get("applicability") == "mechanical",
                command=_step_command(s.get("verify"), verify.command),
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
            "verify": verify,
            "context": files.context(path),
            "source": FixSource(field(lead, "opportunity_id")),
            "next_call": ActionCommand.call(
                "The full opportunity: ordered steps, validation and other causes here",
                "get_health",
                {"opportunity_id": field(lead, "opportunity_id")},
            ),
        }

    confidence = perf_confidence(facets)
    call_sites = int(field(lead, "affected_call_sites_total") or 0)
    return _finish(
        kind="perf_fix",
        may_lead=details.get("may_lead") is not False,
        source_id=f"{path}::{symbol or path}",
        value=perf_value(lead, facets),
        ready=perf_ready(lead, plan),
        worth=perf_worth(call_sites, files.churning(path)),
        confidence=confidence,
        effort=effort or "M",
        improves="performance",
        low=perf_low_priority(lead),
        rank_inputs=lambda: [
            FixRankFact("runs in", field(lead, "execution_context") or "unknown"),
            FixRankFact("run by", execution_role(lead)),
            FixRankFact("call sites", str(call_sites)),
            FixRankFact("loop size", text.loop_size(magnitude)),
            FixRankFact(
                "boundary", text.BOUNDARY_NOUN.get(field(lead, "boundary_kind") or "", "none")
            ),
        ],
        fields=fields,
    )


# --- findings with no plan ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FindingRank:
    """What a finding's value and tier read: one rule for the item and the stored judgement."""

    shape: dict[str, int]
    central: bool
    churning: bool
    cloned: bool
    low: str | None
    value: int
    cold_value: int
    ccn_removed: int
    worth: float

    @property
    def tier(self) -> str:
        return tier(self.cold_value, "medium", False, self.low)[0]


def _finding_rank(finding: Any, files: _Files) -> _FindingRank:
    path = field(finding, "file_path")
    marker = field(finding, "biomarker_type") or ""
    impact = _num(field(finding, "health_impact"))
    central, churning = files.central(path), files.churning(path)
    shape = files.shape(path, field(finding, "function_name"))
    cloned = marker in SIZE_MARKERS and files.cloned(path, shape)
    # Breaking up the whole function takes out its whole CCN.
    ccn = shape.get("ccn", 0) if marker in SIZE_MARKERS else 0
    return _FindingRank(
        shape,
        central,
        churning,
        cloned,
        low_priority(marker, shape, error_kind=detail_map(finding).get("kind")),
        shape_value(impact, shape, cloned, central=central),
        shape_value(impact, shape, cloned, central=False),
        ccn,
        worth(removed(ccn, impact), files.dependents(path), churning),
    )


def _finding_unit(lead: Any, files: _Files, first: FixStep, validate: Validate | None) -> _Unit:
    path = field(lead, "file_path")
    marker = field(lead, "biomarker_type") or ""
    function = field(lead, "function_name")
    impact = _num(field(lead, "health_impact"))
    public_id = field(lead, "public_id")
    dimension = biomarker_dimension(marker)
    rank = _finding_rank(lead, files)
    shape, cloned, low = rank.shape, rank.cloned, rank.low
    size = size_value(shape, rank.central)

    def fields() -> dict[str, Any]:
        where = function or text.basename(path)
        summary = text.first_sentence(suggestion_for(marker))
        line = field(lead, "line_start")
        if magnitude(shape) >= SIZE_BREAK_UP and function and marker in SIZE_MARKERS:
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
                    FixFact("severity", _severity(field(lead, "severity"), low)),
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
            "verify": _verify(
                validate(path, function, field(lead, "line_start"), field(lead, "line_end"))
                if validate
                else None
            ),
            "context": files.context(path),
            "source": FixSource(None, (), (public_id,) if public_id else ()),
            "next_call": ActionCommand.call(
                "Every open finding in the file, with its line and reason",
                "get_health",
                {"targets": [path], "include": ["biomarkers"]},
                cli=f"repowise health --file {path}",
            ),
        }

    return _finish(
        kind="finding",
        source_id=public_id or f"{path}::{marker}::{function or ''}",
        value=rank.value,
        cold_value=rank.cold_value,
        ready=False,
        worth=rank.worth,
        confidence="medium",
        effort="M",
        improves=dimension if dimension in FIX_IMPROVES else "defect",
        rank_inputs=lambda: [
            FixRankFact("health gain", f"{impact:.2f}"),
            FixRankFact("complexity removed", str(rank.ccn_removed)),
            FixRankFact("problem size", str(size if low is None else worth_size(shape))),
            *_reach_facts(files.dependents(path), rank.central, rank.churning),
            FixRankFact("duplicate inside", "yes" if cloned else "no"),
        ],
        fields=fields,
        low=low,
    )


def _severity(severity: str | None, low: str | None) -> str:
    """The detector's severity; on a later item it says the tier overrides it."""
    severity = severity or "unknown"
    return severity if low is None else f"{severity} by the detector; lower priority by shape"


def _dead_spans(rows: Rows) -> dict[str, list[DeadSpan]]:
    """Sure, open dead-code findings by file: ``(symbol, start, end)``, the
    symbol ``None`` for an unreachable file."""
    return dead_spans(rows)


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


def _by_function(findings: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    out: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for f in findings:
        name = field(f, "function_name")
        if name:
            out[(field(f, "file_path"), name)].append(f)
    return dict(out)


def _basis(metrics: Rows) -> dict[str, str | None]:
    stamped = [(str(at), at, m) for m in metrics if (at := field(m, "updated_at"))]
    if not stamped:
        return {"analyzed_commit": None, "health_analyzed_at": None}
    _key, at, latest = max(stamped, key=lambda s: s[0])
    return {
        "analyzed_commit": field(latest, "analyzed_commit"),
        "health_analyzed_at": at.isoformat() if hasattr(at, "isoformat") else str(at),
    }


def _shown_findings(findings: Iterable[Any]) -> dict[str, list[Any]]:
    """Open code-health findings that score, of a shown type, by file."""
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
    return by_file


def _prepare(
    metrics: list[Any],
    findings: Iterable[Any],
    plans: list[Any],
    dead_code: Rows,
    hot_cuts: tuple[float, float] | None,
) -> tuple[dict[str, tuple[list[Any], list[Any]]], _Files]:
    """Findings split into (code shape, history) by file, and the file facts."""
    split = {path: split_by_origin(rows) for path, rows in _shown_findings(findings).items()}
    files = _Files(
        metrics,
        {p: hist for p, (_shape, hist) in split.items() if hist},
        _by_function(f for shape, _hist in split.values() for f in shape),
        hot_cuts,
        _clone_spans(plans),
        _extractions(plans),
        _dead_spans(dead_code),
    )
    return split, files


def judge_findings(
    *,
    metrics: Rows = (),
    findings: Rows = (),
    plans: Rows = (),
    dead_code: Rows = (),
    hot_cuts: tuple[float, float] | None = None,
    planned_files: Iterable[str] = (),
) -> dict[Any, Judgement]:
    """Each open code-health finding's place in the queue, keyed by its ``id``.

    The rule Fix first applies to a file's findings: a file whose plan became
    an item is that plan's (``covered_by_plan``); then the file's scope, then
    history-only markers, then :func:`finding_verdict`; of a file's eligible
    findings only the one that leads its item stays eligible (``not_file_lead``),
    valued and tiered as that item. ``planned_files`` are the files of the
    eligible refactoring plans. Pass every open finding of the files judged, so
    a function's shape is whole.
    """
    metrics = list(metrics)
    split, files = _prepare(metrics, findings, list(plans), dead_code, hot_cuts)
    planned = set(planned_files)
    out: dict[Any, Judgement] = {}
    for path, (shape, history) in split.items():
        if path in planned:
            out.update(dict.fromkeys((field(f, "id") for f in (*shape, *history)), _COVERED))
            continue
        scope = path_verdict(path, files.is_test(path), None, files.origin(path))
        history_verdict = scope if not scope.eligible else Verdict("history_only")
        for f in history:
            out[field(f, "id")] = Judgement.of(history_verdict)
        out.update(_judge_file_shape(shape, scope, files))
    return out


_COVERED = Judgement(COVERED_BY_PLAN)
_NOT_LEAD = Judgement(NOT_FILE_LEAD)


def _judge_file_shape(shape: list[Any], scope: Verdict, files: _Files) -> dict[Any, Judgement]:
    """One file's code-shape findings: only the lead of the eligible ones is eligible."""
    if not scope.eligible:
        return {field(f, "id"): Judgement.of(scope) for f in shape}

    def first_step(finding: Any) -> bool:
        return files.first_step(finding) is not None

    verdicts = {id(f): finding_verdict(f, files, first_step) for f in shape}
    lead = primary_finding([f for f in shape if verdicts[id(f)].eligible])
    out: dict[Any, Judgement] = {}
    for f in shape:
        verdict = verdicts[id(f)]
        if not verdict.eligible:
            out[field(f, "id")] = Judgement.of(verdict)
        elif f is not lead:
            out[field(f, "id")] = _NOT_LEAD
        else:
            rank = _finding_rank(f, files)
            out[field(f, "id")] = Judgement(None, rank.value, rank.tier)
    return out


def build_fix_first(
    *,
    metrics: Rows = (),
    findings: Rows = (),
    refactoring: Rows = (),
    performance: Rows = (),
    plans: Rows = (),
    dead_code: Rows = (),
    limit: int | None = DEFAULT_LIMIT,
    scope: str = "production",
    basis: Mapping[str, str | None] | None = None,
    item_id: str | None = None,
    hot_cuts: tuple[float, float] | None = None,
    symbol_lines: Mapping[str, int] | None = None,
    validate: Validate | None = None,
    judged: dict[str, Judgement] | None = None,
) -> FixFirstQueue:
    """One ranked queue of what to fix, from stored rows (shapes in the module docstring).

    ``scope="all"`` keeps test files (labelled in ``context``); ``limit=None``
    keeps every eligible item. ``basis`` is the analysis stamp when the caller
    read it apart; otherwise it comes from the metrics' ``updated_at``.
    ``item_id`` keeps just that item, at its rank, for a lookup by id.
    ``hot_cuts`` are the (churn, dependents) thresholds for a churning and a central file when the
    caller measured them over more files than it passed in ``metrics``; see
    :func:`hot_cut` for the rule. ``symbol_lines`` maps a symbol id
    (``path::name``) to its first line, for a performance plan step that
    names a function but stored no line. ``validate(path, function, start, end)``
    is the validation profile (``basis``, ``via``, ``total``, ``tests``,
    ``commands``) of a finding with no plan, read lazily for the items shown;
    without it such an item's Verify stays unknown. ``dead_code`` rows make
    the units they cover ``unreachable``. ``judged`` is filled with every open
    refactoring opportunity's judgement, by id, for the index to store.
    """
    metrics = list(metrics)
    keep_tests = scope == "all"
    plans = list(plans)
    split, files = _prepare(metrics, findings, plans, dead_code, hot_cuts)
    tally = Tally(FIX_EXCLUSIONS)

    def exclude(verdict: Verdict, path: str, symbol: str | None = None) -> bool:
        return tally.add(verdict, (path, text.short_symbol(symbol)))

    def scope_verdict(path: str, context: str | None = None) -> Verdict:
        """Why ``path`` is out of scope, counted; eligible when it is in."""
        verdict = path_verdict(
            path, files.is_test(path), context, files.origin(path), keep_tests=keep_tests
        )
        tally.add(verdict)
        return verdict

    plan_rows = {field(p, "public_id"): p for p in plans}

    def concrete(step: Mapping[str, Any]) -> bool:
        return _concrete(step, plan_rows.get(step.get("plan_id")))

    units: list[_Unit] = []
    planned_files: set[str] = set()
    for row in sorted(
        (r for r in refactoring if _open(r)),
        key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")),
    ):
        path = field(row, "file_path")
        verdict = scope_verdict(path)
        details = detail_map(row)
        steps = list(details.get("steps") or [])
        gain = _num(field(row, "recoverable_health"))
        if verdict.eligible:
            verdict = refactor_verdict(gain, steps, files, path, concrete)
            exclude(verdict, path, steps[0].get("target_symbol") if steps else None)
        if not verdict.eligible:
            if judged is not None:
                judged[field(row, "opportunity_id")] = Judgement(verdict.reason)
            continue
        unit = _refactor_unit(row, details, steps, gain, plan_rows, files)
        units.append(unit)
        if judged is not None:
            judged[field(row, "opportunity_id")] = Judgement(None, unit.value, unit.tier)
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
    perf_contexts = DEFAULT_QUEUE_CONTEXTS | {"test"} if keep_tests else DEFAULT_QUEUE_CONTEXTS
    lines = symbol_lines or {}
    for (path, symbol), rows in groups.items():
        if not scope_verdict(path, "production").eligible:
            continue
        rows.sort(key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")))
        verdict, worth = perf_fix_verdict(
            rows,
            perf_contexts,
            lambda path=path, symbol=symbol: files.unreachable(path, symbol, lines.get(symbol)),
        )
        if not exclude(verdict, path, symbol):
            units.append(_perf_unit(worth, files, symbol_lines))

    def first_step(finding: Any) -> bool:
        return files.first_step(finding) is not None

    for path, (shape, history) in split.items():
        if path in planned_files:
            continue
        lead = primary_finding(shape)
        if lead is None and not history:
            continue  # advisory only: nothing to fix, nothing to count
        if not scope_verdict(path).eligible:
            continue
        if lead is None:
            tally.add(Verdict("history_only"))
            continue
        # A finding that is no candidate leaves the file's others to compete;
        # the file is counted under its own lead's reason when none is left.
        verdicts = {id(f): finding_verdict(f, files, first_step) for f in shape}
        eligible = primary_finding([f for f in shape if verdicts[id(f)].eligible])
        if eligible is None:
            culprit = lead if not verdicts[id(lead)].eligible else next(
                f for f in shape if not verdicts[id(f)].eligible
            )
            exclude(verdicts[id(culprit)], path, field(culprit, "function_name"))
            continue
        units.append(_finding_unit(eligible, files, files.first_step(eligible), validate))

    ordered = order(units)
    if item_id is not None:
        shown = [(i, u) for i, u in enumerate(ordered) if u.id == item_id]
    else:
        keep = ordered if limit is None else ordered[: max(limit, 0)]
        shown = list(enumerate(keep))
    by_improves = Counter(u.improves for u in units)
    return FixFirstQueue(
        items=tuple(u.write(rank) for rank, u in shown),
        totals=FixTotals(
            candidates=len(units) + tally.total,
            eligible=len(units),
            shown=len(shown),
            excluded=tally.excluded,
            dormant=len(tally.dormant),
        ),
        by_improves={k: by_improves.get(k, 0) for k in FIX_IMPROVES},
        basis=dict(basis) if basis is not None else _basis(metrics),
    )


__all__ = [
    "CLASS_MARKERS",
    "DEAD_CONFIDENCE",
    "DEFAULT_LIMIT",
    "DISPATCH_SHARE",
    "SIZE_MARKERS",
    "build_fix_first",
    "hot_cut",
    "hot_cut_offset",
    "judge_findings",
]
