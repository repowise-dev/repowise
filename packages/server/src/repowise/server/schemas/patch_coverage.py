"""Patch coverage over the wire: ``PatchCoverage.to_dict()``, typed.

Mirrors ``repowise coverage check --format json`` field for field, so a client
reading this endpoint and a CI job reading the command see one shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from repowise.core.analysis.health.coverage.freshness import FreshnessStatus
from repowise.core.analysis.patch_coverage.compute import BranchData, FileStatus, GateStatus
from repowise.core.analysis.patch_coverage.delta import (
    CauseBasis,
    CauseKind,
    DeltaBasis,
    IndirectStatus,
)
from repowise.core.analysis.patch_coverage.hints import HintBasis
from repowise.core.analysis.patch_coverage.risk import RiskBasis


class _Strict(BaseModel):
    # A key ``to_dict`` gains must fail validation, not vanish from the API.
    model_config = ConfigDict(extra="forbid")


class PatchCoverageFileRisk(_Strict):
    #: Recency-decayed bug-fix count from git; null when git history was not read.
    fix_pressure: float | None
    #: Files importing this one, from the index graph; null without index data.
    dependents: int | None
    hotspot: bool | None
    bug_magnet: bool | None
    #: ``git`` for a file the index has no row for (one the change adds).
    basis: RiskBasis
    #: Index rows: hotspot or bug magnet. Git only: top quartile of files with
    #: bug-fix history, by fix pressure.
    risky: bool
    reasons: list[str]


class PatchCoverageTestHint(_Strict):
    """Where to extend the tests for one uncovered range."""

    #: Inclusive ``[start, end]``, one of the row's ``uncovered_ranges``.
    range: list[int]
    #: Innermost indexed symbol containing the range start; null outside any.
    symbol: str | None
    #: Up to three test files, best first.
    tests: list[str]
    #: ``per_test`` (measured per-test coverage of the same symbol or nearby
    #: lines) beats ``call_graph`` (tests reaching the symbol), which beats
    #: ``import_graph`` (tests importing the file); ``none`` names no test.
    basis: HintBasis
    #: How many test files qualified before the cap.
    total: int


class PatchCoverageFile(_Strict):
    file_path: str
    status: FileStatus
    changed_line_count: int
    coverable_line_count: int
    covered_line_count: int
    #: 0-100, floored to two decimals; null when no changed line is executable.
    patch_coverage_pct: float | None
    #: Inclusive ``[start, end]`` runs of changed, executable, unexecuted lines.
    uncovered_ranges: list[list[int]]
    #: Branches on the changed executable lines the report gives branch counts
    #: for; both 0 without branch data.
    branch_taken: int
    branch_total: int
    #: Inclusive ``[start, end]`` runs of changed lines that ran with fewer
    #: branches taken than they have; empty without branch data.
    partial_ranges: list[list[int]]
    #: Null when the file's risk was not assessed.
    risk: PatchCoverageFileRisk | None
    #: One per uncovered range (the first eight); null without an index.
    hints: list[PatchCoverageTestHint] | None


class PatchCoverageFileCounts(_Strict):
    measured: int
    not_in_report: int
    no_line_data: int
    no_coverable_changes: int
    out_of_scope: int


class PatchCoverageScope(_Strict):
    label: str
    source_formats: list[str]
    reports: list[str]
    #: Null when unknown: stored by an ingest that did not record its path counts.
    report_path_count: int | None
    unmatched_report_path_count: int | None
    #: Fewer than half the report's paths matched this repository.
    mapping_partial: bool
    measured_commit: str | None
    #: ``stale`` when the coverage was measured at another commit than the
    #: change's head: the figure still computes but describes other code.
    freshness: FreshnessStatus
    #: Changed files left out by ``coverage.ignore`` before measuring.
    ignored_file_count: int
    #: Invalid ``coverage.gates`` entries, one message each; while any is
    #: present no path-scoped gate is judged.
    config_errors: list[str]
    #: ``per_line`` when the coverage carries per-line branch counts; ``none``
    #: or ``stored_before`` (stored rows predate them) when it does not.
    branch_data: BranchData | None


class PatchCoveragePathGate(_Strict):
    """One path-scoped gate from ``coverage.gates``, judged on the change."""

    name: str
    #: Gitignore-style globs naming the gate's files.
    paths: list[str]
    threshold: float | None
    #: Judged and shown, but never fails the change.
    informational: bool
    #: Measured changed files the globs match; a file may count in several gates.
    measured_file_count: int
    #: Matching changed files the report does not measure, outside the percentage.
    unmeasured_file_count: int
    covered_line_count: int
    coverable_line_count: int
    patch_coverage_pct: float | None
    #: ``no_data`` too when not judged: stale coverage or ``scope.config_errors``.
    gate: GateStatus


class PatchCoverageRisky(_Strict):
    """Patch coverage over the measured files history marks as risky."""

    file_count: int
    covered_line_count: int
    coverable_line_count: int
    patch_coverage_pct: float | None
    threshold: float | None
    #: ``no_data`` when no risky file has a changed executable line;
    #: ``too_small`` under the whole change's small-change tolerance.
    gate: GateStatus


class PatchCoverageBranches(_Strict):
    """Branches on changed lines, reported beside patch coverage, never blended into it."""

    branch_taken: int
    branch_total: int
    #: 0-100, floored to two decimals; null when no changed line has branch data.
    branch_coverage_pct: float | None
    #: Changed lines that ran with fewer branches taken than they have.
    partial_line_count: int
    threshold: float | None
    #: ``no_data`` when no changed line has branch data; ``too_small`` under the
    #: whole change's small-change tolerance.
    gate: GateStatus


class PatchCoverageProjectTotals(_Strict):
    covered_line_count: int
    coverable_line_count: int
    #: 0-100, floored to two decimals; null when nothing was coverable.
    coverage_pct: float | None


class PatchCoverageOutsideChangeCause(_Strict):
    """A changed file that explains coverage lost outside the change."""

    kind: CauseKind
    path: str
    #: ``per_test`` measured, ``graph`` inferred from the index's call and
    #: import edges, ``name`` a test named for the file.
    basis: CauseBasis


class PatchCoverageOutsideChange(_Strict):
    """A file whose coverage changed on lines the change did not touch."""

    file_path: str
    #: ``no_longer_measured``: the base report named it, the head's does not.
    status: IndirectStatus
    #: Head-side inclusive ``[start, end]`` runs covered at the base, not at the head.
    newly_uncovered_ranges: list[list[int]]
    newly_uncovered_line_count: int
    newly_covered_line_count: int
    base_pct: float | None
    head_pct: float | None
    #: Null when not assessed; empty when nothing in the change explains it.
    causes: list[PatchCoverageOutsideChangeCause] | None


class PatchCoverageProject(_Strict):
    """Project coverage at the change's base against its head."""

    #: ``history``: stored ingests (totals only); ``base_report``: a report
    #: measured at the base commit.
    basis: DeltaBasis
    base_commit: str | None
    head_commit: str | None
    base: PatchCoverageProjectTotals | None
    head: PatchCoverageProjectTotals | None
    #: Head minus base, in percentage points, rounded to two decimals.
    delta_pct: float | None
    max_drop: float | None
    #: ``no_data`` when the two measurements cannot be compared.
    gate: GateStatus
    #: Why the base and head measured different things; empty when comparable.
    incomparable: list[str]
    #: Null on the history basis, which keeps no per-file rows per commit, and
    #: when most files of the base report did not line up with the base commit.
    outside_change: list[PatchCoverageOutsideChange] | None
    #: Why files were left out of ``outside_change``; null when none were.
    outside_change_note: str | None


class PatchCoverageResponse(_Strict):
    patch_coverage_pct: float | None
    covered_line_count: int
    coverable_line_count: int
    threshold: float | None
    #: Small-change tolerance: below this many changed executable lines a
    #: missed threshold reads ``too_small`` and does not fail.
    min_coverable_lines: int | None
    #: Fails when the flat gate, a path-scoped gate that is not informational,
    #: the risky-file, the branch or the max-drop gate fails.
    gate: GateStatus
    file_counts: PatchCoverageFileCounts
    files: list[PatchCoverageFile]
    scope: PatchCoverageScope
    #: ``gate`` reads ``fail`` when any one here fails and is not informational.
    path_gates: list[PatchCoveragePathGate]
    #: Null when no file's risk was assessed.
    risky: PatchCoverageRisky | None
    #: Null when no ingest was measured at the change's base.
    project: PatchCoverageProject | None
    #: Null when no changed line has per-line branch data and no branch gate was set.
    branches: PatchCoverageBranches | None
