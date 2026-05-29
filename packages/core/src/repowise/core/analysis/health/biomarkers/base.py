"""Biomarker contract: Protocol + ``BiomarkerResult`` + ``FileContext``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ....ingestion.git_indexer.function_blame import BlameIndex
from ..complexity import ClassComplexity, FunctionComplexity
from ..duplication import ClonePair
from ..models import Severity


class HasEdge(Protocol):
    """Minimal graph view for biomarkers that need to ask "is there an
    edge between these two files?" without depending on NetworkX in
    tests. ``engine.py`` wraps the real ``DiGraph`` in an adapter that
    implements this protocol.
    """

    def has_edge(self, src: str, dst: str, key: str = "imports") -> bool: ...


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
    # Map symbol-name → complexity metrics for functions/methods in this
    # file. Symbols without a complexity row default to CCN=1, nesting=0.
    function_metrics: dict[str, FunctionComplexity] = field(default_factory=dict)
    # Per-class aggregate metrics (LCOM4, method count, size). Empty for
    # languages whose walker map doesn't opt into class-level analysis
    # (see ``complexity.languages``). Consumed by ``low_cohesion`` /
    # ``god_class``.
    class_metrics: list[ClassComplexity] = field(default_factory=list)
    # Per-file git metadata (may be empty when git indexing skipped).
    git_meta: dict[str, Any] = field(default_factory=dict)
    # Graph-derived signals.
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
    # Duplication signals (populated when the engine ran the
    # duplication detector for this analyze() call). ``clones`` is the
    # list of clone pairs this file participates in; ``duplication_pct``
    # is the percent of NLOC covered by clones.
    clones: list[ClonePair] = field(default_factory=list)
    duplication_pct: float | None = None
    # Thin graph view exposing only ``has_edge`` — see ``HasEdge`` above.
    # ``None`` on test fixtures that never construct a graph.
    graph_view: HasEdge | None = None
    # Repo-wide per-file commit totals (``commit_count_total`` from
    # git_meta), keyed by repo-relative POSIX path. Used by
    # ``hidden_coupling`` to compute correlation denominators against
    # the partner file. Empty when git indexing was skipped.
    repo_commit_counts: dict[str, int] = field(default_factory=dict)
    # Per-line blame index produced by the FULL git tier (see
    # ``ingestion.git_indexer.function_blame``). ``None`` on ESSENTIAL
    # tier until ``backfill_blame()`` runs; function-level biomarkers
    # must treat ``None`` (and an empty index) as the documented
    # "no signal" outcome and emit zero findings.
    blame_index: BlameIndex | None = None
    # Repo-wide p80 of per-function modification counts, computed by the
    # engine across every function in the analyze() call. ``None`` when
    # blame is unavailable or no functions exist. ``function_hotspot``
    # uses this as the churn threshold for its top-quintile gate.
    repo_function_mod_p80: int | None = None


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
