"""One measured function as one row: what it costs, why, what to do, who and what
to check it with, and the one command that verifies a fix.

Pure, over facts a host already read, so the engine, the REST routes, the CLI
and the MCP tools serve the same row:

- :func:`action_row` folds a ranked row's measurement, its next step
  (:mod:`.next_step`, which reads the run's ``inside`` and ``drivers``), its
  production callers weighted by what the run measured
  (:func:`.payloads.weighted_callers`), its tests (:mod:`.function_tests`), its
  owner and last change (:func:`.payloads.owner_history_payload`), the
  benchmark that measures it and what got slower (:mod:`.compare`) into an
  :class:`ActionRow`;
- :meth:`ActionRow.as_dict` is the JSON form, with ``sources`` naming where
  each field it holds came from;
- :func:`row_text` is the compact text form, one fact per line, the step first;
- :func:`row_compact` is the MCP form: the action fields, no bookkeeping.

**One verify command** (:func:`row_verify`). A benchmark that measures the
function beats a rerun of the row's workload: it isolates the function, is
repeatable and prints numbers per operation, where a workload rerun measures
everything else that ran too. Without one, the workload's own rerun
(:func:`.next_step.verify_command`): ``perf measure`` for an entry point, the
load again for a load test, the next window for production. A unit-test run
has neither, so its rows have no verify command.

Silence holds as everywhere (PLAN section 2): no ``next_step`` without a
dominant callee, no owner or last change without the function's own blame row
(never the file's), no ``slower`` unless a comparison names the function.
"""

from __future__ import annotations

import posixpath
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from repowise.core.ingestion.symbol_identity import file_of_symbol

from .function_tests import PER_TEST_MAP, RowTests
from .next_step import NextStep, next_step, verify_command
from .payloads import file_language, weighted_callers

#: ``verify`` reruns the benchmarks that measure the function.
VERIFY_BENCHMARK = "benchmark"
#: ``verify`` reruns the row's workload.
VERIFY_WORKLOAD = "workload"

#: Production callers a row lists at most.
ROW_CALLERS = 3

#: A row whose tests are unknown: never mapped.
_UNMAPPED = RowTests(None, None)


def row_verify(
    benchmark: str | None, workload_name: str | None, workload_class: str | None
) -> tuple[str | None, str | None]:
    """The row's one verify command and its basis (:data:`VERIFY_BENCHMARK` or
    :data:`VERIFY_WORKLOAD`); ``(None, None)`` when neither has one.

    *benchmark* is the command that reruns the benchmarks measuring the
    function (:func:`.function_tests.benchmark_command`). It wins: it times the
    function alone, where a workload rerun times everything else that ran.
    """
    if benchmark:
        return benchmark, VERIFY_BENCHMARK
    command = verify_command(workload_name, workload_class)
    return (command, VERIFY_WORKLOAD) if command else (None, None)


def row_step(
    symbol_id: str,
    inside: Sequence[Mapping[str, Any]] | None,
    drivers: Sequence[Mapping[str, Any]] | None,
    workload_name: str | None,
    workload_class: str | None,
    findings: Iterable[Mapping[str, Any]] = (),
) -> NextStep | None:
    """The next step for one measured function from its run's ``inside`` and
    ``drivers`` (:func:`.next_step.next_step`), in its file's language."""
    row = {
        "symbol_id": symbol_id,
        "inside": inside,
        "drivers": drivers,
        "workload_name": workload_name,
        "workload_class": workload_class,
    }
    return next_step(row, file_language(file_of_symbol(symbol_id)), findings)


def row_slower(
    symbol_id: str, comparisons: Iterable[Any], workload: str | None = None
) -> dict[str, Any] | None:
    """What got slower about *symbol_id*, from the first ``slower`` comparison
    (:class:`.compare.Comparison`) of *workload* (the row's own run's) that
    names it: its own measure that got worse (a profile's self seconds) and
    the commits in the range that changed it. ``None`` when no comparison of
    that workload names it, so a row never reads slower for another workload."""
    for c in comparisons:
        if getattr(c, "verdict", None) != "slower" or (
            workload is not None and c.workload != workload
        ):
            continue
        own = next((s for s in c.slower if s.symbol_id == symbol_id), None)
        commits = [
            {"sha": commit.sha, "pr": commit.pr}
            for commit in (c.cause.commits if c.cause is not None else ())
            if symbol_id in commit.functions
        ]
        if own is None and not commits:
            continue
        out: dict[str, Any] = {"workload": c.workload, "base_commit": c.base_commit}
        if own is not None:
            out.update(metric=own.metric, before=own.before, after=own.after, ratio=own.ratio)
            if own.unit:
                out["unit"] = own.unit
        out["changed_in"] = commits
        return out
    return None


@dataclass(frozen=True)
class ActionRow:
    """One ranked production function of one run, everything a fix needs."""

    symbol_id: str
    run_id: str
    #: 1-based place among the run's production functions by self time.
    rank: int
    #: Self time (waits excluded) as a share of the run, with its interval.
    share: float | None
    share_low: float | None
    share_high: float | None
    #: Inclusive share, context only.
    inclusive: float | None
    samples: int | None
    freshness: str | None
    #: ``[start, end]``; ``None`` when the symbol is no longer indexed.
    lines: list[int] | None = None
    #: Production callers by the call graph; ``None`` when it does not know the function.
    callers_total: int | None = None
    #: At most :data:`ROW_CALLERS`, the ones the run measured first.
    callers: list[str] = field(default_factory=list)
    caller_shares: dict[str, float] = field(default_factory=dict)
    owner: dict[str, Any] | None = None
    last_change: dict[str, Any] | None = None
    tests: list[str] | None = None
    tests_basis: str | None = None
    #: What the time is and what to do (:class:`.next_step.NextStep`), its
    #: ``verify`` left to the row's own.
    step: NextStep | None = None
    verify: str | None = None
    verify_basis: str | None = None
    on_static_findings: list[str] = field(default_factory=list)
    slower: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The JSON form (``MeasuredRow``). ``on_static_findings`` and
        ``slower`` only when the row has them; ``sources`` names where each
        field that holds a value came from."""
        step = self.step.as_dict() if self.step is not None else None
        if step is not None:
            step.pop("verify", None)
        out: dict[str, Any] = {
            "symbol_id": self.symbol_id,
            "file_path": file_of_symbol(self.symbol_id),
            "lines": self.lines,
            "run_id": self.run_id,
            "rank": self.rank,
            "share": self.share,
            "share_low": self.share_low,
            "share_high": self.share_high,
            "inclusive": self.inclusive,
            "samples": self.samples,
            "freshness": self.freshness,
            "callers_total": self.callers_total,
            "callers": self.callers,
            "caller_shares": self.caller_shares,
            "owner": self.owner,
            "last_change": self.last_change,
            "tests": self.tests,
            "tests_basis": self.tests_basis,
            "next_step": step,
            "verify": self.verify,
            "verify_basis": self.verify_basis,
        }
        if self.on_static_findings:
            out["on_static_findings"] = self.on_static_findings
        if self.slower is not None:
            out["slower"] = self.slower
        out["sources"] = self._sources()
        return out

    def _sources(self) -> dict[str, str]:
        held = {
            "share": ("workload_hotness", self.share is not None),
            "lines": ("symbols", self.lines is not None),
            "callers": ("call_graph", self.callers_total is not None),
            "caller_shares": ("run_drivers", bool(self.caller_shares)),
            "owner": ("function_blame", self.owner is not None),
            "last_change": ("function_blame", self.last_change is not None),
            "tests": (self.tests_basis or "", self.tests is not None),
            "next_step": (
                "static_finding" if self.step and self.step.static else "run_inside",
                self.step is not None,
            ),
            "verify": (self.verify_basis or "", self.verify is not None),
            "on_static_findings": ("performance_findings", bool(self.on_static_findings)),
            "slower": ("compare", self.slower is not None),
        }
        return {key: source for key, (source, present) in held.items() if present}


def action_row(
    measured: Any,
    *,
    lines: Sequence[int] | None = None,
    workload_name: str | None = None,
    workload_class: str | None = None,
    next_step: NextStep | None = None,
    callers: Sequence[str] = (),
    callers_total: int | None = None,
    drivers_json: str | None = None,
    tests: RowTests = _UNMAPPED,
    history: Mapping[str, Any] | None = None,
    benchmark: str | None = None,
    compare: Iterable[Any] = (),
    static_causes: Collection[str] = (),
) -> ActionRow:
    """The row for one ranked function.

    *measured* is its ``workload_hotness`` row (``symbol_id``, ``run_id``,
    ``rank``, ``self_share`` and its interval, ``total_share``, ``samples``,
    ``freshness``); *workload_name* and *workload_class* are its run's.
    *next_step* is :func:`row_step` over the run's ``inside`` and ``drivers``.
    *callers* are the function's production callers by the call graph, best
    first, and *drivers_json* the callers its run measured
    (:func:`.payloads.weighted_callers` orders them). *tests* is
    :func:`.function_tests.row_tests`; *history* is
    :func:`.payloads.owner_history_payload` from the function's own blame row
    (``None`` without one: no owner, never the file's). *benchmark* is the
    command rerunning the benchmarks that measure it; :func:`row_verify` picks
    the one verify command. *compare* are the workloads'
    :class:`.compare.Comparison`; only the row's own workload's can mark it
    slower (:func:`row_slower`). *static_causes* the
    open static performance causes on it.
    """
    symbol_id = measured.symbol_id
    listed, shares = weighted_callers(callers, drivers_json, ROW_CALLERS)
    verify, basis = row_verify(benchmark, workload_name, workload_class)
    history = history or {}
    return ActionRow(
        symbol_id=symbol_id,
        run_id=measured.run_id,
        rank=measured.rank,
        share=measured.self_share,
        share_low=measured.share_low,
        share_high=measured.share_high,
        inclusive=measured.total_share,
        samples=measured.samples,
        freshness=measured.freshness,
        lines=list(lines) if lines is not None else None,
        callers_total=callers_total,
        callers=listed,
        caller_shares=shares,
        owner=history.get("owner"),
        last_change=history.get("last_change"),
        tests=tests.tests,
        tests_basis=tests.basis,
        step=next_step,
        verify=verify,
        verify_basis=basis,
        on_static_findings=sorted(static_causes),
        slower=row_slower(symbol_id, compare, workload_name),
    )


# --- the compact forms, read from the JSON form so any host's rows render ------


def _pct(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.0f}%"


def _short(symbol_id: str) -> str:
    """``status_cmd.py::_workspace_rows``: the file's name and the symbol."""
    path = file_of_symbol(symbol_id)
    name = symbol_id[len(path) + 2 :] if symbol_id.startswith(path + "::") else symbol_id
    return f"{posixpath.basename(path)}::{name}" if path else name


def _changed(last: Mapping[str, Any]) -> str:
    """``2026-10-03 (abcdef1, #42)``, or the sha alone when the date is not indexed."""
    ref = ", ".join(x for x in (last["sha"][:7], f"#{last['pr']}" if last.get("pr") else None) if x)
    return f"{last['date']} ({ref})" if last.get("date") else ref


def _slower_words(slower: Mapping[str, Any]) -> str:
    """``1.4x self_seconds in "api" (0.2 to 0.28 s); changed in abcdef1 (#12)``."""
    parts = []
    if slower.get("ratio") is not None:
        unit = f" {slower['unit']}" if slower.get("unit") else ""
        parts.append(
            f'{slower["ratio"]:.2f}x {slower["metric"]} in "{slower["workload"]}" '
            f"({slower['before']:.3g} to {slower['after']:.3g}{unit})"
        )
    else:
        parts.append(f'"{slower["workload"]}" got slower')
    commits = [
        c["sha"][:7] + (f" (#{c['pr']})" if c.get("pr") else "") for c in slower["changed_in"]
    ]
    if commits:
        parts.append("changed in " + ", ".join(commits))
    return "; ".join(parts)


def _tests_words(row: Mapping[str, Any]) -> str:
    tests = row.get("tests")
    ran = row.get("tests_basis") == PER_TEST_MAP
    if tests is None:
        return "tests not mapped"
    if tests:
        return f"{'test that ran it' if ran else 'nearest test by call graph'} {tests[0]}"
    return "no test ran it" if ran else "no test reaches it"


def _facts(row: Mapping[str, Any]) -> str:
    """The measurement and who and what stands behind it, on one line."""
    parts = []
    if row.get("share") is not None:
        interval = ""
        if row.get("share_low") is not None and row.get("share_high") is not None:
            interval = f" ({_pct(row['share_low'])} to {_pct(row['share_high'])})"
        parts.append(f"self {_pct(row['share'])} of the run{interval}")
    if row.get("inclusive") is not None:
        parts.append(f"with callees {_pct(row['inclusive'])}")
    if row.get("samples") is not None:
        parts.append(f"{row['samples']:,} samples")
    callers = row.get("callers_total")
    if callers is not None:
        parts.append(f"{callers} caller{'s' if callers != 1 else ''}")
    if row.get("owner"):
        parts.append(f"owner {row['owner']['name']}")
    if row.get("last_change"):
        parts.append(f"last changed {_changed(row['last_change'])}")
    parts.append(_tests_words(row))
    if row.get("on_static_findings"):
        parts.append(f"static cause {', '.join(row['on_static_findings'])}")
    return " · ".join(parts)


def row_text(row: Mapping[str, Any]) -> list[str]:
    """The compact text form of a JSON-form *row*, plain text, one fact per
    line: the step first (``next``, ``why``), then what drove it, what got
    slower, the measurement with owner and tests, and the verify command."""
    lines: list[str] = []
    step = row.get("next_step")
    if step:
        lines.append(f"next: {step['next']}")
        lines.append(f"why: {step['why']}")
    shares = row.get("caller_shares") or {}
    if shares:
        lines.append("driven by: " + ", ".join(f"{_short(c)} {_pct(s)}" for c, s in shares.items()))
    if row.get("slower"):
        lines.append(f"slower: {_slower_words(row['slower'])}")
    lines.append(_facts(row))
    if row.get("verify"):
        lines.append(f"verify: {row['verify']}")
    return lines


def row_compact(row: Mapping[str, Any]) -> dict[str, Any]:
    """The MCP form of a JSON-form *row*: what to do, what it costs and how to
    verify a fix; unset fields left out, no bookkeeping.

    ``share`` is the cost (self time); ``next`` the step. ``verify`` is on the
    row only when a benchmark measures the function: a workload rerun is the
    same for every row of a run, so it sits on the run once
    (:func:`run_verify`). Why, drivers, tests, owner and last change are the
    JSON form's (REST, ``health --format json``) and ``get_context``'s on the
    function: the default MCP response does not grow to carry them.
    """
    out: dict[str, Any] = {
        "symbol_id": row["symbol_id"],
        "run_id": row["run_id"],
        "share": row.get("share"),
    }
    step = row.get("next_step")
    if step:
        out["next"] = step["next"]
    if row.get("verify") and row.get("verify_basis") == VERIFY_BENCHMARK:
        out["verify"] = row["verify"]
    if row.get("slower"):
        out["slower"] = _slower_words(row["slower"])
    if row.get("on_static_findings"):
        # Open with ``get_health(opportunity_id=...)``.
        out["on_static_findings"] = row["on_static_findings"]
    return out


def run_verify(run: Mapping[str, Any]) -> str | None:
    """The workload rerun a run's rows verify with when no benchmark measures them."""
    return verify_command(run.get("workload_name"), run.get("workload_class"))


__all__ = [
    "ROW_CALLERS",
    "VERIFY_BENCHMARK",
    "VERIFY_WORKLOAD",
    "ActionRow",
    "action_row",
    "row_compact",
    "row_slower",
    "row_step",
    "row_text",
    "row_verify",
    "run_verify",
]
