"""Project coverage delta and coverage outside the change.

Patch coverage judges only the lines a change touched, so it misses a change
that deletes a test file, or removes a code path that ran other files. Two
views close that gap, both pure:

* :class:`ProjectDelta`: total coverage at the change's base against its
  head, with a tolerance gate (``max_drop``, in percentage points). The two
  measurements must be comparable (:class:`CoverageScope`): the same kind and
  number of reports, the same ``coverage.ignore``, neither a partial mapping.
  Otherwise the delta is reported as incomparable and never judged.
* :func:`indirect_changes` (``outside_change`` on the wire): files whose
  coverage changed on lines the change did not touch, each base line moved to
  the head through the diff
  (:func:`~repowise.core.analysis.changed_lines.map_old_line`). Lines the
  change touched belong to patch coverage and are left out here.

The base comes from a report measured at the base commit (``base_report``:
totals and per-file comparison) or from the stored ingest history
(``history``: totals only).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import pathspec

from ..changed_lines import FileDiff, line_ranges, map_old_line
from ..health.coverage.discovery import CoverageScope
from ..health.coverage.model import FileCoverage
from .compute import GateStatus, _pct, _round

DeltaBasis = Literal["base_report", "history"]
IndirectStatus = Literal["changed", "no_longer_measured"]
CauseKind = Literal["test_deleted", "test_modified", "dependent_changed"]
CauseBasis = Literal["per_test", "graph", "name"]


@dataclass(frozen=True)
class ProjectTotals:
    """Covered and coverable lines over every measured file."""

    covered_line_count: int
    coverable_line_count: int

    @property
    def pct(self) -> float | None:
        return _pct(self.covered_line_count, self.coverable_line_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered_line_count": self.covered_line_count,
            "coverable_line_count": self.coverable_line_count,
            "coverage_pct": _round(self.pct),
        }


def project_totals(
    coverage: Mapping[str, FileCoverage], ignore: Sequence[str] = ()
) -> ProjectTotals:
    """Totals of *coverage* after *ignore*, counted the way the coverage summary counts."""
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore)
    kept = [fc for path, fc in coverage.items() if not spec.match_file(path)]
    return ProjectTotals(
        covered_line_count=sum(_covered_count(fc) for fc in kept),
        coverable_line_count=sum(fc.total_coverable_lines or 0 for fc in kept),
    )


def _covered_count(fc: FileCoverage) -> int:
    # The stored summary's rule (``persistence.crud`` ``_covered_count``).
    if fc.covered_line_count is not None:
        return fc.covered_line_count
    return round(float(fc.line_coverage_pct or 0.0) / 100.0 * (fc.total_coverable_lines or 0))


def _file_pct(fc: FileCoverage) -> float | None:
    return _pct(_covered_count(fc), fc.total_coverable_lines or 0)


def incomparable_reasons(
    base: CoverageScope | None, head: CoverageScope | None
) -> tuple[str, ...]:
    """Why a base and a head measurement cannot be compared; empty when they can.

    ``None`` is a measurement that recorded no scope (an ingest written before
    scopes were kept).
    """
    reasons = []
    if base is None:
        reasons.append(
            "the base ingest predates scope records; pass --base-report, "
            "or re-measure coverage at the base commit"
        )
    if head is None:
        reasons.append("the head ingest predates scope records; re-ingest it")
    if base is None or head is None:
        return tuple(reasons)
    reasons += [
        f"the {side} report mapped fewer than half its files"
        for side, scope in (("base", base), ("head", head))
        if scope.mapping_partial
    ]
    if Counter(base.report_formats) != Counter(head.report_formats):
        reasons.append(
            f"the base read {_formats_text(base.report_formats)}, "
            f"the head read {_formats_text(head.report_formats)}"
        )
    if sorted(base.ignore) != sorted(head.ignore):
        reasons.append("coverage.ignore differs")
    return tuple(reasons)


def _formats_text(formats: Iterable[str]) -> str:
    """``"2 lcov reports and 1 cobertura report"``; ``"no report"`` when empty."""
    counts = Counter(formats)
    if not counts:
        return "no report"
    parts = [f"{n} {fmt} report{'s' if n != 1 else ''}" for fmt, n in sorted(counts.items())]
    return " and ".join(parts)


@dataclass(frozen=True)
class IndirectCause:
    """A changed file that explains coverage lost outside the change."""

    kind: CauseKind
    path: str
    #: ``per_test`` measured, ``graph`` inferred from the index, ``name`` a
    #: git-only pairing of a test's name with the file's.
    basis: CauseBasis

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "basis": self.basis}


@dataclass(frozen=True)
class IndirectChange:
    """One file whose coverage changed on lines the change did not touch."""

    file_path: str  # the head-side path
    #: Head-side inclusive runs of lines covered at the base and not at the head.
    newly_uncovered_ranges: tuple[tuple[int, int], ...]
    newly_covered_line_count: int
    base_pct: float | None
    head_pct: float | None
    status: IndirectStatus
    #: ``None`` not assessed; ``()`` assessed, and nothing in the change explains it.
    causes: tuple[IndirectCause, ...] | None = None

    @property
    def newly_uncovered_line_count(self) -> int:
        return sum(b - a + 1 for a, b in self.newly_uncovered_ranges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "status": self.status,
            "newly_uncovered_ranges": [list(r) for r in self.newly_uncovered_ranges],
            "newly_uncovered_line_count": self.newly_uncovered_line_count,
            "newly_covered_line_count": self.newly_covered_line_count,
            "base_pct": _round(self.base_pct),
            "head_pct": _round(self.head_pct),
            "causes": None if self.causes is None else [c.to_dict() for c in self.causes],
        }


#: A file is misaligned when at least this many untouched base lines, and more
#: than :data:`MISALIGNED_SHARE` of them, land on a line the head does not count.
MISALIGNED_MIN_LINES = 3
MISALIGNED_SHARE = 0.10


@dataclass(frozen=True)
class OutsideChangeScan:
    """:func:`indirect_changes`' answer: the rows, and the files it could not line up."""

    #: ``None`` when most compared files are misaligned: the list would be noise.
    rows: tuple[IndirectChange, ...] | None
    #: Files whose base lines did not line up with the head; left out of *rows*.
    misaligned: tuple[str, ...]
    compared_file_count: int

    def note(self, base_commit: str | None) -> str | None:
        """The one project-level reason, or ``None`` when every file lined up."""
        if not self.misaligned:
            return None
        at = base_commit[:7] if base_commit else "the base commit"
        n = len(self.misaligned)
        files = f"{n} file" if n == 1 else f"{n} files"
        return (
            f"the base report does not line up with {at} in {files}; "
            "measure it at the base commit"
        )


def indirect_changes(
    base_cov: Mapping[str, FileCoverage],
    head_cov: Mapping[str, FileCoverage],
    diffs: Mapping[str, FileDiff],
    renames: Mapping[str, str],
    deleted: Iterable[str],
) -> OutsideChangeScan:
    """Files whose coverage changed outside the change, most newly uncovered lines first.

    *base_cov* is keyed by base-side path, *head_cov* and *diffs* by head-side
    path; *renames* maps old paths to new ones and *deleted* holds the old
    paths the change deleted. A base file with coverable lines that the head
    report no longer names, and the change did not delete, is
    ``no_longer_measured`` under its head-side path.

    A base report measured anywhere but the base commit maps onto the wrong
    lines, so each file's untouched base lines are checked: a misaligned file
    is left out, and when most compared files are, no row is kept at all.
    """
    gone = set(deleted)
    rows: list[IndirectChange] = []
    misaligned: list[str] = []
    compared = 0
    for old_path, base_fc in base_cov.items():
        if old_path in gone:
            continue
        new_path = renames.get(old_path, old_path)
        row, counts = _file_result(new_path, base_fc, head_cov.get(new_path), diffs.get(new_path))
        compared += counts is not None
        if counts is not None and _is_misaligned(*counts):
            misaligned.append(new_path)
        elif row is not None:
            rows.append(row)
    return OutsideChangeScan(_kept(rows, misaligned, compared), tuple(sorted(misaligned)), compared)


def _file_result(
    path: str, base_fc: FileCoverage, head_fc: FileCoverage | None, diff: FileDiff | None
) -> tuple[IndirectChange | None, tuple[int, int] | None]:
    """``(row or None, (aligned, misaligned) or None when nothing was compared)``."""
    if head_fc is None:
        # Ceiling: dropping a poorly covered file raises the head's figure;
        # this row is the only place that shows why.
        if not base_fc.total_coverable_lines:
            return None, None
        return IndirectChange(path, (), 0, _file_pct(base_fc), None, "no_longer_measured"), None
    row, aligned, astray = _compare(path, base_fc, head_fc, diff)
    return row, (aligned, astray) if aligned + astray else None


def _is_misaligned(aligned: int, astray: int) -> bool:
    return astray >= MISALIGNED_MIN_LINES and astray > MISALIGNED_SHARE * (aligned + astray)


def _kept(
    rows: list[IndirectChange], misaligned: list[str], compared: int
) -> tuple[IndirectChange, ...] | None:
    """*rows*, most newly uncovered lines first; ``None`` when most files are misaligned."""
    if misaligned and len(misaligned) * 2 > compared:
        return None
    return tuple(sorted(rows, key=lambda c: (-c.newly_uncovered_line_count, c.file_path)))


def _compare(
    path: str, base_fc: FileCoverage, head_fc: FileCoverage, diff: FileDiff | None
) -> tuple[IndirectChange | None, int, int]:
    """``(row or None, aligned lines, misaligned lines)`` for *path*'s untouched base lines.

    A line is aligned when it lands on a line the head report counts as
    coverable. A touched file whose line data is identical on both sides by
    raw line number was measured at the head, not the base: all misaligned.
    """
    if diff and diff.hunks and _same_lines(base_fc, head_fc):
        return None, 0, max(len(base_fc.coverable_lines), MISALIGNED_MIN_LINES)
    head_coverable = set(head_fc.coverable_lines)
    pairs = _untouched_lines(base_fc, diff)
    aligned = [(old, new) for old, new in pairs if new in head_coverable]
    lost, gained = _moved(aligned, set(base_fc.covered_lines), set(head_fc.covered_lines))
    row = None
    if lost or gained:
        row = IndirectChange(
            path, line_ranges(lost), gained, _file_pct(base_fc), _file_pct(head_fc), "changed"
        )
    return row, len(aligned), len(pairs) - len(aligned)


def _untouched_lines(base_fc: FileCoverage, diff: FileDiff | None) -> list[tuple[int, int]]:
    """``(base line, head line)`` for each coverable base line the change did not touch."""
    touched = diff.old_ranges if diff else []
    hunks = diff.hunks if diff else []
    return [
        (line, map_old_line(hunks, line) if hunks else line)
        for line in base_fc.coverable_lines
        if not any(a <= line <= b for a, b in touched)
    ]


def _moved(
    pairs: list[tuple[int, int]], base_covered: set[int], head_covered: set[int]
) -> tuple[list[int], int]:
    """``(head lines newly uncovered, count newly covered)`` over aligned *pairs*."""
    lost = [new for old, new in pairs if old in base_covered and new not in head_covered]
    gained = sum(1 for old, new in pairs if new in head_covered and old not in base_covered)
    return lost, gained


def _same_lines(a: FileCoverage, b: FileCoverage) -> bool:
    same_coverable = set(a.coverable_lines) == set(b.coverable_lines)
    return same_coverable and set(a.covered_lines) == set(b.covered_lines)


@dataclass(frozen=True)
class ProjectDelta:
    """Project coverage at the change's base against its head."""

    base: ProjectTotals | None
    head: ProjectTotals | None
    basis: DeltaBasis
    base_commit: str | None = None
    head_commit: str | None = None
    #: The most project coverage may fall, in percentage points; ``None`` ungated.
    max_drop: float | None = None
    #: Why the two measurements cannot be compared; empty when they can.
    incomparable: tuple[str, ...] = ()
    # Ceiling: ``None`` on the history basis, whose stored ingests keep
    # repo-wide figures only, no per-file rows per commit. A base report fills it.
    outside_change: tuple[IndirectChange, ...] | None = None
    #: Why files were left out of ``outside_change`` (``OutsideChangeScan.note``).
    outside_change_note: str | None = None

    def _pcts(self) -> tuple[float | None, float | None]:
        return (self.base.pct if self.base else None, self.head.pct if self.head else None)

    @property
    def delta_pct(self) -> float | None:
        """Head minus base, in points, unrounded; ``None`` without both figures."""
        base, head = self._pcts()
        return None if base is None or head is None else head - base

    @property
    def gate(self) -> GateStatus:
        """``fail`` when the head is more than ``max_drop`` points below the base.

        Small-change tolerance does not apply: a small change can still delete
        the test that ran half the project.
        """
        if self.max_drop is None:
            return "not_set"
        base, head = self._pcts()
        if self.incomparable or base is None or head is None:
            return "no_data"
        # A tolerance, so an exact-boundary drop (99.9 to 99.6 at 0.3) is not
        # failed by float error.
        return "fail" if base - head > self.max_drop + 1e-9 else "pass"

    def to_dict(self) -> dict[str, Any]:
        delta = self.delta_pct
        return {
            "basis": self.basis,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "base": self.base.to_dict() if self.base else None,
            "head": self.head.to_dict() if self.head else None,
            "delta_pct": None if delta is None else round(delta, 2),
            "max_drop": self.max_drop,
            "gate": self.gate,
            "incomparable": list(self.incomparable),
            "outside_change": (
                None
                if self.outside_change is None
                else [c.to_dict() for c in self.outside_change]
            ),
            "outside_change_note": self.outside_change_note,
        }
