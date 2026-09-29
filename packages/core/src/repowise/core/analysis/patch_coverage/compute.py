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

FileStatus = Literal["measured", "not_in_report", "no_line_data", "no_coverable_changes"]
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
        """``flat_gate``, or ``fail`` when a path-scoped gate fails the change."""
        return "fail" if self.failing_path_gates else self.flat_gate

    @property
    def failing_path_gates(self) -> list[PathGateResult]:
        return [g for g in self.path_gates if g.fails_change]

    def with_status(self, status: FileStatus) -> list[FilePatchCoverage]:
        return [f for f in self.files if f.status == status]

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
        scope=replace(scope or PatchScope(), ignored_file_count=ignored),
        min_coverable_lines=min_coverable_lines,
    )
    small = pc.small_change
    return replace(
        pc, path_gates=tuple(_path_gate(files, g, small, judge_gates) for g in gates)
    )


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
    return FilePatchCoverage(
        path,
        "measured",
        len(lines),
        coverable_line_count=len(coverable),
        covered_line_count=len(covered),
        uncovered_ranges=line_ranges(coverable - covered),
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
