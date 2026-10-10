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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Wire vocabulary, mirrored in ``packages/types/src/refactoring.ts``.
APPLIED = "applied"
FILE_DELETED = "file_deleted"
SUPERSEDED = "superseded"
TARGET_CHANGED = "target_changed"
UNKNOWN = "unknown"
PAYOFF_OUTCOMES = (APPLIED, FILE_DELETED, SUPERSEDED, TARGET_CHANGED, UNKNOWN)

_JUDGED_TYPES = frozenset({"extract_method"})


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


def target_symbol(
    functions: Mapping[str, FunctionMeasures], name: str
) -> FunctionMeasures | None:
    """The one function in a file's *functions* named *name*, or ``None``.

    Symbol ids are ``path::Outer::name``; two functions sharing a last name
    (methods of two classes) are ambiguous and answer ``None``.
    """
    hits = [m for sid, m in functions.items() if sid.rsplit("::", 1)[-1] == name]
    return hits[0] if len(hits) == 1 else None


def _moved_as_planned(before: int | None, after: int | None, predicted: Any) -> bool:
    """Whether a measure dropped by at least half the predicted amount (at least 1)."""
    if before is None or after is None or not isinstance(predicted, int | float):
        return False
    return before - after >= max(1, math.ceil(predicted / 2))


def classify_payoff(
    *,
    refactoring_type: str,
    target: str,
    evidence: Mapping[str, Any],
    file_live: bool,
    before: Mapping[str, FunctionMeasures] | None,
    after: Mapping[str, FunctionMeasures] | None,
    suggested_name: str | None = None,
    redetected: bool = False,
) -> Payoff:
    """Classify one resolved plan.

    *before* and *after* are the target file's functions by symbol id as the
    store held them before the resolving run and as that run measured them;
    ``None`` when there are none to read. Applied needs the target still there,
    a measure the plan predicted to drop dropped by half the prediction or more,
    and a function the file did not have before (the helper). *redetected* says
    the run emitted a plan of the same kind on the same target under a new id:
    short of applied, that plan was superseded rather than gone.
    """
    if not file_live:
        return Payoff(FILE_DELETED)
    if refactoring_type not in _JUDGED_TYPES or before is None or after is None:
        return Payoff(SUPERSEDED if redetected else UNKNOWN)
    was = target_symbol(before, target)
    now = target_symbol(after, target)
    if now is None:
        # Renamed, removed or split past recognition while the file stayed.
        return Payoff(TARGET_CHANGED if was is not None else UNKNOWN, before=was)
    helper = _new_function(before, after, suggested_name)
    return Payoff(
        _measured_outcome(was, now, evidence, helper is not None, redetected),
        before=was,
        after=now,
        new_symbol=helper,
    )


def _new_function(
    before: Mapping[str, FunctionMeasures],
    after: Mapping[str, FunctionMeasures],
    suggested_name: str | None,
) -> str | None:
    """The function the file gained: the plan's suggested name, else the most complex."""
    fresh = [after[sid] for sid in sorted(set(after) - set(before))]
    named = [m for m in fresh if suggested_name and m.symbol_id.endswith(f"::{suggested_name}")]
    if named:
        return named[0].symbol_id
    if not fresh:
        return None
    return max(fresh, key=lambda m: m.ccn if m.ccn is not None else -1).symbol_id


def _measured_outcome(
    was: FunctionMeasures | None,
    now: FunctionMeasures,
    evidence: Mapping[str, Any],
    has_helper: bool,
    redetected: bool,
) -> str:
    if was is None or was.ccn is None or now.ccn is None:
        return SUPERSEDED if redetected else UNKNOWN
    dropped = _moved_as_planned(
        was.ccn, now.ccn, evidence.get("ccn_removed")
    ) or _moved_as_planned(was.nloc, now.nloc, evidence.get("slice_nloc"))
    if dropped and has_helper:
        return APPLIED
    if redetected:
        return SUPERSEDED
    if (was.ccn, was.nloc, was.params) == (now.ccn, now.nloc, now.params):
        # Nothing measured moved: the detector, not the code, changed its mind.
        return UNKNOWN
    return TARGET_CHANGED
