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

from pydantic import BaseModel, ConfigDict, Field

from repowise.core.analysis.health.coverage.freshness import FreshnessStatus

from ._datetime import UTCDateTime

#: ``measured``: a workload's newest run ranks a function. Otherwise why not:
#: ``no_runtime_data`` (no profile run stored), ``stale`` (every function the
#: runs mapped changed since), ``not_ranked`` (current evidence ranks nothing),
#: ``needs_update`` (the per-workload view predates the store; an update builds it).
EvidenceStatusToken = Literal["measured", "no_runtime_data", "stale", "not_ranked", "needs_update"]

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


class EvidenceRunSummary(_Strict):
    """One stored run: its workload, commit, freshness and where its time went."""

    run_id: str
    #: ``profile``, ``benchmark``, ``test_timing`` or ``query_count``.
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
    #: Fewer than half the entries mapped: stored, not a basis for ranking.
    mapping_partial: bool
    tests_failed: int | None

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


class MeasuredRun(_Strict):
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
    #: Functions measured outside production code: counted, not listed.
    outside_production: int | None
    value_kind: str | None
    #: A deterministic tracer ran: the rank orders and no share is given.
    ordering_only: bool


class MeasuredOwner(_Strict):
    name: str
    commit_pct: float | None


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
    #: Callers outside test code; null when the graph does not know the function.
    callers_total: int | None
    callers: list[str]
    owner: MeasuredOwner | None
    #: Nearest tests; null when the function was never mapped to tests.
    tests: list[str] | None
    #: Open static performance causes on the function, by opportunity id.
    on_static_findings: list[str] = Field(default_factory=list)


class MeasuredGroup(_Strict):
    runs: list[MeasuredRun]
    #: At most ``limit`` rows per run, best rank first.
    rows: list[MeasuredRow]
    #: Ranked rows across the group's runs before the ``limit`` cut.
    total: int


class MeasuredResponse(_Strict):
    """What each workload's newest run ranks, grouped by workload class."""

    #: ``measured`` with rows, ``unknown`` without: never zero.
    basis: Literal["measured", "unknown"]
    status: EvidenceStatusToken
    command: str | None
    #: Runs in a workload class that may lead (benchmark, entry point, load
    #: test, production).
    measured: MeasuredGroup | None
    #: ``unit_test`` runs, listed apart.
    measured_in_tests: MeasuredGroup | None


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


class FileTestCost(_Strict):
    """The summed duration of the timed tests that run one production file."""

    file_path: str
    test_seconds: float
    timed_tests: int
    #: Tests that run the file, timed or not.
    tests: int


class FileTestCosts(_Strict):
    #: ``measured`` from the per-test coverage map, ``inferred`` from the call graph.
    basis: Literal["measured", "inferred"]
    files: list[FileTestCost]
    files_total: int
    #: Non-production files run by timed tests: counted, not listed.
    not_production: int
    #: Production files every timed test reaching production code runs (test setup
    #: imports them): each would show all of their time, so they are counted, not listed.
    every_test: int


class SuiteCostResponse(_Strict):
    """The slowest tests and the test time per production file, from stored JUnit timings."""

    status: Literal["measured", "no_test_timings"]
    #: What writes test timings; null when some are stored.
    command: str | None
    #: The basis runs: each test-timing workload's newest run, as `evidence status` picks it.
    runs: list[EvidenceRunSummary]
    slow_tests: list[SlowTest]
    tests_total: int
    file_costs: FileTestCosts | None
