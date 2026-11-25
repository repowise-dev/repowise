"""What got slower: a workload's newest run against a band of its earlier runs,
and the commits in between that changed what it ran.

Pure: :func:`compare_runs` reads stored runs, their rows and a
:class:`History` the host built, and returns one :class:`Comparison` per
workload (kind, name, environment) that has an earlier run to compare with.
Nothing here reads a store or runs git (``evidence.compare_history`` is the
OSS host; hosted builds a :class:`History` from its own snapshots).

The rules (PLAN S5, S6; probe P3):

- **Runs.** The *current* side is the workload's newest run and every newer
  run at the same commit (several runs of one commit pool their samples). The
  *band* is up to :data:`BAND_RUNS` runs before them at other commits. A run
  that mapped under half its entries never counts.
- **Benchmarks.** Per benchmark, the median of the run medians on each side.
  With :data:`MIN_BAND` band runs or more, a benchmark is slower when the
  ratio is above the band's own spread (its slowest run median over its
  median) and a floor, and no current sample overlaps a band sample (each
  run's min and max). The floor is :data:`PAIR_FLOOR` for one current run and
  :data:`BAND_FLOOR` when two or more runs of the current commit pool their
  samples: one run drifts too far on a busy machine to call 10% from it. With fewer band runs one pair of runs
  cannot tell a 20% change from noise, so the verdict is only ``looks_slower``
  above :data:`PAIR_FLOOR` with no overlap (confirm with ``perf measure``), or
  ``within_noise``. A family (``BenchmarkX/sub``) reports the geometric mean
  of its members' ratios beside a flagged member.
- **Exact metrics** (allocations per op; statements per scope) flag on any
  increase over every band run, whatever the band size: the same code
  allocates the same.
- **Profiles.** CPU self seconds of production functions only, never a wait
  or a wall-clock run, never a share (a benchmark profile's shares stay flat
  when an op gets slower, P3), and only against :data:`MIN_BAND` runs or more.
  Benchmark-class profiles never compare: their benchmarks do.
- **Test timings.** Suite seconds over the tests every compared run has,
  against :data:`MIN_BAND` runs or more, and only for a suite of
  :data:`MIN_SUITE_S` or more. Seam: the slow-test clusters of ``suite_cost``
  join here as further :class:`Slower` items once they land.
- **Cause.** The commits between the band's newest run and the current one
  that changed a function the slower workload reached: the slower benchmarks'
  own functions and their callees, the functions a benchmark profile of the
  same commit range measured, a profile's slower functions and its other
  rows. Function level only (``git_function_blame``), never a file: without
  that history the range and its commit count are all that is said.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .hotness import _age_key

#: Earlier runs a band holds at most, newest first.
BAND_RUNS = 5
#: Band runs below which time is never flagged, only "looks slower".
MIN_BAND = 3
#: With a band: the least ratio flagged, whatever the band's own spread.
BAND_FLOOR = 1.10
#: Without a band (one or two earlier runs): the least ratio that "looks slower" (P3: 0
#: false of 31 on a laptop A/A pair at 1.25).
PAIR_FLOOR = 1.25
#: A function's self seconds below this share of its run are never compared.
MIN_PROFILE_SHARE = 0.01
#: A suite shorter than this never reads as slower (the test-cost silence rule).
MIN_SUITE_S = 60.0
#: Commits named per cause, and functions per commit.
MAX_COMMITS = 5
MAX_FUNCTIONS = 3
#: Benchmark columns that are exact (``perf_check.EXACT_METRICS``): bytes per op is
#: not, a pool or a growing map moves it from run to run.
EXACT_BENCHMARK_METRICS = ("allocs_per_op",)

Verdict = Literal["slower", "looks_slower", "within_noise"]

_SECONDS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}


@dataclass(frozen=True)
class ChangedCommit:
    """A commit inside the range that changed a function the workload reached."""

    sha: str
    subject: str | None
    pr: int | None
    #: The reached functions it changed (symbol ids), the slower ones first.
    functions: tuple[str, ...]


@dataclass(frozen=True)
class Cause:
    """The commit range a slowdown appeared in, and what changed inside it."""

    base_commit: str
    head_commit: str
    #: Commits in the range; ``None`` when the host could not list them.
    commits_in_range: int | None
    commits: tuple[ChangedCommit, ...] = ()


@dataclass(frozen=True)
class Slower:
    """One measure that got worse."""

    #: ``time`` (a benchmark), ``allocs_per_op``, ``queries``, ``self_seconds``,
    #: ``suite_seconds``.
    metric: str
    #: The benchmark key, the function's symbol id, the query scope, or ``suite``.
    name: str
    before: float
    after: float
    ratio: float | None
    unit: str | None = None
    #: The function measured (a benchmark's own function, a profile row, a query site).
    symbol_id: str | None = None
    #: A benchmark family's geometric mean ratio, beside its flagged members.
    family_geomean: float | None = None


@dataclass(frozen=True)
class Comparison:
    """A workload's newest run against its band (:func:`compare_runs`)."""

    kind: str
    workload: str
    environment: str
    verdict: Verdict
    band_runs: int
    #: The newest run, the one the comparison is about.
    head_run_id: str
    current_runs: int
    base_commit: str | None
    head_commit: str | None
    #: Worst first; empty for ``within_noise``.
    slower: tuple[Slower, ...] = ()
    cause: Cause | None = None


@dataclass(frozen=True)
class History:
    """What the host knows about the commits between two runs.

    ``ranges`` maps ``(base, head)`` to the range's commits ``(sha, subject)``,
    newest first; a pair the host could not list is absent. ``touched`` maps a
    commit to the functions (symbol ids) it changed, from function-level
    history. ``callees`` maps a function to the functions it reaches (the host
    bounds the walk).
    """

    ranges: Mapping[tuple[str, str], Sequence[tuple[str, str | None]]] = field(default_factory=dict)
    touched: Mapping[str, Collection[str]] = field(default_factory=dict)
    callees: Mapping[str, Collection[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Sides:
    current: tuple[Any, ...]
    band: tuple[Any, ...]


def _usable(run: Any) -> bool:
    return not getattr(run, "mapping_partial", False)


def _sides(runs: Sequence[Any]) -> _Sides | None:
    """*runs* of one workload split into the current side and the band, or ``None``
    without an earlier run at another commit."""
    ordered = sorted(runs, key=_age_key, reverse=True)
    # A partially mapped newest run is no basis: it is never compared, and an older
    # run never speaks in its place.
    if not ordered or not _usable(ordered[0]):
        return None
    ordered = [r for r in ordered if _usable(r)]
    newest = ordered[0]
    current = [newest]
    rest = ordered[1:]
    if newest.commit_sha:
        while rest and rest[0].commit_sha == newest.commit_sha:
            current.append(rest.pop(0))
    # A run at the current commit older than a band run measured the same code; it
    # belongs to neither side, so the band stays the code before the change.
    band = [r for r in rest if not newest.commit_sha or r.commit_sha != newest.commit_sha]
    if not band:
        return None
    return _Sides(tuple(current[:BAND_RUNS]), tuple(band[:BAND_RUNS]))


def _series(runs: Iterable[Any]) -> dict[tuple[str, str, str], list[Any]]:
    out: dict[tuple[str, str, str], list[Any]] = {}
    for run in runs:
        out.setdefault((run.kind, run.workload_name, run.environment), []).append(run)
    return out


def comparison_pairs(runs: Iterable[Any]) -> list[tuple[str, str]]:
    """The ``(base, head)`` commit pairs :func:`compare_runs` will ask *history*
    about, so a host lists only those ranges."""
    pairs: list[tuple[str, str]] = []
    for series in _series(runs).values():
        sides = _sides(series)
        if sides is None:
            continue
        pair = (sides.band[0].commit_sha, sides.current[0].commit_sha)
        if pair[0] and pair[1] and pair[0] != pair[1] and pair not in pairs:
            pairs.append(pair)
    return pairs


def runs_to_read(runs: Iterable[Any]) -> list[str]:
    """The ids of the runs whose rows :func:`compare_runs` reads."""
    runs = list(runs)
    ids: list[str] = []
    for series in _series(runs).values():
        sides = _sides(series)
        if sides is not None:
            ids.extend(r.id for r in (*sides.current, *sides.band))
    ids.extend(r.id for r in _bench_profiles(runs))
    return list(dict.fromkeys(ids))


def _bench_profiles(runs: Iterable[Any]) -> list[Any]:
    """The newest benchmark-class profile run of each workload: what the benchmarks reach."""
    newest: dict[tuple[str, str], Any] = {}
    for run in runs:
        if run.kind != "profile" or run.workload_class != "benchmark" or not _usable(run):
            continue
        key = (run.workload_name, run.environment)
        if key not in newest or _age_key(run) > _age_key(newest[key]):
            newest[key] = run
    return list(newest.values())


def compare_runs(
    runs: Iterable[Any],
    rows: Mapping[str, Sequence[Any]],
    history: History | None = None,
    origins: Mapping[str, str] | None = None,
) -> list[Comparison]:
    """Each workload's newest run against its band, worst verdict first.

    *runs* are stored runs (``evidence_runs`` rows or alike: ``id``, ``kind``,
    ``workload_name``, ``workload_class``, ``environment``, ``commit_sha``,
    ``measured_at``/``ingested_at``, ``value_kind``, ``unit``, ``total_value``,
    ``mapping_partial``); *rows* maps a run id to its rows (the kind's table
    columns), at least for :func:`runs_to_read`. *origins* maps a file to its
    ``code_origin``: a profile compares only production functions, so without it
    no profile is compared. A workload with no earlier run at another commit has
    no comparison.
    """
    runs = list(runs)
    reach_profiles = [rows.get(r.id, ()) for r in _bench_profiles(runs)]
    out: list[Comparison] = []
    for series in _series(runs).values():
        sides = _sides(series)
        if sides is None:
            continue
        kind = sides.current[0].kind
        if kind == "benchmark":
            found = _compare_benchmarks(sides, rows)
        elif kind == "profile":
            found = _compare_profile(sides, rows, origins) if origins is not None else None
        elif kind == "test_timing":
            found = _compare_suite(sides, rows)
        elif kind == "query_count":
            found = _compare_queries(sides, rows)
        else:
            found = None
        if found is None:
            continue
        verdict, slower = found
        comparison = _comparison(sides, verdict, slower)
        if verdict == "slower" and history is not None:
            reached = _reached(kind, sides, rows, slower, reach_profiles, history)
            comparison = _with_cause(comparison, reached, history)
        out.append(comparison)
    order = {"slower": 0, "looks_slower": 1, "within_noise": 2}
    return sorted(out, key=lambda c: (order[c.verdict], c.kind, c.workload, c.environment))


def _comparison(sides: _Sides, verdict: Verdict, slower: list[Slower]) -> Comparison:
    head = sides.current[0]
    return Comparison(
        kind=head.kind,
        workload=head.workload_name,
        environment=head.environment,
        verdict=verdict,
        band_runs=len(sides.band),
        head_run_id=head.id,
        current_runs=len(sides.current),
        base_commit=sides.band[0].commit_sha,
        head_commit=head.commit_sha,
        slower=tuple(slower) if verdict != "within_noise" else (),
    )


def _by_key(
    runs: Sequence[Any], rows: Mapping[str, Sequence[Any]], key: str
) -> list[dict[str, Any]]:
    return [{getattr(r, key): r for r in rows.get(run.id, ())} for run in runs]


def _pick(row: Any, attr: str) -> float | None:
    value = getattr(row, attr, None)
    return float(value) if value is not None else None


def _compare_benchmarks(
    sides: _Sides, rows: Mapping[str, Sequence[Any]]
) -> tuple[Verdict, list[Slower]] | None:
    current = _by_key(sides.current, rows, "benchmark_key")
    band = _by_key(sides.band, rows, "benchmark_key")
    flagged: list[Slower] = []
    hints: list[Slower] = []
    exact: list[Slower] = []
    ratios: dict[str, float] = {}
    for key, head in current[0].items():
        now = [r[key] for r in current if key in r]
        # A band run in another unit or direction measured something else.
        before = [
            r[key]
            for r in band
            if key in r and (r[key].unit, _lower_better(r[key])) == (head.unit, _lower_better(head))
        ]
        if not before:
            continue
        banded = len(before) >= MIN_BAND
        for metric in EXACT_BENCHMARK_METRICS:
            after_v = _pick(head, metric)
            band_v = [v for r in before if (v := _pick(r, metric)) is not None]
            if after_v is not None and band_v and after_v > max(band_v):
                exact.append(Slower(metric, key, max(band_v), after_v, None, None, head.symbol_id))
        ratio = _time_ratio(now, before)
        if ratio is None:
            continue
        ratios[key] = ratio
        floor = max(_floor(now), _spread(before, head)) if banded else PAIR_FLOOR
        if ratio > floor and _apart(now, before, head):
            (flagged if banded else hints).append(
                Slower(
                    "time",
                    key,
                    _median(before, "median"),
                    _median(now, "median"),
                    ratio,
                    head.unit,
                    head.symbol_id,
                )
            )
    flagged = sorted(_with_families(flagged, ratios), key=lambda s: -(s.ratio or 0.0))
    hints = sorted(_with_families(hints, ratios), key=lambda s: -(s.ratio or 0.0))
    # Without a band time is only a hint to confirm; exact values need none.
    if exact or flagged:
        return "slower", exact + flagged
    if hints:
        return "looks_slower", hints
    return "within_noise", []


def _floor(now: Sequence[Any]) -> float:
    """The least ratio a banded benchmark flags at: :data:`BAND_FLOOR` when two or more
    current runs at one commit pool their samples, :data:`PAIR_FLOOR` for one run
    (on a laptop one run of unchanged code drifted 1.45x with no overlap, P3 rerun)."""
    return BAND_FLOOR if len(now) >= 2 else PAIR_FLOOR


def _median(rows: Sequence[Any], attr: str) -> float:
    return statistics.median(v for r in rows if (v := _pick(r, attr)) is not None)


def _time_ratio(now: Sequence[Any], before: Sequence[Any]) -> float | None:
    """How many times worse the current median is than the band's."""
    if not all(_pick(r, "median") is not None for r in (*now, *before)):
        return None
    after_m, before_m = _median(now, "median"), _median(before, "median")
    if after_m <= 0 or before_m <= 0:
        return None
    return after_m / before_m if _lower_better(now[0]) else before_m / after_m


def _lower_better(row: Any) -> bool:
    return getattr(row, "lower_is_better", True) is not False


def _spread(before: Sequence[Any], head: Any) -> float:
    """The band's own spread: its worst run median over its median."""
    medians = [float(r.median) for r in before]
    mid = statistics.median(medians)
    if _lower_better(head):
        return max(medians) / mid
    return mid / min(medians) if min(medians) > 0 else math.inf


def _apart(now: Sequence[Any], before: Sequence[Any], head: Any) -> bool:
    """No current sample overlaps a band sample (each run's min and max; its
    median where the tool gave no range)."""

    def low(r: Any) -> float:
        return _pick(r, "min") if _pick(r, "min") is not None else float(r.median)

    def high(r: Any) -> float:
        return _pick(r, "max") if _pick(r, "max") is not None else float(r.median)

    if _lower_better(head):
        return min(low(r) for r in now) > max(high(r) for r in before)
    return max(high(r) for r in now) < min(low(r) for r in before)


def benchmark_name(key: str) -> str:
    """A benchmark key without its Go package path: ``BenchmarkX/sub``."""
    at = key.find(".Benchmark")
    return key[at + 1 :] if at >= 0 else key


def _family(key: str) -> str | None:
    """The family a sub-benchmark belongs to (``BenchmarkX`` of ``BenchmarkX/sub``,
    ``test_x`` of ``test_x[param]``); ``None`` for a benchmark with no parameters."""
    name = benchmark_name(key)
    for sep in ("/", "["):
        if sep in name:
            return key[: len(key) - len(name)] + name.split(sep, 1)[0]
    return None


def _with_families(flagged: list[Slower], ratios: Mapping[str, float]) -> list[Slower]:
    families: dict[str, list[float]] = {}
    for key, ratio in ratios.items():
        family = _family(key)
        if family is not None:
            families.setdefault(family, []).append(ratio)
    out = []
    for item in flagged:
        family = _family(item.name)
        members = families.get(family) if family is not None else None
        if members and len(members) > 1:
            geomean = math.exp(sum(math.log(r) for r in members) / len(members))
            item = Slower(**{**item.__dict__, "family_geomean": round(geomean, 3)})
        out.append(item)
    return out


def _compare_profile(
    sides: _Sides, rows: Mapping[str, Sequence[Any]], origins: Mapping[str, str]
) -> tuple[Verdict, list[Slower]] | None:
    from repowise.core.ingestion.symbol_identity import file_of_symbol

    head = sides.current[0]
    per = _SECONDS.get(head.unit or "")
    if (
        head.workload_class == "benchmark"
        or head.value_kind != "cpu"
        or per is None
        or len(sides.band) < MIN_BAND
        or any(
            (r.unit, r.value_kind, r.workload_class) != (head.unit, "cpu", head.workload_class)
            for r in sides.band
        )
    ):
        return None
    current = _by_key(sides.current, rows, "symbol_id")
    band = _by_key(sides.band, rows, "symbol_id")
    total = statistics.median(float(r.total_value or 0.0) for r in sides.current)
    flagged: list[Slower] = []
    for symbol_id in current[0]:
        if origins.get(file_of_symbol(symbol_id)) != "production":
            continue
        now = [v for r in current if (v := _pick(r.get(symbol_id), "self_value")) is not None]
        before = [_pick(r.get(symbol_id), "self_value") for r in band]
        # No row in a band run is unknown there, never zero: such a function is not compared.
        if not now or any(v is None or v <= 0 for v in before):
            continue
        after_s = statistics.median(now)
        if total <= 0 or after_s / total < MIN_PROFILE_SHARE:
            continue
        mid = statistics.median(before)  # type: ignore[type-var]
        ratio = after_s / mid
        if ratio > max(PAIR_FLOOR, max(before) / mid) and min(now) > max(before):  # type: ignore[type-var]
            flagged.append(
                Slower("self_seconds", symbol_id, mid * per, after_s * per, ratio, "s", symbol_id)
            )
    if not flagged:
        return "within_noise", []
    flagged.sort(key=lambda s: -(s.after - s.before))
    return "slower", flagged


def _compare_suite(
    sides: _Sides, rows: Mapping[str, Sequence[Any]]
) -> tuple[Verdict, list[Slower]] | None:
    if len(sides.band) < MIN_BAND:
        return None
    runs = [*sides.current, *sides.band]
    timed = [
        {r.test_id: float(r.duration_s) for r in rows.get(run.id, ()) if r.duration_s is not None}
        for run in runs
    ]
    common = set(timed[0]).intersection(*timed[1:])
    if not common:
        return None
    totals = [sum(t[test] for test in common) for t in timed]
    now, before = totals[: len(sides.current)], totals[len(sides.current) :]
    after_s, mid = statistics.median(now), statistics.median(before)
    if after_s < MIN_SUITE_S or mid <= 0:
        return "within_noise", []
    ratio = after_s / mid
    if ratio > max(PAIR_FLOOR, max(before) / mid) and min(now) > max(before):
        return "slower", [Slower("suite_seconds", f"{len(common)} tests", mid, after_s, ratio, "s")]
    return "within_noise", []


def _compare_queries(
    sides: _Sides, rows: Mapping[str, Sequence[Any]]
) -> tuple[Verdict, list[Slower]] | None:
    """Statements per scope and site: exact, so any growth over every band run flags."""

    def counts(run: Any) -> dict[tuple[str, str, str], float]:
        out: dict[tuple[str, str, str], float] = {}
        for r in rows.get(run.id, ()):
            key = (r.scope_kind, r.scope_id, r.site_symbol_id)
            out[key] = out.get(key, 0.0) + float(r.query_count)
        return out

    head = counts(sides.current[0])
    band = [counts(run) for run in sides.band]
    flagged = []
    for key, after_v in head.items():
        before = [b[key] for b in band if key in b]
        if before and after_v > max(before):
            flagged.append(
                Slower(
                    "queries",
                    f"{key[0]} {key[1]} at {key[2]}",
                    max(before),
                    after_v,
                    None,
                    None,
                    key[2],
                )
            )
    if not flagged:
        return "within_noise", []
    flagged.sort(key=lambda s: -(s.after - s.before))
    return "slower", flagged


def _reached(
    kind: str,
    sides: _Sides,
    rows: Mapping[str, Sequence[Any]],
    slower: Sequence[Slower],
    reach_profiles: Sequence[Sequence[Any]],
    history: History,
) -> list[str]:
    """The functions the slower workload ran, most direct first."""
    first = [s.symbol_id for s in slower if s.symbol_id]
    if kind == "benchmark":
        return reached_functions(first, history, reach_profiles)
    if kind == "profile":
        return reached_functions(first, history, [rows.get(sides.current[0].id, ())])
    return reached_functions(first, history, ())


def reached_functions(
    first: Sequence[str], history: History, measured: Iterable[Iterable[Any]]
) -> list[str]:
    """*first* (the slower functions or benchmarks), then their callees, then the
    functions of *measured* profile rows by self value: what a slower workload ran,
    most direct first."""
    then: list[str] = []
    for symbol_id in first:
        then.extend(sorted(history.callees.get(symbol_id, ())))
    for profile in measured:
        ranked = sorted(profile, key=lambda r: -(_pick(r, "self_value") or 0.0))
        then.extend(r.symbol_id for r in ranked)
    return list(dict.fromkeys([*first, *then]))


def benchmark_profiles(runs: Iterable[Any]) -> list[Any]:
    """The newest benchmark-class profile run of each workload: what benchmarks reach."""
    return _bench_profiles(runs)


def _with_cause(comparison: Comparison, reached: Sequence[str], history: History) -> Comparison:
    cause = cause_for(comparison.base_commit, comparison.head_commit, reached, history)
    if cause is None:
        return comparison
    return Comparison(**{**comparison.__dict__, "cause": cause})


def cause_for(
    base: str | None, head: str | None, reached: Sequence[str], history: History
) -> Cause | None:
    """The commits between *base* and *head* that changed a function of *reached*
    (most direct first); ``None`` when the two runs share a commit or either is unknown."""
    if not base or not head or base == head:
        return None
    listed = history.ranges.get((base, head))
    commits: list[ChangedCommit] = []
    if listed is not None and reached:
        rank = {symbol_id: i for i, symbol_id in enumerate(reached)}
        for sha, subject in listed:
            hit = sorted(
                (s for s in history.touched.get(sha, ()) if s in rank), key=rank.__getitem__
            )
            if hit:
                commits.append(
                    ChangedCommit(sha, subject, _pr(subject), tuple(hit[:MAX_FUNCTIONS]))
                )
    return Cause(
        base, head, len(listed) if listed is not None else None, tuple(commits[:MAX_COMMITS])
    )


def _pr(subject: str | None) -> int | None:
    if not subject:
        return None
    from repowise.core.forges import change_number

    return change_number(subject)


# --- words -------------------------------------------------------------------


def _short_name(symbol_id: str) -> str:
    from repowise.core.ingestion.symbol_identity import file_of_symbol

    path = file_of_symbol(symbol_id)
    name = symbol_id[len(path) + 2 :] if symbol_id.startswith(path + "::") else symbol_id
    return f"{path.rsplit('/', 1)[-1]}::{name}"


def _amount(value: float, unit: str | None) -> str:
    text = f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.3g}"
    return f"{text} {unit}" if unit else text


def _item_words(item: Slower) -> str:
    if item.metric == "self_seconds":
        name = _short_name(item.name)
    elif item.metric in ("time", "allocs_per_op"):
        name = benchmark_name(item.name)
    else:
        name = item.name
    if item.metric == "allocs_per_op":
        return f"{name} {item.before:g} to {item.after:g} allocs/op"
    if item.metric == "queries":
        return f"{name} {item.before:g} to {item.after:g} statements"
    per_op = item.metric == "time" and item.unit in _SECONDS
    unit = f"{item.unit}/op" if per_op else item.unit
    text = f"{name} {item.ratio:.2f}x ({_amount(item.before, unit)} to {_amount(item.after, unit)})"
    if item.family_geomean is not None:
        text += f", family {item.family_geomean:.2f}x"
    return text


_KIND_WORDS = {
    "benchmark": "benchmark",
    "profile": "function",
    "test_timing": "suite",
    "query_count": "query scope",
}


def comparison_line(comparison: Comparison) -> str:
    """One line in words, what to look at first; plain text, no markup."""
    c = comparison
    band = f"{c.band_runs} earlier run" + ("s" if c.band_runs != 1 else "")
    if c.verdict == "within_noise":
        return f'"{c.workload}": within noise of its {band}.'
    worst = _item_words(c.slower[0])
    more = len({s.name for s in c.slower[1:]} - {c.slower[0].name})
    what = _KIND_WORDS.get(c.kind, c.kind)
    named = len({s.name for s in c.slower})
    count = f"{named} {what}" + ("s" if named != 1 else "")
    if c.verdict == "looks_slower":
        return (
            f'"{c.workload}" looks slower than its {band} ({worst}'
            + (f", {more} more" if more else "")
            + "); one pair of runs is within laptop noise, confirm with `repowise perf measure`."
        )
    text = f'"{c.workload}": {count} slower than the band of {band} ({worst}' + (
        f", {more} more)" if more else ")"
    )
    if c.cause is not None:
        text += "; " + cause_words(c.cause)
    return text + "."


def cause_words(cause: Cause) -> str:
    """``changed in between: tree.go::node::getValue in 1a2b3c4 "subject" (#12)``."""
    span = f"{cause.base_commit[:7]}..{cause.head_commit[:7]}"
    if not cause.commits:
        counted = (
            f", {cause.commits_in_range} commit" + ("s" if cause.commits_in_range != 1 else "")
            if cause.commits_in_range is not None
            else ""
        )
        return f"since {span}{counted}; no commit in it changed a function it ran"
    parts = []
    for commit in cause.commits:
        names = ", ".join(_short_name(s) for s in commit.functions)
        subject = f' "{commit.subject}"' if commit.subject else ""
        pr = (
            f" (#{commit.pr})"
            if commit.pr and f"#{commit.pr}" not in (commit.subject or "")
            else ""
        )
        parts.append(f"{names} in {commit.sha[:7]}{subject}{pr}")
    return f"changed in {span}: " + "; ".join(parts)


def comparison_payload(comparison: Comparison) -> dict[str, Any]:
    """The JSON form: every field traceable to its runs and rows."""
    c = comparison
    out: dict[str, Any] = {
        "kind": c.kind,
        "workload": c.workload,
        "environment": c.environment,
        "verdict": c.verdict,
        "band_runs": c.band_runs,
        "current_runs": c.current_runs,
        "base_commit": c.base_commit,
        "head_commit": c.head_commit,
        "slower": [{k: v for k, v in s.__dict__.items() if v is not None} for s in c.slower],
        "line": comparison_line(c),
    }
    if c.cause is not None:
        out["cause"] = {
            "base_commit": c.cause.base_commit,
            "head_commit": c.cause.head_commit,
            "commits_in_range": c.cause.commits_in_range,
            "commits": [
                {
                    "sha": m.sha,
                    "subject": m.subject,
                    "pr": m.pr,
                    "functions": list(m.functions),
                }
                for m in c.cause.commits
            ],
        }
    return out


__all__ = [
    "BAND_FLOOR",
    "BAND_RUNS",
    "MIN_BAND",
    "PAIR_FLOOR",
    "Cause",
    "ChangedCommit",
    "Comparison",
    "History",
    "Slower",
    "benchmark_profiles",
    "cause_for",
    "cause_words",
    "compare_runs",
    "comparison_line",
    "comparison_pairs",
    "comparison_payload",
    "reached_functions",
    "runs_to_read",
]
