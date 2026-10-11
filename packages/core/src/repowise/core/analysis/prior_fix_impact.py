"""What the bug-fix record says about the ground a change stands on.

Aggregate only. No inducing commit is named: file-level SZZ measured 74.5%
precision against the frozen judgments, which is enough to count fixes and not
enough to accuse the commit that caused one.

Line overlap is the honest weak signal. A fix's stored ranges are line numbers
on *its own* parent commit while the change's are line numbers now, so any
commit in between shifts them. The count says "this neighbourhood has been
patched before", never "this exact line". The per-file fix count beside it
carries no such caveat, which is why the two are reported separately.

Collection is not here. A caller reads fix events from wherever it holds them --
SQL today, an artifact or an API elsewhere -- and hands them over as
:class:`FixRecord`s. This module only summarizes, so the summary cannot differ
between two callers that read the same events.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .review_directive import EvidenceState

#: Share of a change's counted lines above which one file is said to carry it.
#: A semantic threshold, not a display choice: below it, naming a single file
#: would imply a concentration the numbers do not show.
CONCENTRATION_SHARE = 0.5

#: What the line-overlap count is worth. Stated once, so no surface has to
#: remember to caveat it.
LINE_OVERLAP_BASIS = "approximate"


@dataclass(frozen=True, slots=True)
class FixRecord:
    """One past bug fix's effect on one file, as the summary needs it."""

    fix_sha: str
    file_path: str
    #: Inclusive ``(start, end)`` spans on the fix's own parent commit. Empty
    #: for a pure insertion, which replaced nothing.
    old_ranges: tuple[tuple[int, int], ...] = ()
    committed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PriorFixFile:
    """One changed file's bug-fix past, and how much of the change it holds."""

    file_path: str
    #: Rows for this file, which is one per fixing commit.
    fix_count: int
    #: Changed lines falling inside a past fix's replaced ranges. Approximate;
    #: see :data:`LINE_OVERLAP_BASIS`.
    overlapping_lines: int
    changed_lines: int
    share_of_change: float
    last_fix_days_ago: int | None = None


@dataclass(frozen=True, slots=True)
class PriorFixImpact:
    """The fix record over one change's files, uncapped.

    ``status`` is the point of this type. ``unavailable`` means the record could
    not be read, which must never render the same way as ``available`` with no
    files -- "we could not look" and "nothing has broken here" are opposite
    claims about the same change.
    """

    status: EvidenceState = "available"
    reason: str | None = None
    #: Ordered by overlap, then fix count, then path. Uncapped: a surface caps.
    files: tuple[PriorFixFile, ...] = ()
    #: Distinct fixing commits, not rows. One commit that fixed three of the
    #: changed files is one past fix, not three.
    total_fixes: int = 0
    line_overlap_basis: str = LINE_OVERLAP_BASIS

    @property
    def files_with_fixes(self) -> int:
        return len(self.files)

    @property
    def changed_lines_in_fixed_files(self) -> int:
        return sum(f.changed_lines for f in self.files)

    @property
    def is_empty(self) -> bool:
        return not self.files

    @property
    def dominant_file(self) -> PriorFixFile | None:
        """The fix-carrying file holding most of this change, if one does."""
        return dominant_file(self.files)


def dominant_file(files: Sequence[PriorFixFile]) -> PriorFixFile | None:
    """The fix-carrying file holding most of a change, if one does.

    The score itself is whole-change, so this is the only thing here that says
    *where* the risk sits: the file with both the past and the churn. Exposed
    as a function as well as a property because a surface asks it of the capped
    list it is about to render, so that it never names a file the reader cannot
    find anywhere else in the block.
    """
    if not files:
        return None
    # Negated keys so ties break toward the first file the ordering shows.
    top = min(files, key=lambda f: (-f.share_of_change, -f.fix_count, f.file_path))
    return top if top.share_of_change >= CONCENTRATION_SHARE else None


def unavailable_prior_fixes(reason: str) -> PriorFixImpact:
    """The record exists but could not be read. Not a clean bill."""
    return PriorFixImpact(status="unavailable", reason=reason)


def unsupported_prior_fixes(reason: str) -> PriorFixImpact:
    """Nothing to read: no index, or an index built before fix events existed."""
    return PriorFixImpact(status="unsupported", reason=reason)


def parse_old_ranges(raw: str | None) -> tuple[tuple[int, int], ...]:
    """Inclusive spans from a stored ``old_ranges_json`` value.

    Tolerant on purpose, and it lives here because the tolerance belongs with
    the knowledge of the format. This feeds advisory evidence: one malformed
    row must cost that row, never the whole analysis.
    """
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(span for raw_span in parsed if (span := _span(raw_span)) is not None)


def _span(raw: object) -> tuple[int, int] | None:
    """One stored span, or ``None`` if it is not a usable pair of line numbers.

    A reversed pair is dropped rather than turned the right way round. The
    writer emits ``[start, end]``, so ``[10, 4]`` is a corrupt row and not a
    backwards one -- reordering it would invent a ten-line range the fix may
    never have touched, and this evidence is advisory enough already.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        lo, hi = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None
    return (lo, hi) if lo <= hi else None


def _overlap(changed_lines: set[int], ranges: Sequence[tuple[int, int]]) -> int:
    return sum(1 for line in changed_lines for lo, hi in ranges if lo <= line <= hi)


def _days_ago(moment: datetime | None, now: datetime) -> int | None:
    if moment is None:
        return None
    # A naive timestamp is read as UTC rather than dropped: the column is
    # nullable and historically inconsistent, and "no date" is a weaker answer
    # than a date that is at worst a few hours out.
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return max(0, (now - aware).days)


def summarize_prior_fixes(
    events: Iterable[FixRecord],
    changed: Mapping[str, set[int]],
    *,
    now: datetime | None = None,
) -> PriorFixImpact:
    """Summarize *events* against the lines *changed* touches.

    *changed* maps a changed path to the line numbers this change touches there,
    and is the counted universe: an event for a path outside it is ignored
    rather than allowed to widen the change.

    An empty result with ``status="available"`` is a real answer -- these files
    have no fix record. Callers that could not read the record at all must say
    so through :func:`unavailable_prior_fixes` instead.
    """
    now = now or datetime.now(UTC)
    total_changed = sum(len(lines) for lines in changed.values())

    counts: dict[str, int] = {}
    overlaps: dict[str, int] = {}
    newest: dict[str, int] = {}
    shas: set[str] = set()
    for event in events:
        lines = changed.get(event.file_path)
        if lines is None:
            continue
        shas.add(event.fix_sha)
        counts[event.file_path] = counts.get(event.file_path, 0) + 1
        overlaps[event.file_path] = overlaps.get(event.file_path, 0) + _overlap(
            lines, event.old_ranges
        )
        days = _days_ago(event.committed_at, now)
        if days is not None:
            seen = newest.get(event.file_path)
            newest[event.file_path] = days if seen is None else min(seen, days)

    files = tuple(
        sorted(
            (
                PriorFixFile(
                    file_path=path,
                    fix_count=counts[path],
                    overlapping_lines=overlaps[path],
                    changed_lines=len(changed[path]),
                    share_of_change=(
                        round(len(changed[path]) / total_changed, 3) if total_changed else 0.0
                    ),
                    last_fix_days_ago=newest.get(path),
                )
                for path in counts
            ),
            key=lambda f: (-f.overlapping_lines, -f.fix_count, f.file_path),
        )
    )
    return PriorFixImpact(status="available", files=files, total_fixes=len(shas))
