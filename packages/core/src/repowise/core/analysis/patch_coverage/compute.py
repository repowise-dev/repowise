"""Patch coverage: how much of a change's executable lines the tests ran.

Pure: it takes the change as ``{path: changed new-side lines}`` and the
coverage as ``{path: FileCoverage}``, both keyed by repo-relative POSIX path,
and does no I/O. Built to be the one computation a CLI gate, an agent tool or
a hosted pull-request check reads, so they cannot disagree.

The denominator is changed lines the report calls executable. A changed
comment or blank line is neither covered nor uncovered, which is why the
report's ``coverable_lines`` is required: without it a file is
``no_line_data``, never 0%.

What counts is decided by the report. A changed file the report names is
measured. One it does not name is ``not_in_report`` when it looks like code
the report measures (its extension appears in the report), so a new,
untested source file is surfaced rather than dropped; it is out of scope
when it is a test file or a type the report never measures (docs, config).
Ceiling: a not-in-report file is reported but excluded from the percentage,
because its executable lines are unknown. A stricter gate can count it once a
report format carries "file present, nothing executed".

Path-scoped gates (``coverage.gates``) apply the same rule to the measured
files their globs match. One that fails and is not informational fails the
change, whatever the whole-change figure says.

Branch coverage on changed lines is reported beside the line figure, never
blended into it: a changed ``if`` whose other way never ran is covered as a
line and "partly taken" as a branch. The share counts the branches on every
changed executable line the report gives branch counts for, executed or not,
and every line that lowers it is shown: a line that never ran is uncovered, a
line that ran with fewer branches taken than it has is partly taken. With no
such line it is not measured (``None``), never 0%. ``scope.branch_data`` says
whether that is because the coverage carries no per-line branch data at all
(a setup problem) or because no changed line branches.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, get_args

import pathspec

from ...test_paths import is_test_related_path
from ..changed_lines import line_ranges
from ..health.coverage.freshness import FreshnessStatus
from ..health.coverage.model import FileCoverage

if TYPE_CHECKING:
    from ..health.coverage.discovery import PathGate, ResolvedCoverage
    from .delta import ProjectDelta
    from .hints import TestHint
    from .risk import FileRisk

FileStatus = Literal["measured", "not_in_report", "no_line_data", "no_coverable_changes"]
#: Whether the coverage read carries per-line branch counts: ``per_line`` it
#: does, ``none`` the report has none, ``stored_before`` the stored rows were
#: written before per-line branches were kept.
BranchData = Literal["per_line", "none", "stored_before"]
GateStatus = Literal["pass", "fail", "no_data", "not_set", "too_small"]


@dataclass(frozen=True)
class FilePatchCoverage:
    """One changed file's slice of the patch. Counts are line counts."""

    file_path: str
    status: FileStatus
    changed_line_count: int
    coverable_line_count: int = 0
    covered_line_count: int = 0
    # Inclusive (start, end) runs of changed, executable, unexecuted lines.
    uncovered_ranges: tuple[tuple[int, int], ...] = ()
    # Branches on the changed executable lines the report gives branch counts for.
    branch_taken: int = 0
    branch_total: int = 0
    # Inclusive runs of changed lines that ran with fewer branches taken than they have.
    partial_ranges: tuple[tuple[int, int], ...] = ()
    # What history says about the file (``risk.attach_risk``); ``None`` unread.
    risk: FileRisk | None = None
    # Where to add a test per uncovered range (``hints.attach_hints``); ``None``
    # without an index, empty when there is nothing to hint.
    hints: tuple[TestHint, ...] | None = None

    @property
    def patch_coverage_pct(self) -> float | None:
        return _pct(self.covered_line_count, self.coverable_line_count)

    @property
    def uncovered_line_count(self) -> int:
        return self.coverable_line_count - self.covered_line_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "status": self.status,
            "changed_line_count": self.changed_line_count,
            "coverable_line_count": self.coverable_line_count,
            "covered_line_count": self.covered_line_count,
            "patch_coverage_pct": _round(self.patch_coverage_pct),
            "uncovered_ranges": [list(r) for r in self.uncovered_ranges],
            "branch_taken": self.branch_taken,
            "branch_total": self.branch_total,
            "partial_ranges": [list(r) for r in self.partial_ranges],
            "risk": self.risk.to_dict() if self.risk is not None else None,
            "hints": None if self.hints is None else [h.to_dict() for h in self.hints],
        }


@dataclass(frozen=True)
class PatchScope:
    """Where the numbers came from, carried so every renderer can say so."""

    label: str = ""  # the diff, e.g. "origin/main...HEAD"
    source_formats: tuple[str, ...] = ()
    reports: tuple[str, ...] = ()  # report files read, as the caller named them
    # File entries across those reports, and how many did not map to a file in
    # the repository. ``None`` when unknown (an ingest that did not record them).
    report_path_count: int | None = None
    unmatched_report_path_count: int | None = None
    # Fewer than half the report's files mapped: the figure covers a fragment.
    mapping_partial: bool = False
    # The commit the coverage was measured at, and whether that is the
    # change's head (``coverage_freshness``). Stale coverage still computes,
    # but its line numbers describe other code, so every renderer says so.
    measured_commit: str | None = None
    freshness: FreshnessStatus = "unknown"
    # Changed files left out by ``coverage.ignore`` before anything was
    # measured. Set by :func:`compute_patch_coverage`.
    ignored_file_count: int = 0
    # Invalid ``coverage.gates`` entries, one message each. The CLI refuses to
    # run on them; read-only surfaces carry them and judge no path gate.
    config_errors: tuple[str, ...] = ()
    # Derived from the coverage by :func:`compute_patch_coverage` when unset.
    branch_data: BranchData | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source_formats": list(self.source_formats),
            "reports": list(self.reports),
            "report_path_count": self.report_path_count,
            "unmatched_report_path_count": self.unmatched_report_path_count,
            "mapping_partial": self.mapping_partial,
            "measured_commit": self.measured_commit,
            "freshness": self.freshness,
            "ignored_file_count": self.ignored_file_count,
            "config_errors": list(self.config_errors),
            "branch_data": self.branch_data,
        }


@dataclass(frozen=True)
class PathGateResult:
    """One path-scoped gate (``coverage.gates``) judged on the change.

    Counts cover the measured changed files the gate's globs match, and the
    verdict follows the whole change's rule. The small-change tolerance
    exempts a small change, not a small slice of a big one, so *small_change*
    is the whole change's test. An unjudged gate (stale coverage, invalid
    config) reads ``no_data`` whatever its counts.
    """

    name: str
    paths: tuple[str, ...]
    threshold: float | None
    informational: bool
    measured_file_count: int
    covered_line_count: int
    coverable_line_count: int
    # Matching changed files the report does not measure (``not_in_report``,
    # ``no_line_data``): outside the percentage, but counted so a gate over
    # only unmeasured files does not read as empty.
    unmeasured_file_count: int = 0
    small_change: bool = False
    judged: bool = True

    @property
    def patch_coverage_pct(self) -> float | None:
        return _pct(self.covered_line_count, self.coverable_line_count)

    @property
    def gate(self) -> GateStatus:
        if not self.judged:
            return "no_data"
        return _gate(
            self.threshold, self.covered_line_count, self.coverable_line_count, self.small_change
        )

    @property
    def fails_change(self) -> bool:
        """A failed gate that is not informational fails the whole change."""
        return self.gate == "fail" and not self.informational

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "paths": list(self.paths),
            "threshold": self.threshold,
            "informational": self.informational,
            "measured_file_count": self.measured_file_count,
            "unmeasured_file_count": self.unmeasured_file_count,
            "covered_line_count": self.covered_line_count,
            "coverable_line_count": self.coverable_line_count,
            "patch_coverage_pct": _round(self.patch_coverage_pct),
            "gate": self.gate,
        }


@dataclass(frozen=True)
class PatchCoverage:
    """The patch-coverage verdict for one change."""

    files: tuple[FilePatchCoverage, ...] = ()
    threshold: float | None = None
    # Changed files left out because the report does not measure their kind.
    out_of_scope_count: int = 0
    scope: PatchScope = field(default_factory=PatchScope)
    # Small-change tolerance: a change with fewer changed executable lines
    # than this is reported against the threshold but never fails it.
    min_coverable_lines: int | None = None
    # Path-scoped gates, in config order. A failing one that is not
    # informational fails ``gate``; ``flat_gate`` ignores them.
    path_gates: tuple[PathGateResult, ...] = ()
    # A stricter gate over the risky files only (``risk.attach_risk``).
    risky_threshold: float | None = None
    # Project coverage at the change's base against its head (``delta``).
    project: ProjectDelta | None = None
    # The gate over branches on changed lines, judged apart from the line gates.
    branch_threshold: float | None = None

    @property
    def changed_file_count(self) -> int:
        return len(self.files) + self.out_of_scope_count

    @property
    def coverable_line_count(self) -> int:
        return sum(f.coverable_line_count for f in self.with_status("measured"))

    @property
    def covered_line_count(self) -> int:
        return sum(f.covered_line_count for f in self.with_status("measured"))

    @property
    def patch_coverage_pct(self) -> float | None:
        return _pct(self.covered_line_count, self.coverable_line_count)

    @property
    def flat_gate(self) -> GateStatus:
        """The whole change against ``threshold``, path-scoped gates aside."""
        return _gate(
            self.threshold, self.covered_line_count, self.coverable_line_count, self.small_change
        )

    @property
    def small_change(self) -> bool:
        """Fewer changed executable lines than ``min_coverable_lines``."""
        n = self.min_coverable_lines
        return n is not None and self.coverable_line_count < n

    @property
    def gate(self) -> GateStatus:
        """``flat_gate``, or ``fail`` when a path-scoped, the risky-file, the branch or the
        project gate fails.

        With no flat threshold the risky-file gate's status is the verdict, then
        the branch gate's, then the project gate's.
        """
        project = self.project.gate if self.project is not None else "not_set"
        # In precedence order; the first gate with a verdict decides.
        verdicts = [self.flat_gate, self.risky_gate, self.branch_gate, project]
        if self.failing_path_gates or "fail" in verdicts:
            return "fail"
        # An unapplied branch gate gives way to a project verdict.
        if project != "not_set" and verdicts[2] == "no_data":
            verdicts[2] = "not_set"
        return next((v for v in verdicts if v != "not_set"), "not_set")

    @property
    def failing_path_gates(self) -> list[PathGateResult]:
        return [g for g in self.path_gates if g.fails_change]

    @property
    def risky_files(self) -> list[FilePatchCoverage]:
        """Measured files whose risk marks them risky."""
        return [f for f in self.with_status("measured") if f.risk is not None and f.risk.risky]

    @property
    def risky_covered_line_count(self) -> int:
        return sum(f.covered_line_count for f in self.risky_files)

    @property
    def risky_coverable_line_count(self) -> int:
        return sum(f.coverable_line_count for f in self.risky_files)

    @property
    def risky_pct(self) -> float | None:
        return _pct(self.risky_covered_line_count, self.risky_coverable_line_count)

    @property
    def risky_gate(self) -> GateStatus:
        """``risky_threshold`` over the risky files' lines, by the one gate rule.

        No risky file is ``no_data``. Like a path gate it takes the whole
        change's small-change tolerance: a small change is exempt, a small
        risky slice of a big one is not.
        """
        return _gate(
            self.risky_threshold,
            self.risky_covered_line_count,
            self.risky_coverable_line_count,
            self.small_change,
        )

    @property
    def branch_taken(self) -> int:
        return sum(f.branch_taken for f in self.with_status("measured"))

    @property
    def branch_total(self) -> int:
        return sum(f.branch_total for f in self.with_status("measured"))

    @property
    def branch_pct(self) -> float | None:
        """Taken share of the branches on changed lines; ``None`` when none carries any."""
        return _pct(self.branch_taken, self.branch_total)

    @property
    def branch_gate(self) -> GateStatus:
        """``branch_threshold`` over branches on changed lines, by the one gate rule.

        Not measured is ``no_data``, never a failure. The whole change's
        small-change tolerance applies, as it does to the line gate.
        """
        return _gate(
            self.branch_threshold, self.branch_taken, self.branch_total, self.small_change
        )

    @property
    def partial_line_count(self) -> int:
        return sum(b - a + 1 for f in self.files for a, b in f.partial_ranges)

    def with_status(self, status: FileStatus) -> list[FilePatchCoverage]:
        return [f for f in self.files if f.status == status]

    def _risky_dict(self) -> dict[str, Any] | None:
        # Null when no row carries risk: "not assessed" is not "nothing risky".
        if all(f.risk is None for f in self.files):
            return None
        return {
            "file_count": len(self.risky_files),
            "covered_line_count": self.risky_covered_line_count,
            "coverable_line_count": self.risky_coverable_line_count,
            "patch_coverage_pct": _round(self.risky_pct),
            "threshold": self.risky_threshold,
            "gate": self.risky_gate,
        }

    def _branches_dict(self) -> dict[str, Any] | None:
        # Null when no changed line carries branch data and no branch gate asked.
        if not self.branch_total and self.branch_threshold is None:
            return None
        return {
            "branch_taken": self.branch_taken,
            "branch_total": self.branch_total,
            "branch_coverage_pct": _round(self.branch_pct),
            "partial_line_count": self.partial_line_count,
            "threshold": self.branch_threshold,
            "gate": self.branch_gate,
        }

    def to_dict(self) -> dict[str, Any]:
        counts = {status: len(self.with_status(status)) for status in get_args(FileStatus)}
        counts["out_of_scope"] = self.out_of_scope_count
        return {
            "patch_coverage_pct": _round(self.patch_coverage_pct),
            "covered_line_count": self.covered_line_count,
            "coverable_line_count": self.coverable_line_count,
            "threshold": self.threshold,
            "min_coverable_lines": self.min_coverable_lines,
            "gate": self.gate,
            "file_counts": counts,
            "files": [f.to_dict() for f in self.files],
            "scope": self.scope.to_dict(),
            "path_gates": [g.to_dict() for g in self.path_gates],
            "risky": self._risky_dict(),
            "project": self.project.to_dict() if self.project is not None else None,
            "branches": self._branches_dict(),
        }


def compute_patch_coverage(
    changed: Mapping[str, Iterable[int]],
    coverage: Mapping[str, FileCoverage],
    *,
    threshold: float | None = None,
    scope: PatchScope | None = None,
    report_paths: Iterable[str] | None = None,
    min_coverable_lines: int | None = None,
    ignore: Sequence[str] = (),
    gates: Sequence[PathGate] = (),
    judge_gates: bool = True,
) -> PatchCoverage:
    """Intersect a change with a coverage report. See the module docstring.

    *report_paths* is every path the report measures, when *coverage* holds
    only the changed files' entries (a stored report read for one change); it
    decides which unlisted files count as ``not_in_report``. Changed files
    matching *ignore* (``coverage.ignore``, gitignore syntax) are dropped
    before anything is measured and counted in the scope. Each of *gates*
    (``coverage.gates``) is judged on the measured files its globs match;
    with *judge_gates* false they are counted but read ``no_data``.
    """
    measured_suffixes = {PurePosixPath(p).suffix for p in report_paths or coverage}
    ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore)
    scope = _with_branch_data(scope or PatchScope(), coverage)
    files: list[FilePatchCoverage] = []
    out_of_scope = ignored = 0
    for path in sorted(changed):
        lines = set(changed[path])
        if not lines:
            continue
        if ignore_spec.match_file(path):
            ignored += 1
        elif (row := _changed_file(path, lines, coverage, measured_suffixes)) is None:
            out_of_scope += 1
        else:
            files.append(row)
    pc = PatchCoverage(
        files=tuple(files),
        threshold=threshold,
        out_of_scope_count=out_of_scope,
        scope=replace(scope, ignored_file_count=ignored),
        min_coverable_lines=min_coverable_lines,
    )
    small = pc.small_change
    return replace(
        pc, path_gates=tuple(_path_gate(files, g, small, judge_gates) for g in gates)
    )


def _with_branch_data(scope: PatchScope, coverage: Mapping[str, FileCoverage]) -> PatchScope:
    """*scope* with ``branch_data`` derived from *coverage* when the caller left it unset."""
    if scope.branch_data is not None:
        return scope
    per_line = any(fc.branch_lines for fc in coverage.values())
    return replace(scope, branch_data="per_line" if per_line else "none")


def patch_coverage_from_resolved(
    changed: Mapping[str, Iterable[int]],
    resolved: ResolvedCoverage,
    *,
    threshold: float | None = None,
    label: str = "",
    reports: Sequence[str] = (),
    min_coverable_lines: int | None = None,
    ignore: Sequence[str] = (),
    gates: Sequence[PathGate] = (),
) -> PatchCoverage:
    """:func:`compute_patch_coverage` over reports already resolved to repo keys."""
    unmatched = len(resolved.unmatched) + len(resolved.ambiguous)
    return compute_patch_coverage(
        changed,
        {fc.file_path: fc for fc in resolved.files},
        threshold=threshold,
        min_coverable_lines=min_coverable_lines,
        ignore=ignore,
        gates=gates,
        scope=PatchScope(
            label=label,
            source_formats=tuple(resolved.source_formats),
            reports=tuple(reports),
            report_path_count=resolved.total,
            unmatched_report_path_count=unmatched,
            mapping_partial=resolved.mapping_partial,
        ),
    )


def _changed_file(
    path: str,
    lines: set[int],
    coverage: Mapping[str, FileCoverage],
    measured_suffixes: set[str],
) -> FilePatchCoverage | None:
    """One changed file's row, or ``None`` when it is out of the report's scope."""
    fc = coverage.get(path)
    if fc is not None:
        return _file_patch(path, lines, fc)
    if is_test_related_path(path) or PurePosixPath(path).suffix not in measured_suffixes:
        return None
    return FilePatchCoverage(path, "not_in_report", len(lines))


def _file_patch(path: str, lines: set[int], fc: FileCoverage) -> FilePatchCoverage:
    if not fc.coverable_lines:
        return FilePatchCoverage(path, "no_line_data", len(lines))
    coverable = lines.intersection(fc.coverable_lines)
    if not coverable:
        return FilePatchCoverage(path, "no_coverable_changes", len(lines))
    covered = coverable.intersection(fc.covered_lines)
    branched = {n: fc.branch_lines[n] for n in coverable if n in fc.branch_lines}
    partial = (n for n, (taken, total) in branched.items() if n in covered and taken < total)
    return FilePatchCoverage(
        path,
        "measured",
        len(lines),
        coverable_line_count=len(coverable),
        covered_line_count=len(covered),
        uncovered_ranges=line_ranges(coverable - covered),
        branch_taken=sum(taken for taken, _ in branched.values()),
        branch_total=sum(total for _, total in branched.values()),
        partial_ranges=line_ranges(partial),
    )


def _path_gate(
    files: Sequence[FilePatchCoverage], gate: PathGate, small_change: bool, judged: bool
) -> PathGateResult:
    """*gate* over the changed files its globs match; a file may match several gates."""
    spec = pathspec.PathSpec.from_lines("gitwildmatch", gate.paths)
    matched = [f for f in files if spec.match_file(f.file_path)]
    hits = [f for f in matched if f.status == "measured"]
    return PathGateResult(
        name=gate.name,
        paths=gate.paths,
        threshold=gate.fail_under,
        informational=gate.informational,
        measured_file_count=len(hits),
        unmeasured_file_count=sum(f.status in _UNMEASURED for f in matched),
        covered_line_count=sum(f.covered_line_count for f in hits),
        coverable_line_count=sum(f.coverable_line_count for f in hits),
        small_change=small_change,
        judged=judged,
    )


_UNMEASURED = frozenset({"not_in_report", "no_line_data"})


def _gate(threshold: float | None, covered: int, coverable: int, small: bool) -> GateStatus:
    """One rule for the whole change and every path-scoped gate."""
    if threshold is None:
        return "not_set"
    pct = _pct(covered, coverable)
    if pct is None:
        return "no_data"
    if pct >= threshold:
        return "pass"
    return "too_small" if small else "fail"


def _pct(covered: int, coverable: int) -> float | None:
    # Unrounded, so a gate never passes on rounding (79.998% is below 80%).
    # Rounding happens only where the number is shown or serialized.
    # Multiply first: 57 / 100 * 100 is 56.99999999999999.
    return covered * 100.0 / coverable if coverable else None


def _round(pct: float | None) -> float | None:
    # Floored like the display, so a serialized 80.0 never means 79.998.
    return None if pct is None else math.floor(pct * 100) / 100
