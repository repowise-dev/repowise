"""Which symbols a bug fix landed in, and how much of a file's fix mass is recent.

``fix_events`` records the old-side line ranges each fix replaced;
``wiki_symbols`` records where every symbol starts and ends. Intersecting the
two turns "this file was fixed 5 times" into "``persist_graph_edges`` was fixed
5 times", which is the granularity an agent about to edit that function can act
on.

State-free on purpose, like :mod:`signals` and :mod:`trends`: callers pass rows
they already loaded and get plain values back, so the join logic is unit-testable
without a database.

Two honesty rules run through this module.

**Line numbers drift.** Symbol ranges are current-tree; the fix's ranges are from
its own parent commit. Any commit to the file in between can shift them, so an
attribution is only ``exact`` when nothing has touched the file since the fix.
Everything else is ``approximate`` and the surfaces are expected to hedge. This
will be most rows on an active file, which is the truth rather than a defect: the
alternative is re-parsing every historical revision.

**Recency is applied here, not at write time.** Rows are stored undecayed with
their ``committed_at`` (see :class:`~persistence.models.FixEvent`), so the
half-life below is a read-time constant. Changing it re-runs the rollup, never a
reindex.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "BUG_MAGNET_MASS",
    "FIX_HALF_LIFE_DAYS",
    "FixRollup",
    "SymbolSpan",
    "attribute_ranges",
    "decayed_mass",
    "roll_up_file",
]

# G1.4 swept 60 / 90 / 180-day half-lives against the 21-repo calibration corpus.
# Decay was the only lever that moved the ``prior_defect`` coefficient at all
# (0.15 undecayed -> 0.23), and 90d and 180d tied inside noise; 90d is the
# plan's candidate and the tighter of the two, so a file fixed three times last
# month outranks one fixed three times six months ago by ~4x rather than ~2x.
FIX_HALF_LIFE_DAYS = 90.0

# "Bug magnet" means the file carries at least the weight of three fixes landed
# today. Three is the same trigger the PR bot already uses for prior defects, so
# the two surfaces agree on what counts as a lot; decay is what stops a file
# whose fixes all sit at the window's trailing edge from flagging identically to
# one fixed three times this month.
#
# Read the threshold with the decay in mind: because only a same-day fix is
# worth a full 1.0, three real fixes spread over a couple of weeks land near
# 2.9 and do NOT flag. In practice the flag needs four recent fixes, or three
# very recent ones. That is deliberately the conservative end — the flag exists
# to interrupt someone mid-edit, so it should be rare enough to be worth reading.
BUG_MAGNET_MASS = 3.0

_DAY_SECONDS = 86400.0


@dataclass(frozen=True)
class SymbolSpan:
    """A symbol's current line span. Mirrors the ``wiki_symbols`` columns used."""

    symbol_id: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class FixRollup:
    """A file's fix history collapsed to what the surfaces read.

    ``fix_mass`` is carried alongside ``bug_magnet`` so the flag is auditable:
    a bare boolean cannot be argued with, and the number behind it is one float.
    """

    fix_count: int
    fix_mass: float
    bug_magnet: bool
    last_fix_at: datetime | None
    symbol_counts: dict[str, int]


def attribute_ranges(
    old_ranges: list[tuple[int, int]] | list[list[int]],
    symbols: list[SymbolSpan],
) -> list[str]:
    """Symbol ids whose current span overlaps any of *old_ranges*.

    A range that cuts across a class and two of its methods attributes to the
    methods only: any selected symbol that strictly contains another selected
    symbol is dropped. That keeps per-symbol counts from double-counting one
    fix at every level of nesting, and it is language-agnostic in a way that
    filtering on ``kind`` would not be.

    Returns ids in file order. Empty when the fix was a pure insertion (no old
    side to intersect) or the file has no parsed symbols.
    """
    if not old_ranges or not symbols:
        return []

    hits: list[SymbolSpan] = []
    for sym in symbols:
        if sym.start_line <= 0 or sym.end_line < sym.start_line:
            continue
        if any(sym.start_line <= int(hi) and int(lo) <= sym.end_line for lo, hi in old_ranges):
            hits.append(sym)

    leaves = [
        sym
        for sym in hits
        if not any(
            other is not sym
            and sym.start_line <= other.start_line
            and other.end_line <= sym.end_line
            and (other.end_line - other.start_line) < (sym.end_line - sym.start_line)
            for other in hits
        )
    ]
    leaves.sort(key=lambda s: (s.start_line, s.end_line))
    return [s.symbol_id for s in leaves]


def attribution_kind(
    symbol_ids: list[str],
    fixed_at: datetime | None,
    file_last_commit_at: datetime | None,
) -> str:
    """``exact`` | ``approximate`` | ``none`` for one attributed event.

    ``exact`` requires that nothing has touched the file since the fix, which is
    the only case where the current symbol spans are the spans the fix saw. On a
    file that has changed since, the attribution is still the best available
    (symbols rarely move far relative to their own size) but it is reported as
    ``approximate`` so nothing downstream states it as fact.
    """
    if not symbol_ids:
        return "none"
    if fixed_at is None or file_last_commit_at is None:
        return "approximate"
    return "exact" if fixed_at >= file_last_commit_at else "approximate"


def decayed_mass(fixed_at: datetime | None, as_of: datetime) -> float:
    """Weight of one fix at *as_of*: 1.0 the day it landed, 0.5 a half-life later.

    A fix dated after *as_of* (clock skew in the committer's timezone) is worth
    a fresh fix, never more.
    """
    if fixed_at is None:
        return 0.0
    age_days = (as_of - fixed_at).total_seconds() / _DAY_SECONDS
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / FIX_HALF_LIFE_DAYS)


def roll_up_file(
    events: list[tuple[datetime | None, list[str]]],
    as_of: datetime,
) -> FixRollup:
    """Collapse one file's fix events into its :class:`FixRollup`.

    *events* is ``(committed_at, symbol_ids)`` per event, already filtered to the
    fixes that count (production-code fixes inside the defect window). Callers
    do the filtering because the same rollup shape should not have to know the
    shape vocabulary.
    """
    mass = 0.0
    last: datetime | None = None
    counts: dict[str, int] = {}

    for fixed_at, symbol_ids in events:
        mass += decayed_mass(fixed_at, as_of)
        if fixed_at is not None and (last is None or fixed_at > last):
            last = fixed_at
        for symbol_id in symbol_ids:
            counts[symbol_id] = counts.get(symbol_id, 0) + 1

    return FixRollup(
        fix_count=len(events),
        fix_mass=round(mass, 4),
        bug_magnet=mass >= BUG_MAGNET_MASS,
        last_fix_at=last,
        # Descending count, then symbol id, so the stored order is stable across
        # runs and a surface that shows "top 3" always shows the same three.
        symbol_counts=dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    )
