"""What happened to a plan that stopped being detected.

The writer resolves a plan nobody detects any more as ``no_longer_detected``,
which on its own conflates a plan someone applied with a file that was deleted,
a function that was rewritten another way, and an edit that moved the plan's
span so it is detected again under a new id. :func:`classify_payoff` tells
them apart from the target function's stored measures before and after the
resolving run, read off ``function_facts`` rather than measured again.

Only Extract Method is judged as applied so far: its target is one function
and its prediction (decision points and code lines moved out) is a measure the
store keeps. Other kinds get ``file_deleted``, ``superseded`` or ``unknown``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ...execution_graph import file_of_symbol

# Wire vocabulary, mirrored in ``packages/types/src/refactoring.ts``.
APPLIED = "applied"
FILE_DELETED = "file_deleted"
SUPERSEDED = "superseded"
TARGET_CHANGED = "target_changed"
UNKNOWN = "unknown"
PAYOFF_OUTCOMES = (APPLIED, FILE_DELETED, SUPERSEDED, TARGET_CHANGED, UNKNOWN)

_JUDGED_TYPES = frozenset({"extract_method"})
# A helper the plan produced holds about the slice: its code lines plus a
# signature and a return, or somewhat less when the edit tidied the span.
_HELPER_MIN_SHARE = 0.5
_HELPER_MAX_SHARE = 2.0
_HELPER_SLACK_LINES = 3


@dataclass(frozen=True)
class FunctionMeasures:
    """One function's stored size measures; ``None`` is unknown, never 0."""

    symbol_id: str
    ccn: int | None = None
    nloc: int | None = None
    params: int | None = None


@dataclass(frozen=True)
class Payoff:
    outcome: str
    before: FunctionMeasures | None = None
    after: FunctionMeasures | None = None
    new_symbol: str | None = None


def measures_by_file(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, FunctionMeasures]]:
    """``function_facts``-shaped rows as each file's functions by symbol id."""
    out: dict[str, dict[str, FunctionMeasures]] = {}
    for row in rows:
        symbol = row["symbol_id"]
        out.setdefault(file_of_symbol(symbol), {})[symbol] = FunctionMeasures(
            symbol, row.get("ccn"), row.get("nloc"), row.get("params")
        )
    return out


def target_symbol(
    functions: Mapping[str, FunctionMeasures], name: str
) -> FunctionMeasures | None:
    """The one function in a file's *functions* named *name*, or ``None``.

    Symbol ids are ``path::Outer::name``; two functions sharing a last name
    (methods of two classes) are ambiguous and answer ``None``.
    """
    hits = [m for sid, m in functions.items() if sid.rsplit("::", 1)[-1] == name]
    return hits[0] if len(hits) == 1 else None


def classify_payoff(
    *,
    refactoring_type: str,
    target: str,
    evidence: Mapping[str, Any],
    file_live: bool | None,
    before: Mapping[str, FunctionMeasures],
    after: Mapping[str, FunctionMeasures],
    suggested_name: str | None = None,
    redetected: bool = False,
) -> Payoff:
    """Classify one resolved plan.

    *before* and *after* are the target file's functions by symbol id as the
    store held them before the resolving run and as that run measured them.
    *file_live* is ``False`` only when the file is gone from disk and git, and
    ``None`` when nobody could tell. *redetected* says the run emitted a plan
    of the same kind on the same target under a new id: short of applied, that
    plan was superseded rather than gone.
    """
    was = target_symbol(before, target)
    if file_live is False:
        return Payoff(FILE_DELETED, before=was)
    fallback = SUPERSEDED if redetected else UNKNOWN
    if refactoring_type not in _JUDGED_TYPES or not after:
        # No measures after the run (an excluded or unscored file, or a run
        # with no graph to key them on) say nothing about the target.
        return Payoff(fallback, before=was)
    now = target_symbol(after, target)
    if now is None:
        # Renamed, removed or split past recognition while the file stayed.
        return Payoff(TARGET_CHANGED if was is not None else fallback, before=was)
    helper = _helper(before, after, suggested_name, evidence.get("slice_nloc"))
    outcome = _measured_outcome(was, now, evidence.get("ccn_removed"), helper is not None)
    if outcome != APPLIED and redetected:
        outcome = SUPERSEDED
    return Payoff(outcome, before=was, after=now, new_symbol=helper)


def _fits_slice(nloc: int | None, slice_nloc: Any) -> bool:
    if nloc is None or not isinstance(slice_nloc, int | float) or slice_nloc <= 0:
        return False
    high = _HELPER_MAX_SHARE * slice_nloc + _HELPER_SLACK_LINES
    return _HELPER_MIN_SHARE * slice_nloc <= nloc <= high


def _helper(
    before: Mapping[str, FunctionMeasures],
    after: Mapping[str, FunctionMeasures],
    suggested_name: str | None,
    slice_nloc: Any,
) -> str | None:
    """The function the file gained that the plan would have produced.

    The plan's suggested name, else the new function whose size fits the
    extracted slice most closely. Any new function is not enough.
    """
    fresh = [after[sid] for sid in sorted(set(after) - set(before))]
    for m in fresh:
        if suggested_name and m.symbol_id.rsplit("::", 1)[-1] == suggested_name:
            return m.symbol_id
    fitting = [m for m in fresh if _fits_slice(m.nloc, slice_nloc)]
    if not fitting:
        return None
    return min(fitting, key=lambda m: abs((m.nloc or 0) - slice_nloc)).symbol_id


def _measured_outcome(
    was: FunctionMeasures | None, now: FunctionMeasures, ccn_removed: Any, has_helper: bool
) -> str:
    """Applied when a helper that fits the slice appeared and the target shed at
    least half the decision points the plan moves out (at least 1)."""
    if was is None or was.ccn is None or now.ccn is None:
        return UNKNOWN
    predicted = ccn_removed if isinstance(ccn_removed, int | float) else None
    if has_helper and predicted and was.ccn - now.ccn >= max(1, math.ceil(predicted / 2)):
        return APPLIED
    if (was.ccn, was.nloc, was.params) == (now.ccn, now.nloc, now.params):
        # Nothing measured moved: the detector, not the code, changed its mind.
        return UNKNOWN
    return TARGET_CHANGED
