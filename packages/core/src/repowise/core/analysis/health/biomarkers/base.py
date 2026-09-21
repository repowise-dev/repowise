"""Biomarker contract: Protocol + ``BiomarkerResult`` + ``FileContext``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ....ingestion.git_indexer.function_blame import BlameIndex
from ...graph_view import HasEdge
from ..complexity import ClassComplexity, ErrorHandlingHit, FunctionComplexity, PerfHit
from ..duplication import ClonePair
from ..models import Severity


@dataclass
class FileContext:
    """All inputs a biomarker may need to evaluate a file.

    Populated by ``engine.py`` once per file. Biomarkers are pure
    functions over this context — they don't open files or talk to the
    DB themselves.
    """

    file_path: str
    language: str
    nloc: int
    has_test_file: bool
    module: str | None
    # Every walked function in this file, in document order. Not keyed by
    # name: a name key drops all but one of a file's anonymous ``it``
    # callbacks, and every same-named method on a second class beside it.
    # This replaced the name-keyed ``function_metrics`` map every marker used
    # to read. Empty for SQL, whose routine metrics are text-counted and
    # defect-uncalibrated.
    all_functions: tuple[FunctionComplexity, ...] = ()
    # Per-class aggregate metrics (LCOM4, method count, size). Empty for
    # languages whose walker map doesn't opt into class-level analysis
    # (see ``complexity.languages``). Consumed by ``low_cohesion`` /
    # ``god_class``.
    class_metrics: list[ClassComplexity] = field(default_factory=list)
    # Per-file git metadata (may be empty when git indexing skipped).
    git_meta: dict[str, Any] = field(default_factory=dict)
    # Graph-derived signals.
    # True when a test file can execute into this one along the call graph,
    # within ``test_reachability.DEFAULT_CALL_DEPTH`` hops. Distinct from
    # ``has_test_file``, which is a filename convention: this is a recorded
    # edge, so it finds behaviour-named tests the convention cannot, and it
    # over-claims, since control reaching a file is not a run exercising it.
    # Sound as a floor ("something tests this"), never as a coverage quantity.
    # ``False`` when no graph was available - the documented "no signal"
    # outcome.
    reached_by_tests: bool = False
    dependents_count: int = 0
    # Repo-wide 80th percentile of file-level in-degree (dependents),
    # computed by the engine across files that have ≥1 dependent. ``None``
    # when no graph is available. ``brain_method`` uses it as a
    # language-agnostic centrality floor so its gate adapts to
    # sparse-graph languages instead of assuming Python's import density.
    repo_dependents_p80: int | None = None
    pagerank_score: float = 0.0
    # Coverage signals (populated when --coverage was ingested). When no
    # coverage is available these stay ``None`` and coverage-aware
    # biomarkers return no findings.
    line_coverage_pct: float | None = None
    branch_coverage_pct: float | None = None
    covered_lines: set[int] = field(default_factory=set)
    total_coverable_lines: int = 0
    # True when a coverage report named this file, whatever it said about it.
    # Needed because ``line_coverage_pct is None`` has two causes that call for
    # opposite verdicts: no report was ingested (unknown — fall back to the
    # test-file signals), or a report measured the file and found nothing
    # coverable (known — there is nothing here to test, so say nothing). With
    # ``total_coverable_lines`` at 0 in both cases, this flag is what separates
    # them (issue #2193).
    coverage_measured: bool = False
    # Duplication signals (populated when the engine ran the
    # duplication detector for this analyze() call). ``clones`` is the
    # list of clone pairs this file participates in; ``duplication_pct``
    # is the percent of NLOC covered by clones.
    clones: list[ClonePair] = field(default_factory=list)
    duplication_pct: float | None = None
    # Thin graph view exposing only ``has_edge``. ``None`` on test fixtures
    # that never construct a graph.
    graph_view: HasEdge | None = None
    # Per-line blame index produced by the FULL git tier (see
    # ``ingestion.git_indexer.function_blame``). ``None`` on ESSENTIAL
    # tier until the FULL-tier backfill (``backfill_full_tier()``) runs;
    # function-level biomarkers
    # must treat ``None`` (and an empty index) as the documented
    # "no signal" outcome and emit zero findings.
    blame_index: BlameIndex | None = None
    # Repo-wide p80 of per-function modification counts, computed by the
    # engine across every function in the analyze() call. ``None`` when
    # blame is unavailable or no functions exist. ``function_hotspot``
    # uses this as the churn threshold for its top-quintile gate.
    repo_function_mod_p80: int | None = None
    # Distinct non-bot contributors active in the repo's trailing 90-day
    # window, computed once by the engine from ``top_authors_json``
    # timestamps. ``None`` = unknown (git skipped, or the index predates
    # per-author timestamps) — ownership biomarkers must treat ``None``
    # as "no team-size signal" and keep their historical behaviour. On
    # small teams (≤ SMALL_TEAM_MAX_CONTRIBUTORS) concentration-only
    # ownership findings are downgraded to informational severity unless
    # corroborated (issue #361).
    repo_active_contributors_90d: int | None = None
    # Error-handling anti-pattern occurrences (swallowed catch, bare
    # except, unsafe unwrap, discarded Go error) collected by the
    # complexity walker's whole-tree pass. Empty when the language is
    # unsupported or parsing failed — the documented "no signal" outcome.
    # Consumed by the ``error_handling`` biomarker.
    error_handling_hits: list[ErrorHandlingHit] = field(default_factory=list)
    # Performance-risk occurrences (io-in-loop, string-concat-in-loop,
    # blocking-sync-in-async) collected by the complexity walker's whole-tree
    # perf pass. Empty when the language opts out of the perf pass or parsing
    # failed — the documented "no signal" outcome. Consumed by the perf
    # biomarkers (``io_in_loop`` / ``string_concat_in_loop`` /
    # ``blocking_sync_in_async``).
    perf_hits: list[PerfHit] = field(default_factory=list)
    # Names imported from an I/O-typed library in this file (the import
    # bridge). Empty when none / language opts out. Carried for PR4's
    # cross-function reachability; the same-function perf biomarkers read the
    # already-resolved ``perf_hits`` instead.
    io_boundary_names: set[str] = field(default_factory=set)
    # Start lines of the test cases in this file that hand their oracle to an
    # asserting function in ANOTHER file, resolved on a call edge by
    # ``asserts.oracle_reach``. Lines rather than names: a name would collapse
    # a file's same-named functions, which is the collision #2408 removed.
    # Empty without a call graph, which leaves the marker as it was.
    cross_file_oracle_lines: frozenset[int] = frozenset()


# A repo whose trailing-90-day window has at most this many active human
# contributors is a "small team": concentrated ownership there is the normal
# operating model, not a silo warning. Used by ``ownership_risk`` /
# ``knowledge_loss`` (and the server's risk classifier) to cap severity of
# concentration-only findings at LOW unless corroborated by a hotspot signal.
SMALL_TEAM_MAX_CONTRIBUTORS: int = 3


@dataclass
class BiomarkerResult:
    """One biomarker hit before scoring deductions are applied."""

    biomarker_type: str
    severity: Severity
    function_name: str | None
    line_start: int | None
    line_end: int | None
    details: dict[str, Any]
    reason: str = ""
    # Optional continuous deduction override (health points, pre-weight,
    # pre-category-cap). When set, the scorer uses this magnitude instead of
    # the discrete ``severity`` → deduction table — letting a biomarker carry a
    # signal whose strength varies continuously per finding (e.g. a coverage
    # deduction that scales with the uncovered fraction). ``severity`` is still
    # carried for display/filtering. Stays fully per-finding attributable, so
    # the linear ``health_impact`` contract holds.
    deduction: float | None = None


class Biomarker(Protocol):
    """Detector contract. Each concrete biomarker is a stateless object."""

    name: str
    category: str  # one of the scoring categories in ``scoring.CATEGORY_CAPS``.

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]: ...
