"""Runtime evidence over the wire: what stored runs measured, on what basis.

Each model mirrors a core payload field for field and is closed, so a key the
core gains fails here instead of vanishing from the API:

- :class:`EvidenceRunSummary` is ``evidence.status.run_status``;
- :class:`MeasuredRun` and :class:`MeasuredRow` are the run and row of
  ``crud.measured_costs``;
- :class:`MeasuredFunction` is one ``symbol_hotness`` row;
- :class:`SuiteCostResponse` is ``evidence.suite_cost.suite_costs``.

No row means no evidence: unknown, never zero. Shares are fractions of the
run's whole value (0-1); intervals come from the core statistics module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from repowise.core.analysis.health.coverage.freshness import FreshnessStatus

from ._datetime import UTCDateTime

#: ``measured``: a workload's run ranks a function. Otherwise why not:
#: ``no_runtime_data`` (no profile run stored), ``ahead_of_index`` (the newest
#: run was added after the indexed commit's; it ranks once the index reaches
#: its commit), ``stale`` (every function the runs mapped changed since),
#: ``not_ranked`` (current evidence ranks nothing), ``needs_update`` (the
#: per-workload view predates the store; an update builds it).
EvidenceStatusToken = Literal[
    "measured", "no_runtime_data", "ahead_of_index", "stale", "not_ranked", "needs_update"
]

#: ``current`` at the indexed commit; ``body_unchanged`` from an older commit
#: whose function body is the same; ``stale`` otherwise (never ranked).
MeasuredFreshness = Literal["current", "body_unchanged", "stale"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceUnits(_Strict):
    """How the run's own entries mapped at ingest; null when the writer did not know."""

    total: int | None
    matched: int | None
    skipped: int | None
    outside_repo: int | None
    truncated: int | None


class ProductionWindow(_Strict):
    """The window a production run covers: ``evidence.status.production_window``."""

    start: UTCDateTime | None
    end: UTCDateTime | None
    #: The oldest commit deployed in the window, when the source knows it.
    base_commit: str | None
    #: What the run's rows claim: ``in production over 2026-10-30 to
    #: 2026-11-26, deployed commit unknown``.
    claim: str


class _OmitAbsentWindow(_Strict):
    """A run model whose ``window`` is sent only on a production run, so other runs do not grow."""

    @model_serializer(mode="wrap")
    def _omit_absent_window(self, handler):  # no return annotation: keeps the schema
        data = handler(self)
        if getattr(self, "window", None) is None:
            data.pop("window", None)
        return data


class EvidenceRunSummary(_OmitAbsentWindow):
    """One stored run: its workload, commit, freshness and where its time went."""

    run_id: str
    #: One of ``evidence.kinds``: ``profile``, ``benchmark``, ``test_timing``,
    #: ``query_count``, ``route_latency`` or ``statement_stats``.
    kind: str
    workload: str
    #: The workload class: ``unit_test``, ``benchmark``, ``entry_point``,
    #: ``load_test`` or ``production``.
    workload_class: str = Field(alias="class")
    environment: str
    value_kind: str | None
    unit: str | None
    tool: str | None
    tool_version: str | None
    commit_sha: str | None
    #: Against the indexed commit: ``current``, ``stale`` or ``unknown``.
    freshness: FreshnessStatus
    dirty: bool | None
    ingested_at: UTCDateTime | None
    units: EvidenceUnits
    #: ``{reason: count}`` for the entries that did not map.
    skipped: dict[str, int] | None
    total_value: float | None
    #: Shares of the run: production repo code itself, waits, libraries.
    #: Null for a run stored without the split.
    repo_self_share: float | None
    wait_share: float | None
    library_share: float | None
    #: The rest: test or tooling code, module bodies, unresolved frames, runtime only.
    unattributed_share: float | None = None
    #: How many samples a profile run holds; null when the profiler counted none.
    total_samples: int | None = None
    #: A profile run with no sample counts: ``pruned_1pct`` from an export that
    #: drops frames under 1% of the run, ``no_samples`` otherwise. Null when
    #: the run counted its samples, and for other kinds.
    low_signal: Literal["pruned_1pct", "no_samples"] | None = None
    #: Fewer than half the entries mapped: stored, not a basis for ranking.
    mapping_partial: bool
    tests_failed: int | None
    #: A production run's window; absent for every other class.
    window: ProductionWindow | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvidenceStatusResponse(_Strict):
    """The newest run of every workload, and whether anything is ranked."""

    status: EvidenceStatusToken
    #: The command that moves an unranked status on; null when none does.
    command: str | None
    #: The indexed commit runs are compared against.
    reference_commit: str | None
    #: Runs kept per evidence kind.
    runs_stored: dict[str, int]
    #: Newest run of each workload (kind, name, environment), newest first.
    workloads: list[EvidenceRunSummary]


class EvidenceRunsResponse(_Strict):
    """Every stored run, newest first (bounded by the store's prune caps)."""

    reference_commit: str | None
    runs: list[EvidenceRunSummary]


class NewerRun(_Strict):
    """A workload's newer run that ranks nothing, listed in place of by an older one."""

    run_id: str
    commit: str | None
    measured_at: UTCDateTime | None
    #: ``ahead_of_index``: added after the indexed commit's run, ranks once
    #: the index reaches its commit. ``not_ranked``: at the indexed commit, too
    #: few samples in production code or no production frame. ``stale``: another
    #: commit whose function bodies changed since.
    reason: Literal["ahead_of_index", "not_ranked", "stale"]


class MeasuredRun(_OmitAbsentWindow):
    """A run's basis: workload, commit, age, samples and where its time went."""

    run_id: str
    workload_class: str
    workload_name: str
    commit: str | None
    measured_at: UTCDateTime | None
    age_days: int | None
    #: Samples in the whole run; null when the profiler counted none.
    samples: int | None
    #: The share of the run spent in production repo code itself: how much of
    #: the run repo code explains.
    repo_self_share: float | None
    wait_share: float | None
    library_share: float | None
    #: The rest: test or tooling code, module bodies, unresolved frames, runtime only.
    unattributed_share: float | None = None
    #: Functions measured outside production code: counted, not listed.
    outside_production: int | None
    value_kind: str | None
    #: A deterministic tracer ran: the rank orders and no share is given.
    ordering_only: bool
    #: The workload's newer run this one is listed in place of (it ranks
    #: nothing); null when this is the workload's newest run.
    newer_run: NewerRun | None = None
    #: A production run's window; absent for every other class.
    window: ProductionWindow | None = None


class MeasuredOwner(_Strict):
    """Who wrote most of the function's current lines, by line blame."""

    name: str
    #: Their share of the function's blamed lines.
    line_pct: float | None


class MeasuredLastChange(_Strict):
    """The newest commit blame credits a current line of the function to."""

    sha: str
    #: Null when the commit is older than the indexed history window.
    date: str | None
    subject: str | None
    #: The PR or MR the commit subject names.
    pr: int | None


class MeasuredRow(_Strict):
    """One ranked production function of one run."""

    symbol_id: str
    file_path: str
    #: ``[start, end]``; null when the symbol is no longer indexed.
    lines: list[int] | None
    run_id: str
    #: 1-based place among the run's production functions by self time.
    rank: int
    #: Self time (the cost the rank is by, waits excluded) as a share of the run.
    share: float | None
    #: The interval of ``share``; null without sample counts.
    share_low: float | None
    share_high: float | None
    #: Inclusive share, context only.
    inclusive: float | None
    #: Self samples on the function; null when not counted.
    samples: int | None
    freshness: MeasuredFreshness
    #: Callers in production code; null when the graph does not know the function.
    callers_total: int | None
    #: At most three production callers: the ones the run measured first, heaviest first.
    callers: list[str]
    #: The share of the function's time each listed caller drove, for the ones
    #: the run measured; empty when the run recorded no callers.
    caller_shares: dict[str, float] = Field(default_factory=dict)
    #: Owner and last change come from the function's own lines; null without
    #: its blame row (never the file's owner).
    owner: MeasuredOwner | None
    last_change: MeasuredLastChange | None
    #: Tests for the function, at most three; null when unknown. Empty: no
    #: test ran it (``per_test_map``) or reaches it (``call_graph``).
    tests: list[str] | None
    #: ``per_test_map``: tests the per-test map saw run its lines;
    #: ``call_graph``: its nearest tests by call graph, where no map has its file.
    tests_basis: Literal["per_test_map", "call_graph"] | None = None
    #: What the time is and the next step (``evidence.next_step``), from what
    #: its run measured inside it; null when the profile does not say. Its
    #: ``verify`` is never set here: the row's own ``verify`` is the one command.
    next_step: MeasuredNextStep | None = None
    #: The one command that verifies a fix, run before and after it: the
    #: benchmarks that measure the function, else a rerun of its workload
    #: (``evidence.action_row.row_verify``); null when neither has one.
    verify: str | None = None
    #: ``benchmark`` or ``workload``: what ``verify`` reruns.
    verify_basis: Literal["benchmark", "workload"] | None = None
    #: Open static performance causes on the function, by opportunity id.
    on_static_findings: list[str] = Field(default_factory=list)
    #: What got slower about the function, when a comparison of its
    #: workloads' runs names it (``evidence.compare``); only where the host
    #: read the comparisons.
    slower: MeasuredRowSlower | None = None
    #: ``import time`` on a module body's row (the time to import its file);
    #: absent on a function's row.
    label: str | None = None
    #: Where each field that holds a value came from: ``workload_hotness``
    #: (``statement_stats`` on a production statement row, ``route_latency``
    #: on a production route row),
    #: ``symbols``, ``call_graph``, ``run_drivers``, ``function_blame``, the
    #: ``tests_basis``, ``run_inside`` or ``static_finding``, the
    #: ``verify_basis``, ``performance_findings``, ``compare``.
    sources: dict[str, str] = Field(default_factory=dict)


class StatementFacts(_Strict):
    """What the statements one function issues did in a production window."""

    calls: int
    total_ms: float
    #: Mean time per call across its statements; null with no calls.
    mean_ms: float | None
    #: Distinct statements the function issues.
    statements: int


class StatementRow(MeasuredRow):
    """One function's production statements in one run, as a measured row.

    ``rank`` is by statement time and ``share`` is of the window's stored
    statement time; there is no interval, inclusive share or samples, and no
    freshness: the statement was joined to the function when it was stored.
    No ``next_step``: no rule reads a statement's cost yet. ``verify`` is the
    next production window."""

    freshness: MeasuredFreshness | None = None
    statements: StatementFacts


class UnattributedStatements(_Strict):
    statements: int
    total_ms: float
    #: Of the window's stored statement time.
    share: float | None


class StatementRun(_Strict):
    """A production statement workload's newest run."""

    run_id: str
    workload_class: str
    workload_name: str
    #: The newest deployed commit in the window; null when unknown.
    commit: str | None
    measured_at: str | None
    age_days: int | None
    window_start: str | None
    window_end: str | None
    #: All statement time and statements the run stored.
    total_ms: float
    statements: int
    #: Statements on no single function, by what the join found:
    #: ``ambiguous`` (several functions could issue it), ``database_schedule``
    #: (only a ``cron.schedule`` job runs it), ``unjoined`` (nothing matched).
    unattributed: dict[str, UnattributedStatements]
    #: The window and deploy in words, as every production run names them.
    window: ProductionWindow


class StatementGroup(_Strict):
    runs: list[StatementRun]
    #: At most ``limit`` rows per run, by statement time.
    rows: list[StatementRow]
    #: Functions across the group's runs before the ``limit`` cut.
    total: int


class RouteFacts(_Strict):
    """What the requests one handler's routes served did in a production window."""

    requests: int
    total_ms: float
    #: Mean time per request across its routes; null with no requests.
    mean_ms: float | None
    #: The slowest 95th percentile among its routes; null when none said.
    p95_ms: float | None
    #: Failed requests across its routes; null when the source did not say.
    errors: int | None
    #: Its routes as ``METHOD template``, costliest first.
    routes: list[str]


class RouteRow(MeasuredRow):
    """One handler's production requests in one run, as a measured row.

    ``rank`` is by request time and ``share`` is of the window's stored
    request time; there is no interval, inclusive share, samples or
    freshness: the route was joined to its handler when it was stored. No
    ``next_step``: no rule reads a route's cost yet. ``verify`` is the next
    production window."""

    freshness: MeasuredFreshness | None = None
    routes: RouteFacts


class UnattributedRoutes(_Strict):
    routes: int
    total_ms: float
    #: Of the window's stored request time.
    share: float | None


class RouteRun(_Strict):
    """A production route workload's newest run."""

    run_id: str
    workload_class: str
    workload_name: str
    #: The newest deployed commit in the window; null when unknown.
    commit: str | None
    measured_at: str | None
    age_days: int | None
    window_start: str | None
    window_end: str | None
    #: All request time, requests and routes the run stored.
    total_ms: float
    requests: int
    routes: int
    #: Routes on no handler, as ``unjoined``: no stored route matched, more
    #: than one did, or the handler is inline (``evidence add`` counts each).
    unattributed: dict[str, UnattributedRoutes]
    #: The window and deploy in words, as every production run names them.
    window: ProductionWindow


class RouteGroup(_Strict):
    runs: list[RouteRun]
    #: At most ``limit`` rows per run, by request time.
    rows: list[RouteRow]
    #: Handlers across the group's runs before the ``limit`` cut.
    total: int


class MeasuredChangedIn(_Strict):
    sha: str
    pr: int | None


class MeasuredRowSlower(_Strict):
    """A slower comparison that names a measured function (``evidence.action_row.row_slower``)."""

    workload: str
    #: The band's newest commit.
    base_commit: str | None
    #: The function's own measure that got worse, when the comparison has one.
    metric: str | None = None
    before: float | None = None
    after: float | None = None
    ratio: float | None = None
    unit: str | None = None
    #: Commits in the range that changed the function.
    changed_in: list[MeasuredChangedIn]


class MeasuredGroup(_Strict):
    runs: list[MeasuredRun]
    #: At most ``limit`` rows per run, best rank first.
    rows: list[MeasuredRow]
    #: Ranked rows across the group's runs before the ``limit`` cut.
    total: int


class BenchmarkRun(_Strict):
    """A workload's newest benchmark run."""

    run_id: str
    workload_class: str
    workload_name: str
    commit: str | None
    measured_at: str | None
    age_days: int | None


class BenchmarkRow(_Strict):
    """One benchmark's result: what it measures and its numbers per operation."""

    run_id: str
    #: The benchmark as the tool names it, from its own name on.
    benchmark: str
    #: The benchmark function; null when it did not resolve to an indexed symbol.
    benchmark_symbol_id: str | None
    #: The production function it measures; null when its name and calls do not say.
    measures: str | None
    #: Time per operation (the median the tool gave), in ``unit``.
    time: float | None
    unit: str | None
    #: Exact where the tool reports them.
    allocs_per_op: float | None
    bytes_per_op: float | None


class BenchmarkGroup(_Strict):
    runs: list[BenchmarkRun]
    #: At most ``limit`` rows per run, those naming a function first.
    rows: list[BenchmarkRow]
    #: Results across the group's runs before the ``limit`` cut.
    total: int


class MeasuredResponse(_Strict):
    """What each workload's newest run that ranks anything ranks, grouped by workload class."""

    #: ``measured`` with rows, ``unknown`` without: never zero.
    basis: Literal["measured", "unknown"]
    status: EvidenceStatusToken
    command: str | None
    #: Runs in a workload class that may lead (entry point, load test,
    #: production).
    measured: MeasuredGroup | None
    #: ``unit_test`` runs, listed apart.
    measured_in_tests: MeasuredGroup | None
    #: Production statement runs: the functions that issue each window's
    #: statements, by statement time. Listed, never leading.
    statements: StatementGroup | None = None
    #: Production route runs: the handlers that serve each window's routes,
    #: by request time. Listed, never leading. Sent only when a run names a
    #: handler; absent otherwise, so every other response is unchanged.
    routes: RouteGroup | None = None
    #: Benchmark results, per benchmark: a benchmark profile never ranks.
    benchmarks: BenchmarkGroup | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_routes(self, handler):  # no return annotation: keeps the schema
        data = handler(self)
        if self.routes is None:
            data.pop("routes", None)
        return data


InsideKind = Literal["own", "repo", "wait", "import", "native", "library"]


class MeasuredLeaf(_Strict):
    """The frame a callee's stacks end in most: what the callee spends its time on."""

    name: str
    kind: InsideKind
    #: Share of the measured function's inclusive time.
    share: float


class MeasuredCallee(_Strict):
    """What part of a measured function's time went to (``evidence.profile_convert``)."""

    #: The callee's name, or ``(own code)`` for the function's own body.
    name: str
    #: ``own``, ``repo``, ``wait``, ``import``, ``native`` or ``library``.
    kind: InsideKind
    #: Share of the function's inclusive time.
    share: float
    #: The callee's heaviest leaf, when its stacks end below it.
    leaf: MeasuredLeaf | None = None


class MeasuredDriver(_Strict):
    """A caller that drove part of a measured function's time."""

    name: str
    #: Share of the function's inclusive time.
    share: float
    #: The caller's symbol id at the evidence commit, when it joined exactly.
    symbol: str | None = None
    #: The caller's repo-relative file, when it is repo code that did not join.
    path: str | None = None


class MeasuredNextStep(_Strict):
    """What a measured function's time is and what to do (``evidence.next_step``)."""

    #: The lexicon entry that named it (``filesystem``, ``import``, ``db_read`` ...).
    rule: str
    #: What the time is, in words; a static finding's text when one agreed.
    why: str
    #: What to do.
    next: str
    #: How to check a change for this workload.
    verify: str | None = None
    #: The dominant callee and the share of the function's time its family holds.
    callee: str
    share: float
    #: The callee's heaviest leaf, when it named the cost.
    leaf: str | None = None
    #: The static finding (biomarker) that wrote ``why``.
    static: str | None = None


class MeasuredFunction(_Strict):
    """What runtime evidence says about one function, from its basis run."""

    symbol_id: str
    file_path: str
    #: ``ranked`` in the top of its run with enough samples, else ``measured``.
    tier: Literal["measured", "ranked"]
    rank: int | None
    self_share: float | None
    total_share: float | None
    share_low: float | None
    share_high: float | None
    samples: int | None
    run_samples: int | None
    #: The basis run, and how many runs measured the function.
    run_id: str | None
    run_count: int | None
    workload_name: str | None
    workload_class: str | None
    value_kind: str | None
    ordering_only: bool | None
    freshness: MeasuredFreshness | None
    evidence_commit_sha: str | None
    measured_at: UTCDateTime | None
    repo_self_share: float | None
    wait_share: float | None
    library_share: float | None
    #: Functions the basis run measured outside production code.
    run_unlisted: int | None
    #: Up to three callees by share; null when the basis run did not record them.
    inside: list[MeasuredCallee] | None = None
    #: Up to three callers by share; null when the basis run did not record them.
    drivers: list[MeasuredDriver] | None = None
    #: What the time is and the next step; null when the profile does not say
    #: (no dominant callee, a wait, the framework spine, a write per request).
    next_step: MeasuredNextStep | None = None


class MeasuredFunctionsResponse(_Strict):
    """Measured functions of the asked symbol or file; empty means unknown."""

    functions: list[MeasuredFunction]


class SlowTest(_Strict):
    """One timed test, at its longest duration across the basis runs."""

    test_id: str
    test_file: str | None
    duration_s: float
    #: ``passed``, ``failed``, ``skipped``, ``error``; null: not reported.
    outcome: str | None
    #: First test its run reports for its class: its time may include shared fixtures.
    first_in_class: bool | None
    workload: str


class CostClusterTest(_Strict):
    test_id: str
    duration_s: float


class CostFixture(_Strict):
    """The fixture most of a cluster's tests ask for and few other timed tests do."""

    name: str
    file_path: str
    line: int | None
    #: The cluster's tests asking for it.
    tests: int
    #: Other timed tests asking for it.
    others: int


class CostGroup(_Strict):
    """A larger group of tests a cluster sits inside: code all of its tests run too."""

    files: list[str]
    files_total: int
    tests: int


class CostCluster(_Strict):
    """Tests that run the same production files and take longer than the rest."""

    #: The production files exactly these tests run, the first few by path.
    files: list[str]
    files_total: int
    tests: int
    #: Mean duration of its tests, and of the other timed tests still ranked.
    mean_s: float
    others_mean_s: float
    #: ``tests x (mean_s - others_mean_s)``: the time over the others' pace.
    excess_s: float
    #: ``excess_s`` as a fraction of the suite (0-1).
    share: float | None
    slowest: list[CostClusterTest]
    fixture: CostFixture | None
    #: The group it was narrowed from, when it carried most of that group's excess.
    within: CostGroup | None


class CostConcentration(_Strict):
    #: The slowest tenth of the timed tests, and the fraction of the suite they take.
    tests: int
    share: float


class SuiteCost(_Strict):
    """Where the suite's time goes (``evidence.suite_cost.cost_fold``)."""

    #: ``small_suite`` (under a minute) and ``small_excess`` (no group 5 s over the
    #: rest) list nothing; ``no_test_map`` gives the concentration only.
    verdict: Literal["small_suite", "no_test_map", "small_excess", "clusters"]
    seconds: float
    timed: int
    concentration: CostConcentration | None
    #: Up to 3, most excess first; empty unless ``verdict`` is ``clusters``.
    clusters: list[CostCluster]
    #: What records a per-test coverage map, for ``no_test_map``.
    command: str | None


class DurationBucket(_Strict):
    """Tests whose duration is at most ``le`` seconds and above the bucket before."""

    #: Upper bound in seconds; null for the open bucket past the last bound.
    le: float | None
    tests: int
    seconds: float


class SuiteFileTime(_Strict):
    """The summed duration of one test file's timed tests."""

    test_file: str
    seconds: float
    tests: int


class SuiteSummary(_Strict):
    """Every test of the basis runs, including those the per-run row cap left out."""

    tests: int
    #: Tests with a duration.
    timed: int
    seconds: float
    histogram: list[DurationBucket]
    #: The test files with the most seconds, most first.
    test_files: list[SuiteFileTime]
    test_files_total: int


class SuiteCostResponse(_Strict):
    """The slowest tests and where the suite's time goes, from stored JUnit timings."""

    status: Literal["measured", "no_test_timings"]
    #: What writes test timings; null when some are stored.
    command: str | None
    #: The basis runs: each test-timing workload's newest run, as `evidence status` picks it.
    runs: list[EvidenceRunSummary]
    slow_tests: list[SlowTest]
    tests_total: int
    #: Null when no timing is stored.
    cost: SuiteCost | None
    #: Every test of the basis runs, folded at ingest before the row cap; null
    #: when a basis run was stored before the fold existed.
    suite: SuiteSummary | None = None
