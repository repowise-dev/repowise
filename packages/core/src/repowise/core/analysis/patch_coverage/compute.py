"""Patch coverage: how much of a change's executable lines the tests ran.

The one computation every surface reads (CLI gate, MCP, REST, UI, hosted).
Pure: it takes the change as ``{path: changed new-side lines}`` and the
coverage as ``{path: FileCoverage}``, both already keyed by repo-relative
POSIX path, and does no I/O.

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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

from ...test_paths import is_test_related_path
from ..health.coverage.model import FileCoverage

FileStatus = Literal["measured", "not_in_report", "no_line_data", "no_coverable_changes"]
GateStatus = Literal["pass", "fail", "no_data", "not_set"]


@dataclass(frozen=True)
class FilePatchCoverage:
    """One changed file's slice of the patch."""

    path: str
    status: FileStatus
    changed_lines: int
    coverable_lines: int = 0
    covered_lines: int = 0
    # Inclusive (start, end) runs of changed, executable, unexecuted lines.
    uncovered_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def pct(self) -> float | None:
        return _pct(self.covered_lines, self.coverable_lines)

    @property
    def uncovered_lines(self) -> int:
        return self.coverable_lines - self.covered_lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "changed_lines": self.changed_lines,
            "coverable_lines": self.coverable_lines,
            "covered_lines": self.covered_lines,
            "pct": self.pct,
            "uncovered_ranges": [list(r) for r in self.uncovered_ranges],
        }


@dataclass(frozen=True)
class PatchScope:
    """Where the numbers came from, carried so every renderer can say so."""

    label: str = ""  # the diff, e.g. "origin/main...HEAD"
    report_formats: tuple[str, ...] = ()
    report_files: int = 0
    # Report paths that did not map to a file in the repository.
    unmatched_report_paths: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "report_formats": list(self.report_formats),
            "report_files": self.report_files,
            "unmatched_report_paths": self.unmatched_report_paths,
        }


@dataclass(frozen=True)
class PatchCoverage:
    """The patch-coverage verdict for one change."""

    files: tuple[FilePatchCoverage, ...] = ()
    threshold: float | None = None
    # Changed files left out because the report does not measure their kind.
    out_of_scope: int = 0
    scope: PatchScope = field(default_factory=PatchScope)

    @property
    def coverable_lines(self) -> int:
        return sum(f.coverable_lines for f in self.files if f.status == "measured")

    @property
    def covered_lines(self) -> int:
        return sum(f.covered_lines for f in self.files if f.status == "measured")

    @property
    def pct(self) -> float | None:
        return _pct(self.covered_lines, self.coverable_lines)

    @property
    def gate(self) -> GateStatus:
        if self.threshold is None:
            return "not_set"
        pct = self.pct
        if pct is None:
            return "no_data"
        return "pass" if pct >= self.threshold else "fail"

    def with_status(self, status: FileStatus) -> list[FilePatchCoverage]:
        return [f for f in self.files if f.status == status]

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {
            s: 0 for s in ("measured", "not_in_report", "no_line_data", "no_coverable_changes")
        }
        for f in self.files:
            counts[f.status] += 1
        counts["out_of_scope"] = self.out_of_scope
        return {
            "patch_coverage_pct": self.pct,
            "covered_lines": self.covered_lines,
            "coverable_lines": self.coverable_lines,
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
) -> PatchCoverage:
    """Intersect a change with a coverage report. See the module docstring."""
    measured_suffixes = {PurePosixPath(p).suffix for p in coverage}
    files: list[FilePatchCoverage] = []
    out_of_scope = 0
    for path in sorted(changed):
        lines = set(changed[path])
        if not lines:
            continue
        fc = coverage.get(path)
        if fc is None:
            if is_test_related_path(path) or PurePosixPath(path).suffix not in measured_suffixes:
                out_of_scope += 1
            else:
                files.append(FilePatchCoverage(path, "not_in_report", len(lines)))
            continue
        files.append(_file_patch(path, lines, fc))
    return PatchCoverage(
        files=tuple(files),
        threshold=threshold,
        out_of_scope=out_of_scope,
        scope=scope or PatchScope(),
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
        coverable_lines=len(coverable),
        covered_lines=len(covered),
        uncovered_ranges=line_ranges(coverable - covered),
    )


def line_ranges(lines: Iterable[int]) -> tuple[tuple[int, int], ...]:
    """Collapse line numbers into inclusive ``(start, end)`` runs."""
    runs: list[tuple[int, int]] = []
    for n in sorted(lines):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return tuple(runs)


def _pct(covered: int, coverable: int) -> float | None:
    return round(covered / coverable * 100.0, 2) if coverable else None
