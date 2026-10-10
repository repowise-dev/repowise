"""Whether a unit of work belongs in a default queue, and the one reason when not.

Every default list (Fix first, the performance default queue, the refactoring
list's default scope) reads its ladder here and counts what it leaves out with
:class:`Tally`, so a reason means the same thing on every surface and adding
one is a change in one place.

Each ladder returns a :class:`Verdict`. The facts a ladder needs beyond the
row (a function's measured shape, dead code, whether a step is concrete) come
from the caller, which already holds them.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

from repowise.core.code_origin import path_origin

from ...execution_roles import COLD_ROLES, EXECUTION_ROLES
from ..perf.actionability import EXPECTED_REASONS
from ..perf.causal import code_context
from ..rows import detail_map, field
from ..worth import (
    SIZE_MARKERS,
    CostProof,
    LowPriority,
    cost_proof,
    dispatch_shaped,
    dormant,
    lead_reason,
    perf_facets,
)

Reason = Literal[
    "test",
    "tooling",
    "unknown",
    "generated",
    "expected",
    "no_strategy",
    "no_plan",
    "below_min_worth",
    "history_only",
    "vendored",
    "docs_example",
    "deprecated",
    "gated_off",
    "cold_path",
    "unreachable",
    "inherent_dispatch",
    "small_function",
    "no_concrete_step",
    "low_value_kind",
    "kind_unaudited",
    "unmeasured_cost",
    "cold_role",
    "background_unproven",
]
"""Why a unit is out of a default queue. Each surface counts the subset its
ladders can produce (``fix_first.model.FIX_EXCLUSIONS``,
:data:`DEFAULT_QUEUE_EXCLUSIONS`)."""
REASONS: tuple[str, ...] = get_args(Reason)


@dataclass(frozen=True, slots=True)
class Verdict:
    """A unit's eligibility: ``reason`` is ``None`` when it is in the queue."""

    reason: Reason | None = None

    @property
    def eligible(self) -> bool:
        return self.reason is None


ELIGIBLE = Verdict()


def _verdict(reason: str | None) -> Verdict:
    return ELIGIBLE if reason is None else Verdict(reason)  # type: ignore[arg-type]


class Tally:
    """What a queue left out, by reason: the one place exclusions are counted.

    ``reasons`` seeds every key a surface reports, zeros included; a reason
    outside them is still counted. ``dormant`` collects the functions kept out
    as ``gated_off``, by the key the caller names them with.
    """

    __slots__ = ("dormant", "excluded")

    def __init__(self, reasons: Iterable[str] = ()) -> None:
        self.excluded: dict[str, int] = dict.fromkeys(reasons, 0)
        self.dormant: set[Any] = set()

    def add(self, verdict: Verdict, dormant_key: Any = None) -> bool:
        """Count ``verdict`` when it excludes; returns whether it did."""
        reason = verdict.reason
        if reason is None:
            return False
        self.excluded[reason] = self.excluded.get(reason, 0) + 1
        if reason == "gated_off" and dormant_key is not None:
            self.dormant.add(dormant_key)
        return True

    @property
    def total(self) -> int:
        return sum(self.excluded.values())

    def by_reason(self) -> dict[str, int]:
        """Non-zero counts, largest first, ties in the order first counted."""
        return dict(sorted(((r, n) for r, n in self.excluded.items() if n), key=lambda i: -i[1]))


# --- where the code lives ------------------------------------------------------

#: Code origins that do not ship, by the reason each counts as. A build script
#: is tooling to a reader choosing what to fix.
ORIGIN_EXCLUSION: dict[str, Reason] = {
    "test": "test",
    "vendored": "vendored",
    "docs_example": "docs_example",
    "generated": "generated",
    "tooling": "tooling",
    "build": "tooling",
}
#: Reasons that say where the code lives, not whether the work is worth doing:
#: a unit excluded for one of these was never in scope.
SCOPE_EXCLUSIONS = frozenset(ORIGIN_EXCLUSION.values())

# Pure on the path, and the test check dominates a cold Fix first build.
_code_context = functools.lru_cache(maxsize=65536)(code_context)


def path_verdict(
    path: str,
    is_test: bool | None,
    context: str | None = None,
    origin: str | None = None,
    *,
    keep_tests: bool = False,
) -> Verdict:
    """Whether a file's code is in scope.

    The stored ``code_origin`` decides first: it read the file's head, so it
    knows a vendored library or a docs tutorial the path alone does not.
    Then the same classifier on the path (an index stored before the origin
    column, or a build file classed before build files were), then the path
    context. Code-shape work reads :func:`code_context`, where a CLI ships. A
    performance fix passes ``production``: its stored execution context is
    judged by the performance ladder. The stored ``is_test`` flag also marks a
    test. ``keep_tests`` keeps test code in scope (Fix first's ``scope="all"``).
    """
    reason = _path_reason(path, is_test, context, origin)
    if reason == "test" and keep_tests:
        return ELIGIBLE
    return _verdict(reason)


def _path_reason(
    path: str, is_test: bool | None, context: str | None, origin: str | None
) -> Reason | None:
    if origin in ORIGIN_EXCLUSION:
        return ORIGIN_EXCLUSION[origin]
    ctx = context or _code_context(path)
    if is_test or ctx == "test":
        return "test"
    if reason := ORIGIN_EXCLUSION.get(path_origin(path)):
        return reason
    # ``unknown`` under a directory is docs, examples or demos at any depth:
    # code that may not ship. A root-level file stays eligible.
    if ctx == "tooling" or (ctx == "unknown" and "/" in path):
        return "tooling"
    return None


# --- code shape: refactorings and findings --------------------------------------

#: A refactoring whose credited gain is under this is not worth an item. The
#: refactoring model credits only the share of a finding a plan removes and
#: drops trivial spans itself (minimum worth), so this floor is on the file.
MIN_WORTH = 0.5
#: A function-level complexity unit needs this many code lines or this CCN.
#: Picked on the dev labels (67 complexity rows): 30 / 15 drops 13 rejected
#: small functions and 4 accepted ones; no cut that keeps every accepted row
#: drops more than 4 rejected.
SMALL_NLOC = 30
SMALL_CCN = 15
#: Kinds the baseline raters found not worth doing: a refactoring led by one
#: of these steps, or a plan-less finding led by one of these markers, is no
#: candidate (it stays in the refactoring tab). Worth over rater labels, dev
#: repos first, then all 17 baseline repos.
LOW_VALUE_KINDS: dict[str, str] = {
    "low_cohesion": "dev 0/26, all 0/46",
    "large_method": "dev 0/4, all 0/10",
    # Thinly measured: one held-out item (two labels), none on the dev repos.
    # A low-value maintainability nudge that otherwise fills a small repo's
    # whole top three; revisit when more of it is labelled.
    "primitive_obsession": "dev 0/0, all 0/2",
}
#: The same, for one detail ``kind`` of a marker whose other kinds stay. A Rust
#: unwrap or panic is a crash path, not the hidden failure the error-handling
#: item describes, and the raters found it not worth doing first. Both kinds
#: were rated together on ripgrep, fd, serde and mini-redis.
LOW_VALUE_DETAIL_KINDS: dict[tuple[str, str], str] = {
    ("error_handling", "unsafe_unwrap"): "rust all 1/19",
    ("error_handling", "panic_macro"): "rust all 1/19",
}
#: Refactoring kinds served only in the full inventory until each passes a
#: per-language audit; until then a plan they lead is never a queue item. The
#: rater worth that first kept them out, dev repos then all 17 baseline repos.
UNAUDITED_KINDS: dict[str, str] = {
    "extract_class": "dev 0/6, all 0/14",
    "move_method": "dev 0/14, all 0/34",
}


#: A dead-code finding's ``(symbol, start line, end line)``; no symbol for a whole file.
DeadSpan = tuple[str | None, int | None, int | None]
#: A dead-code finding this sure (or marked safe to delete) makes its target
#: ``unreachable``: no plan beats deleting it.
DEAD_CONFIDENCE = 0.8


def dead_spans(rows: Iterable[Any]) -> dict[str, list[DeadSpan]]:
    """Sure, open dead-code findings by file: ``(symbol, start, end)``, the
    symbol ``None`` for an unreachable file."""
    out: dict[str, list[DeadSpan]] = {}
    for row in rows:
        sure = float(field(row, "confidence") or 0.0) >= DEAD_CONFIDENCE or field(
            row, "safe_to_delete"
        )
        whole = field(row, "kind") == "unreachable_file"
        name = None if whole else field(row, "symbol_name")
        if (field(row, "status") or "open") == "open" and sure and (whole or name):
            span = (name, field(row, "start_line"), field(row, "end_line"))
            out.setdefault(field(row, "file_path"), []).append(span)
    return out


def dead_target(
    spans: Mapping[str, Iterable[DeadSpan]], path: str, symbol: str | None, line: int | None
) -> bool:
    """Whether a sure dead-code finding covers ``symbol`` (at ``line``) in
    ``path``: the whole file, the line inside the finding's span, or, for a
    finding stored with no lines, the same name as written (``Old.run`` is
    not ``New.run``)."""
    name_here = symbol.rsplit("::", 1)[-1] if symbol else None
    for name, start, end in spans.get(path, ()):
        if name is None:
            return True
        if start and end:
            if line and start <= line <= end:
                return True
        elif name_here and name == name_here:
            return True
    return False


class UnitFacts(Protocol):
    """What the code-shape ladders read about a function beyond its row."""

    def unreachable(self, path: str, symbol: str | None, line: int | None) -> bool: ...

    def shape(self, path: str, symbol: str | None) -> Mapping[str, int]: ...

    def cloned(self, path: str, shape: Mapping[str, int]) -> bool: ...


def small(shape: Mapping[str, int]) -> bool:
    """Under both size floors. A function whose size findings carry no line
    count is measured by its span, which bounds its code lines from above; one
    with neither is not judged small."""
    nloc = shape.get("nloc")
    if not nloc and shape.get("start") and shape.get("end"):
        nloc = shape["end"] - shape["start"] + 1
    if not nloc:
        return False
    return nloc < SMALL_NLOC and shape.get("ccn", 0) < SMALL_CCN


def unit_verdict(
    facts: UnitFacts, path: str, symbol: str | None, *, complexity: bool, line: int | None = None
) -> Verdict:
    """Whether a unit on ``symbol`` is worth an item.

    Unreachable code is deleted, not fixed. A deprecated function is on its
    way out, and a dormant one does not run. A complexity unit on a function
    that is mostly one dispatch on one value is usually fine as it is, unless
    a duplicate also sits in it; one on a small function is not worth an item.
    """
    if facts.unreachable(path, symbol, line):
        return Verdict("unreachable")
    shape = facts.shape(path, symbol)
    if shape.get("deprecated"):
        return Verdict("deprecated")
    if dormant(shape):
        return Verdict("gated_off")
    if not complexity:
        return ELIGIBLE
    if dispatch_shaped(shape) and not facts.cloned(path, shape):
        return Verdict("inherent_dispatch")
    if small(shape):
        return Verdict("small_function")
    return ELIGIBLE


def refactor_verdict(
    gain: float,
    steps: list[Mapping[str, Any]],
    facts: UnitFacts,
    path: str,
    concrete: Callable[[Mapping[str, Any]], bool],
) -> Verdict:
    """Whether an open refactoring opportunity in scope is a candidate.
    ``concrete(step)`` says whether its lead step names an edit to make."""
    if gain < MIN_WORTH or not steps:
        return Verdict("below_min_worth")
    lead = steps[0]
    kind = lead.get("refactoring_type")
    if kind in UNAUDITED_KINDS:
        return Verdict("kind_unaudited")
    if kind in LOW_VALUE_KINDS:
        return Verdict("low_value_kind")
    verdict = unit_verdict(
        facts,
        path,
        lead.get("target_symbol"),
        complexity=kind == "extract_method",
        line=lead.get("line_start"),
    )
    if verdict.eligible and not concrete(lead):
        return Verdict("no_concrete_step")
    return verdict


def finding_verdict(
    finding: Any, facts: UnitFacts, concrete: Callable[[Any], bool]
) -> Verdict:
    """Whether a finding with no plan is a candidate. ``concrete(finding)``
    says whether it has a first edit to name."""
    marker = field(finding, "biomarker_type")
    if marker in LOW_VALUE_KINDS or (
        (marker, detail_map(finding).get("kind")) in LOW_VALUE_DETAIL_KINDS
    ):
        return Verdict("low_value_kind")
    verdict = unit_verdict(
        facts,
        field(finding, "file_path"),
        field(finding, "function_name"),
        complexity=marker in SIZE_MARKERS,
        line=field(finding, "line_start"),
    )
    if verdict.eligible and not concrete(finding):
        return Verdict("no_concrete_step")
    return verdict


# --- performance ------------------------------------------------------------------

DEFAULT_QUEUE_CONTEXTS = frozenset({"production"})
DEFAULT_QUEUE_STATES = frozenset({"plan_ready", "advisory"})
DEFAULT_QUEUE_PROOFS = frozenset({"proven"})
"""What the performance queue holds when a caller names no filter: production
work with a strategy whose cost is measured.

Test, tooling and unclassified code, ``expected`` repetition, causes with no
supported strategy (``investigate``, whose plan state is always ``no_safe_plan``)
and causes whose loop nobody measured (``worth.cost_proof``) are true and stay one
filter away, but none of them is work to schedule. Each is counted by
:func:`perf_queue_counts` so leaving it out is never silent.
"""

DEFAULT_QUEUE_EXCLUSIONS = (
    "test",
    "tooling",
    "unknown",
    *EXPECTED_REASONS,
    "expected",
    "no_strategy",
    "unmeasured_cost",
    "cold_role",
    "background_unproven",
)
"""Every reason the performance default queue leaves a cause out, in the order
it is checked."""

QUEUE_ROLES = frozenset(EXECUTION_ROLES) - COLD_ROLES
"""Execution roles the performance queue holds; a scheduled job also needs
proven growth. ``unknown`` is no evidence either way, so it stays."""

#: Causes whose cost is real only in shipped code over a loop that grows: a
#: string built in a bounded loop, or in a script, costs nothing a user sees.
GROWS_ONLY_MARKERS = frozenset({"string_concat_in_loop"})


def perf_strategy_verdict(
    item: Any, contexts: frozenset[str] = DEFAULT_QUEUE_CONTEXTS
) -> Verdict:
    """Whether a cause is work to schedule, by context and state.

    Reads an opportunity, an ORM row or a plain row. *contexts* widens it for a
    caller that asked for more (Fix first's ``scope="all"`` keeps test code);
    the state rule never moves. Fix first reads this rather than
    :func:`perf_queue_verdict` because it keeps an unproven cause, as a
    ``later`` item.
    """
    context = field(item, "execution_context")
    if context not in contexts:
        return _verdict(context)
    state = field(item, "actionability_state")
    if state in DEFAULT_QUEUE_STATES:
        return ELIGIBLE
    if state != "expected":
        return Verdict("no_strategy")
    reason = field(item, "actionability_reason") or detail_map(item).get("actionability_reason")
    return _verdict(reason if reason in EXPECTED_REASONS else "expected")


def _role_reason(item: Any) -> Reason | None:
    """Why the role running *item*'s loop keeps it out, or ``None``.

    A loop only startup, a CLI, tooling or tests run is paid once per process.
    A scheduled job's loop matters only once it is shown to grow with the data.
    """
    facets = perf_facets(item)
    role = field(item, "execution_role") or facets.get("execution_role") or "unknown"
    if role in COLD_ROLES:
        return "cold_role"
    if role == "scheduled_job" and facets.get("loop_magnitude") != "grows_with_data":
        return "background_unproven"
    return None


def queue_proof(item: Any) -> CostProof:
    """The stored proof the queue filters on: a background job without proven
    growth is as unproven as an unmeasured loop, and listed beside it."""
    return "unproven" if _role_reason(item) == "background_unproven" else cost_proof(item)


def perf_queue_verdict(item: Any) -> Verdict:
    """Whether the performance default queue holds *item*.

    One reason per cause, context first and role last, so the counts of every
    reason and the queue add up to the whole.
    """
    verdict = perf_strategy_verdict(item)
    if verdict.eligible and cost_proof(item) not in DEFAULT_QUEUE_PROOFS:
        return Verdict("unmeasured_cost")
    return verdict if not verdict.eligible else _verdict(_role_reason(item))


def perf_queue_counts(items: Iterable[Any]) -> dict[str, Any]:
    """The performance default queue's size and what it leaves out, by reason."""
    tally = Tally(DEFAULT_QUEUE_EXCLUSIONS)
    queued = 0
    for item in items:
        if not tally.add(perf_queue_verdict(item)):
            queued += 1
    return {"total": queued, "excluded": tally.excluded}


def perf_low_priority(row: Any) -> LowPriority | None:
    """Why a performance cause can wait: it runs outside production code, or
    :func:`worth.lead_reason` holds it back."""
    context = field(row, "execution_context")
    if context != "production":
        return "unknown_context" if context in (None, "unknown") else "not_production"
    return lead_reason(field(row, "biomarker_type"), perf_facets(row))


def _has_plan(row: Any) -> bool:
    return field(row, "plan_state") == "available" and bool(field(row, "fix_strategy"))


def _perf_worth(row: Any) -> bool:
    """Whether a planned cause is worth an item at all."""
    return field(row, "biomarker_type") not in GROWS_ONLY_MARKERS or perf_low_priority(row) is None


def perf_fix_verdict(
    rows: list[Any], contexts: frozenset[str], unreachable: Callable[[], bool]
) -> tuple[Verdict, list[Any]]:
    """Whether the causes one intervention groups make a Fix first item, and
    the rows worth one.

    The performance queue's context and actionability rule decides which
    causes are work; an unproven cost stays in, tiered later by
    :func:`perf_low_priority`. An item needs a stored plan to quote.
    ``unreachable()`` says whether dead code covers the intervention.
    """
    verdicts = [perf_strategy_verdict(r, contexts) for r in rows]
    queued = [r for r, v in zip(rows, verdicts, strict=True) if v.eligible]
    ready = [r for r in queued if _has_plan(r)]
    if not ready:
        if queued:
            return Verdict("no_plan"), []
        # Dormant only when every row is: one live row speaks for the group.
        live = [v for v in verdicts if v.reason != "gated_off"]
        return (live[0] if live else Verdict("gated_off")), []
    worth = [r for r in ready if _perf_worth(r)]
    if not worth:
        return Verdict("below_min_worth"), []
    if unreachable():
        return Verdict("unreachable"), []
    return ELIGIBLE, worth


__all__ = [
    "DEAD_CONFIDENCE",
    "DEFAULT_QUEUE_CONTEXTS",
    "DEFAULT_QUEUE_EXCLUSIONS",
    "DEFAULT_QUEUE_PROOFS",
    "DEFAULT_QUEUE_STATES",
    "ELIGIBLE",
    "GROWS_ONLY_MARKERS",
    "LOW_VALUE_DETAIL_KINDS",
    "LOW_VALUE_KINDS",
    "MIN_WORTH",
    "ORIGIN_EXCLUSION",
    "QUEUE_ROLES",
    "REASONS",
    "SCOPE_EXCLUSIONS",
    "SMALL_CCN",
    "SMALL_NLOC",
    "UNAUDITED_KINDS",
    "DeadSpan",
    "Reason",
    "Tally",
    "UnitFacts",
    "Verdict",
    "dead_spans",
    "dead_target",
    "finding_verdict",
    "path_verdict",
    "perf_fix_verdict",
    "perf_low_priority",
    "perf_queue_counts",
    "perf_queue_verdict",
    "perf_strategy_verdict",
    "queue_proof",
    "refactor_verdict",
    "small",
    "unit_verdict",
]
