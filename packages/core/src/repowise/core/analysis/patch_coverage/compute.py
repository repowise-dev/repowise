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
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, get_args

from ...test_paths import is_test_related_path
from ..changed_lines import line_ranges
from ..health.coverage.freshness import FreshnessStatus
from ..health.coverage.model import FileCoverage

if TYPE_CHECKING:
    from ..health.coverage.discovery import ResolvedCoverage

FileStatus = Literal["measured", "not_in_report", "no_line_data", "no_coverable_changes"]
GateStatus = Literal["pass", "fail", "no_data", "not_set"]


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
    # the repository. ``None`` when unknown (stored coverage keeps only matches).
    report_path_count: int | None = None
    unmatched_report_path_count: int | None = None
    # Fewer than half the report's files mapped: the figure covers a fragment.
    mapping_partial: bool = False
    # The commit the coverage was measured at, and whether that is the
    # change's head (``coverage_freshness``). Stale coverage still computes,
    # but its line numbers describe other code, so every renderer says so.
    measured_commit: str | None = None
    freshness: FreshnessStatus = "unknown"

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
        }


@dataclass(frozen=True)
class PatchCoverage:
    """The patch-coverage verdict for one change."""

    files: tuple[FilePatchCoverage, ...] = ()
    threshold: float | None = None
    # Changed files left out because the report does not measure their kind.
    out_of_scope_count: int = 0
    scope: PatchScope = field(default_factory=PatchScope)

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
    def gate(self) -> GateStatus:
        if self.threshold is None:
            return "not_set"
        pct = self.patch_coverage_pct
        if pct is None:
            return "no_data"
        return "pass" if pct >= self.threshold else "fail"

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
            "gate": self.gate,
            "file_counts": counts,
            "files": [f.to_dict() for f in self.files],
            "scope": self.scope.to_dict(),
        }


def compute_patch_coverage(
    changed: Mapping[str, Iterable[int]],
    coverage: Mapping[str, FileCoverage],
    *,
    threshold: float | None = None,
    scope: PatchScope | None = None,
    report_paths: Iterable[str] | None = None,
) -> PatchCoverage:
    """Intersect a change with a coverage report. See the module docstring.

    *report_paths* is every path the report measures, when *coverage* holds
    only the changed files' entries (a stored report read for one change); it
    decides which unlisted files count as ``not_in_report``.
    """
    measured_suffixes = {PurePosixPath(p).suffix for p in report_paths or coverage}
    files: list[FilePatchCoverage] = []
    out_of_scope = 0
    for path in sorted(changed):
        lines = set(changed[path])
        if not lines:
            continue
        fc = coverage.get(path)
        if fc is not None:
            files.append(_file_patch(path, lines, fc))
        elif is_test_related_path(path) or PurePosixPath(path).suffix not in measured_suffixes:
            out_of_scope += 1
        else:
            files.append(FilePatchCoverage(path, "not_in_report", len(lines)))
    return PatchCoverage(
        files=tuple(files),
        threshold=threshold,
        out_of_scope_count=out_of_scope,
        scope=scope or PatchScope(),
    )


def patch_coverage_from_resolved(
    changed: Mapping[str, Iterable[int]],
    resolved: ResolvedCoverage,
    *,
    threshold: float | None = None,
    label: str = "",
    reports: Sequence[str] = (),
) -> PatchCoverage:
    """:func:`compute_patch_coverage` over reports already resolved to repo keys."""
    unmatched = len(resolved.unmatched) + len(resolved.ambiguous)
    return compute_patch_coverage(
        changed,
        {fc.file_path: fc for fc in resolved.files},
        threshold=threshold,
        scope=PatchScope(
            label=label,
            source_formats=tuple(resolved.source_formats),
            reports=tuple(reports),
            report_path_count=resolved.matched + unmatched,
            unmatched_report_path_count=unmatched,
            mapping_partial=resolved.mapping_partial,
        ),
    )


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


def _pct(covered: int, coverable: int) -> float | None:
    # Unrounded, so a gate never passes on rounding (79.998% is below 80%).
    # Rounding happens only where the number is shown or serialized.
    # Multiply first: 57 / 100 * 100 is 56.99999999999999.
    return covered * 100.0 / coverable if coverable else None


def _round(pct: float | None) -> float | None:
    # Floored like the display, so a serialized 80.0 never means 79.998.
    return None if pct is None else math.floor(pct * 100) / 100
