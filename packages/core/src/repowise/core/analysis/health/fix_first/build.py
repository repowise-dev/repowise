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
    ``params``, ``returns``, ``suggested_name``, ``anchors``; a move's destination; a
    split's named groups; a helper's ``occurrences``) and ``rank`` / ``rank_json``
    (``leaves_above_bar``). A plan row that also
    carries ``refactoring_type`` ``extract_method``, ``file_path`` and
    ``target_symbol`` can give a finding with no plan of its own its first
    concrete step. Every Extract Helper plan's occurrences say where verified
    duplicates sit.
``dead_code``
    ``dead_code_findings`` rows: ``kind``, ``file_path``, ``symbol_name``,
    ``start_line``, ``end_line``, ``confidence``, ``safe_to_delete``,
    ``status``. A unit an open row of :data:`DEAD_CONFIDENCE` or more (or one
    safe to delete) covers is ``unreachable``: the fix is to delete it.
``function_changes``
    ``git_function_blame`` ``mod_count`` by ``symbol_id`` (``path::name``):
    the commits that changed each function. ``None`` when the repository keeps
    no function history; a function missing from it changed in none.
``blame_skipped``
    Paths the blame pass skipped for size: their functions have no history,
    so the file's churn orders them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.complexity.dispatch import DISPATCH_SHARE
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.queue.counts import COVERED_BY_PLAN, NOT_FILE_LEAD, Judgement
from repowise.core.analysis.health.queue.eligibility import (
    DEAD_CONFIDENCE,
    DEFAULT_QUEUE_CONTEXTS,
    Tally,
    Verdict,
    dead_spans,
    finding_verdict,
    path_verdict,
    perf_fix_verdict,
    refactor_verdict,
)
from repowise.core.analysis.health.queue.order import order
from repowise.core.analysis.health.rows import detail_map, field
from repowise.core.analysis.health.worth import SIZE_MARKERS, by_function

from . import text
from ._files import _Files
from ._parts import (  # noqa: F401  (moved helpers re-exported)
    CLASS_MARKERS,
    DEFAULT_LIMIT,
    HOT_QUANTILE,
    MAX_CONTEXT,
    MAX_FACTS,
    MAX_STEPS,
    MAX_TESTS,
    SIZE_BREAK_UP,
    Rows,
    Validate,
    _about,
    _num,
    _open,
    _Unit,
    hot_cut,
    hot_cut_offset,
    symbol_key,
)
from .finding_units import _finding_rank, _finding_unit
from .model import FIX_EXCLUSIONS, FIX_IMPROVES, FixFirstQueue, FixTotals
from .perf_units import _perf_step_text, _perf_unit  # noqa: F401
from .plan_steps import _concrete, _extractions, _plan_body, _refactor_step  # noqa: F401
from .refactor_units import _refactor_unit


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
    function_changes: Mapping[str, int] | None = None,
    blame_skipped: Iterable[str] = (),
) -> tuple[dict[str, tuple[list[Any], list[Any]]], _Files]:
    """Findings split into (code shape, history) by file, and the file facts."""
    split = {path: split_by_origin(rows) for path, rows in _shown_findings(findings).items()}
    files = _Files(
        metrics,
        {p: hist for p, (_shape, hist) in split.items() if hist},
        by_function(f for shape, _hist in split.values() for f in shape),
        hot_cuts,
        _clone_spans(plans),
        _extractions(plans),
        dead_spans(dead_code),
        function_changes,
        blame_skipped,
        {p: shape + hist for p, (shape, hist) in split.items()},
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
    verdicts, lead = _shape_verdicts(shape, files)
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


def _shape_verdicts(shape: list[Any], files: _Files) -> tuple[dict[int, Verdict], Any | None]:
    """Each code-shape finding's verdict, by ``id``, and the lead of the
    eligible ones: the one rule the queue and the stored judgement share."""

    def first_step(finding: Any) -> bool:
        return files.first_step(finding) is not None

    verdicts = {id(f): finding_verdict(f, files, first_step) for f in shape}
    return verdicts, primary_finding([f for f in shape if verdicts[id(f)].eligible])


class _Gate:
    """The scope rule for one queue build, counting every unit it turns away."""

    def __init__(self, files: _Files, keep_tests: bool) -> None:
        self.files = files
        self.keep_tests = keep_tests
        self.tally = Tally(FIX_EXCLUSIONS)

    def exclude(self, verdict: Verdict, path: str, symbol: str | None = None) -> bool:
        return self.tally.add(verdict, (path, text.short_symbol(symbol)))

    def scope(self, path: str, context: str | None = None) -> Verdict:
        """Why ``path`` is out of scope, counted; eligible when it is in."""
        files = self.files
        verdict = path_verdict(
            path, files.is_test(path), context, files.origin(path), keep_tests=self.keep_tests
        )
        self.tally.add(verdict)
        return verdict


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
    function_changes: Mapping[str, int] | None = None,
    blame_skipped: Iterable[str] = (),
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
    ``function_changes`` orders units inside a band by how often their
    function changed (module docstring); without it, and in a
    ``blame_skipped`` file, the file's churn does.
    """
    metrics = list(metrics)
    plans = list(plans)
    split, files = _prepare(
        metrics, findings, plans, dead_code, hot_cuts, function_changes, blame_skipped
    )
    gate = _Gate(files, keep_tests=scope == "all")
    units, planned_files = _refactor_units(refactoring, plans, gate, judged)
    units += _perf_units(performance, gate, symbol_lines)
    units += _finding_units(split, planned_files, gate, validate)
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
            candidates=len(units) + gate.tally.total,
            eligible=len(units),
            shown=len(shown),
            excluded=gate.tally.excluded,
            dormant=len(gate.tally.dormant),
        ),
        by_improves={k: by_improves.get(k, 0) for k in FIX_IMPROVES},
        basis=dict(basis) if basis is not None else _basis(metrics),
    )


def _refactor_units(
    refactoring: Rows, plans: list[Any], gate: _Gate, judged: dict[str, Judgement] | None
) -> tuple[list[_Unit], set[str]]:
    """Units for the open refactoring plans in scope, and the files they cover."""
    files = gate.files
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
        verdict = gate.scope(path)
        details = detail_map(row)
        steps = list(details.get("steps") or [])
        gain = _num(field(row, "recoverable_health"))
        if verdict.eligible:
            verdict = refactor_verdict(gain, steps, files, path, concrete)
            gate.exclude(verdict, path, steps[0].get("target_symbol") if steps else None)
        if not verdict.eligible:
            _record(judged, row, Judgement(verdict.reason))
            continue
        unit = _refactor_unit(row, details, steps, gain, plan_rows, files)
        units.append(unit)
        _record(judged, row, Judgement(None, unit.value, unit.tier))
        # Only a plan that became an item speaks for the file's findings; an
        # excluded one leaves them to compete on their own.
        planned_files.add(path)
    return units, planned_files


def _record(judged: dict[str, Judgement] | None, row: Any, judgement: Judgement) -> None:
    if judged is not None:
        judged[field(row, "opportunity_id")] = judgement


def _perf_units(
    performance: Rows, gate: _Gate, symbol_lines: Mapping[str, int] | None
) -> list[_Unit]:
    """One unit per open performance intervention in scope."""
    contexts = DEFAULT_QUEUE_CONTEXTS | {"test"} if gate.keep_tests else DEFAULT_QUEUE_CONTEXTS
    lines = symbol_lines or {}
    units: list[_Unit] = []
    for (path, symbol), rows in _interventions(performance).items():
        if not gate.scope(path, "production").eligible:
            continue
        rows.sort(key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")))
        verdict, worth = perf_fix_verdict(
            rows,
            contexts,
            lambda path=path, symbol=symbol: gate.files.unreachable(
                path, symbol, lines.get(symbol)
            ),
        )
        if not gate.exclude(verdict, path, symbol):
            units.append(_perf_unit(worth, gate.files, symbol_lines))
    return units


def _interventions(performance: Rows) -> dict[tuple[str, str], list[Any]]:
    """Open performance rows by (file, intervention symbol)."""
    # Ceiling: grouped by the intervention as stored, until persistence writes
    # one row per intervention.
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in performance:
        if _open(row):
            path = field(row, "file_path") or ""
            groups[(path, field(row, "intervention_symbol") or path)].append(row)
    return groups


def _finding_units(
    split: dict[str, tuple[list[Any], list[Any]]],
    planned_files: set[str],
    gate: _Gate,
    validate: Validate | None,
) -> list[_Unit]:
    """One unit per file whose code-shape findings no plan covers: its lead."""
    files = gate.files
    units: list[_Unit] = []
    for path, (shape, history) in split.items():
        if path in planned_files:
            continue
        lead = primary_finding(shape)
        if lead is None and not history:
            continue  # advisory only: nothing to fix, nothing to count
        if not gate.scope(path).eligible:
            continue
        if lead is None:
            gate.tally.add(Verdict("history_only"))
            continue
        # A finding that is no candidate leaves the file's others to compete;
        # the file is counted under its own lead's reason when none is left.
        verdicts, eligible = _shape_verdicts(shape, files)
        if eligible is None:
            culprit = _culprit(shape, lead, verdicts)
            gate.exclude(verdicts[id(culprit)], path, field(culprit, "function_name"))
            continue
        units.append(_finding_unit(eligible, files, files.first_step(eligible), validate))
    return units


def _culprit(shape: list[Any], lead: Any, verdicts: dict[int, Verdict]) -> Any:
    """The finding a file with no candidate is counted under: its lead when
    that is out, else the first one out."""
    if not verdicts[id(lead)].eligible:
        return lead
    return next(f for f in shape if not verdicts[id(f)].eligible)


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
    "symbol_key",
]
