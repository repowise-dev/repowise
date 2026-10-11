"""Whether a measured problem is worth doing first, or real but lower priority.

Every default list reads this one rule, so a function judged lower priority
here is lower priority everywhere. Code shape decides: how far the function
sits past each detector's bar, and whether its branching is one dispatch on
one value. Git history may order items within a tier; it never lifts an item
into the top one.

A lower-priority problem is still real and stays listed. Its label says why it
can wait and never reads as the next thing to do.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from .complexity.dispatch import DISPATCH_SHARE
from .rows import detail_map, field

#: Problem-size cut points. Each is a multiple of the detector's own bar: CCN
#: 20 is twice the complex_method bar and 40, 80 and 150 double on from it;
#: 100 lines is past the large_method bar and 800 is a module in one body;
#: nesting 5 is one past the nested_complexity bar and 8 is unreadable.
SIZE_CCN = (20, 40, 80, 150)
SIZE_NLOC = (100, 200, 400, 800)
SIZE_NESTING = (5, 6, 8, 99)
#: Findings that measure one function's size.
SIZE_MARKERS = frozenset(
    {"complex_method", "nested_complexity", "brain_method", "large_method", "bumpy_road"}
)
#: How far past its bars (see :func:`worth_size`) a function must sit to be
#: worth doing first: CCN 40, 200 lines or nesting 6. The same size from which
#: an item is titled "break up".
WORTH_MAGNITUDE = 2
#: From this size (CCN 80, 400 lines or nesting 8) a function is worth doing
#: first whatever its branching looks like: a dispatch that big has arms worth
#: splitting out. Length with simple control flow stays lower priority.
EXTREME_MAGNITUDE = 3
#: A function at least this branchy and this deep is worth doing first even
#: under the size bars: tangled, not just long.
TANGLED_CCN = 25
TANGLED_NESTING = 5

LowPriority = Literal[
    "dispatch",
    "chain",
    "near_bar",
    "deep_block",
    "straight",
    "condition",
    "swallow",
    "broad_catch",
    "unwrap",
    "handler",
    "design",
    "history",
    "bounded_loop",
    "unmeasured_cost",
    "not_production",
    "unknown_context",
    "unreached_call",
]

#: Why an item can wait, as the tier reason a surface shows beside it.
LOW_PRIORITY_LABEL: dict[str, str] = {
    "dispatch": (
        "lower priority: most of its branching is one dispatch on one value, "
        "which usually reads fine as written"
    ),
    "chain": "lower priority: one long else-if or ternary chain, deep by count but flat to read",
    "near_bar": "lower priority: past the bar, but under CCN 40, 200 lines and nesting 6",
    "deep_block": "lower priority: deeply nested, but under 100 lines",
    "straight": "lower priority: long, but its control flow is simple",
    "condition": "lower priority: the fix is one condition, not the function",
    "swallow": "lower priority: a swallowed error, which is sometimes deliberate",
    "broad_catch": "lower priority: a broad catch, which may be deliberate at a boundary",
    "unwrap": "lower priority: an unwrap or panic that may be unreachable",
    "handler": "lower priority: error handling at one site",
    "design": "lower priority: a design signal that seldom needs a change on its own",
    "history": "lower priority: it rests on git history alone, not on the code's shape",
    "bounded_loop": "lower priority: the loop is bounded",
    "unmeasured_cost": "lower priority: nothing shows the loop grows with the data",
    "not_production": "lower priority: it runs outside production code",
    "unknown_context": "lower priority: where this code runs is unknown",
    "unreached_call": "lower priority: no entry point is known to reach it",
}

#: Findings about one expression or one error site: real, and small to fix.
_LOCAL_MARKERS: dict[str, LowPriority] = {
    "complex_conditional": "condition",
    "error_handling": "handler",
}
#: Class and signature findings whose fix raters seldom took (the Fix first
#: low-value kinds): real, and a judgment call.
_DESIGN_MARKERS = frozenset({"god_class", "low_cohesion", "primitive_obsession"})
#: An error_handling finding's stored ``kind``, by the reason it can wait.
_ERROR_KINDS: dict[str, LowPriority] = {
    "swallowed_catch": "swallow",
    "go_swallow": "swallow",
    "bare_except": "swallow",
    "broad_except": "broad_catch",
    "unsafe_unwrap": "unwrap",
    "panic_macro": "unwrap",
}


def dormant(facts: Mapping[str, Any]) -> bool:
    """Whether a finding's details, or a measured shape, place it in a function
    a constant-false flag in its own file switches off (``complexity/gating.py``).
    Its work never runs while the flag is off, so it is no work to schedule."""
    return bool(facts.get("gated_off"))


def measure(findings: Iterable[Any]) -> dict[str, int]:
    """A function's largest measured CCN, size and nesting across its findings.

    ``severe`` is set when any of them is critical or a brain method,
    ``deprecated`` when any sits in a function marked deprecated,
    ``gated_off`` when any sits in a :func:`dormant` function,
    ``dispatch_pct`` is the largest stored dispatch share in percent,
    ``start`` / ``end`` span the function as its size findings place it, and
    ``deep_start`` / ``deep_end`` are its deepest nested block's lines.
    """
    shape: dict[str, int] = {}
    for f in findings:
        if field(f, "severity") == "critical" or field(f, "biomarker_type") == "brain_method":
            shape["severe"] = 1
        details = detail_map(f)
        if details.get("deprecated"):
            shape["deprecated"] = 1
        if dormant(details):
            shape["gated_off"] = 1
        deepest = details.get("deepest_block")
        if isinstance(deepest, dict) and deepest.get("start") and "deep_start" not in shape:
            shape["deep_start"] = int(deepest["start"])
            shape["deep_end"] = int(deepest.get("end") or deepest["start"])
        share = details.get("dispatch_share")
        if isinstance(share, (int, float)):
            shape["dispatch_pct"] = max(shape.get("dispatch_pct", 0), round(share * 100))
        if field(f, "biomarker_type") in SIZE_MARKERS:
            start, end = field(f, "line_start"), field(f, "line_end")
            if start and end:
                shape["start"] = min(shape.get("start", start), start)
                shape["end"] = max(shape.get("end", end), end)
        for k in ("ccn", "nloc", "max_nesting", "lcom4", "method_count"):
            v = details.get(k)
            if isinstance(v, (int, float)) and v > shape.get(k, 0):
                shape[k] = int(v)
    return shape


def magnitude(shape: Mapping[str, int]) -> int:
    """How far the measured CCN, size or nesting sits past its bar, 0 to 4."""
    return max(
        sum(shape.get("ccn", 0) >= c for c in SIZE_CCN),
        sum(shape.get("nloc", 0) >= c for c in SIZE_NLOC),
        sum(shape.get("max_nesting", 0) >= c for c in SIZE_NESTING),
    )


def dispatch_shaped(shape: Mapping[str, int]) -> bool:
    """Most of the function's branching is one dispatch on one value."""
    return shape.get("dispatch_pct", 0) >= DISPATCH_SHARE * 100


#: Nesting this share of the CCN or more is one else-if or ternary chain,
#: each arm nested in the last: deep by count, flat to read.
CHAIN_SHARE = 0.8


def _chained(shape: Mapping[str, int]) -> bool:
    ccn = shape.get("ccn", 0)
    return ccn >= SIZE_CCN[0] and shape.get("max_nesting", 0) >= CHAIN_SHARE * ccn


def worth_size(shape: Mapping[str, int]) -> int:
    """:func:`magnitude` with nesting under 8 counted only in a function past
    the length bar: moderate depth in a short function is a small fix."""
    nloc = shape.get("nloc") or 0
    if not nloc and shape.get("start") and shape.get("end"):
        nloc = shape["end"] - shape["start"] + 1
    if nloc >= SIZE_NLOC[0] or shape.get("max_nesting", 0) >= SIZE_NESTING[2]:
        return magnitude(shape)
    return magnitude({**shape, "max_nesting": 0})


def low_priority(
    marker: str | None,
    shape: Mapping[str, int],
    *,
    function_size: bool = False,
    error_kind: str | None = None,
) -> LowPriority | None:
    """Why a problem of kind ``marker`` on a function of ``shape`` can wait,
    or ``None`` when it is worth doing first. ``function_size`` judges any
    marker but a local one as a function-size problem (an Extract Method plan
    answers the function's size, whichever finding led it). ``error_kind`` is
    an error_handling finding's stored ``kind``, which picks its reason.

    A condition or one error site is a local fix. A function-size problem is
    worth doing first when the function is far past a bar
    (:data:`WORTH_MAGNITUDE`) and its branching is not one dispatch, when it is
    both branchy and deep (CCN 25 and nesting 5), or when it is past
    :data:`EXTREME_MAGNITUDE` whatever its branching. An else-if or ternary
    chain is flat however deep it counts; length with CCN under 20 and nesting
    under 5 is a table or a straight sequence, however long. Other kinds keep
    their own rules, so they get ``None``.
    """
    if marker in _DESIGN_MARKERS:
        return "design"
    local = _LOCAL_MARKERS.get(marker or "")
    if local == "handler":
        return _ERROR_KINDS.get(error_kind or "", "handler")
    if local is not None:
        return local
    if marker not in SIZE_MARKERS and not function_size:
        return None
    if not any(shape.get(k) for k in ("ccn", "nloc", "max_nesting")):
        return None  # nothing measured: no grounds to call it small
    return _size_reason(shape)


def _size_reason(shape: Mapping[str, int]) -> LowPriority | None:
    """:func:`low_priority` for a measured function-size problem."""
    if _chained(shape):
        return "chain"
    size = worth_size(shape)
    # Past the bar on length alone, however long: wiring, a table, a sequence.
    simple = shape.get("ccn", 0) < SIZE_CCN[0] and shape.get("max_nesting", 0) < SIZE_NESTING[0]
    if size >= WORTH_MAGNITUDE and simple:
        return "straight"
    if size >= EXTREME_MAGNITUDE:
        return None
    if dispatch_shaped(shape):
        return "dispatch"
    if shape.get("ccn", 0) >= TANGLED_CCN and shape.get("max_nesting", 0) >= TANGLED_NESTING:
        return None
    if size < WORTH_MAGNITUDE:
        return "deep_block" if magnitude(shape) >= WORTH_MAGNITUDE else "near_bar"
    return None


def finding_priorities(findings: Sequence[Any]) -> list[LowPriority | None]:
    """:func:`low_priority` for each finding, in order. A function's shape is
    measured over every finding on it in ``findings``; a finding that rests
    on git history alone is ``history``."""
    from .models import split_by_origin

    history = {id(f) for f in split_by_origin(findings)[1]}
    by_function: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for f in findings:
        if field(f, "function_name"):
            by_function[(field(f, "file_path"), field(f, "function_name"))].append(f)
    out: list[LowPriority | None] = []
    for f in findings:
        if id(f) in history:
            out.append("history")
            continue
        key = (field(f, "file_path"), field(f, "function_name"))
        out.append(
            low_priority(
                field(f, "biomarker_type"),
                measure(by_function.get(key, [f])),
                error_kind=detail_map(f).get("kind"),
            )
        )
    return out


#: Performance causes that cost once per call, not once per loop iteration:
#: they lead when an entry point reaches them.
_PER_CALL_MARKERS = frozenset(
    {
        "hot_path_sync_io",
        "blocking_sync_in_async",
        "blocking_io_under_lock",
        "unbounded_read_reduced_in_memory",
    }
)
#: Causes whose cost grows with the data by their shape alone.
_GROWING_MARKERS = frozenset({"goroutine_in_unbounded_loop", "sql_cartesian_join"})

CostProof = Literal["proven", "unproven"]
COST_PROOFS: tuple[str, ...] = ("proven", "unproven")


def perf_facets(row: Any) -> Mapping[str, Any]:
    """A performance cause's facets, in memory or stored in its details."""
    return field(row, "facets") or detail_map(row).get("facets") or {}


def execution_role(row: Any) -> str:
    """The role running a cause's loop: the stored column, else its facet;
    ``unknown`` on an index stored before roles were. Which role a function
    gets is decided in :mod:`repowise.core.analysis.execution_roles`."""
    return field(row, "execution_role") or perf_facets(row).get("execution_role") or "unknown"


def lead_reason(marker: str | None, facets: Mapping[str, Any]) -> LowPriority | None:
    """Why a performance cause may not lead, or ``None`` when it may.

    The one rule the performance queue, its lead, its rank and Fix first read.
    A cause leads when its loop grows with the data, when it is a per-call
    cause an entry point reaches, or when its shape grows by itself (an
    unbounded goroutine loop, a cartesian join). A cause with no loop
    (magnitude ``n/a``) is judged as a per-call one. ``unmeasured_cost`` is
    the unproven case: nothing measured the loop, so it is a guess, not a cost.
    """
    if marker in _GROWING_MARKERS:
        return None
    magnitude_ = facets.get("loop_magnitude")
    if marker in _PER_CALL_MARKERS or magnitude_ == "n/a":
        return None if facets.get("exposure") == "entry_reachable" else "unreached_call"
    if magnitude_ == "grows_with_data":
        return None
    return "bounded_loop" if magnitude_ == "bounded" else "unmeasured_cost"


def cost_proof(row: Any) -> CostProof:
    """``unproven`` when nothing measured whether the cause's cost grows: kept
    out of the default queue, listed under its own filter."""
    reason = lead_reason(field(row, "biomarker_type"), perf_facets(row))
    return "unproven" if reason == "unmeasured_cost" else "proven"


__all__ = [
    "CHAIN_SHARE",
    "COST_PROOFS",
    "EXTREME_MAGNITUDE",
    "LOW_PRIORITY_LABEL",
    "SIZE_CCN",
    "SIZE_MARKERS",
    "SIZE_NESTING",
    "SIZE_NLOC",
    "TANGLED_CCN",
    "TANGLED_NESTING",
    "WORTH_MAGNITUDE",
    "CostProof",
    "LowPriority",
    "cost_proof",
    "dispatch_shaped",
    "dormant",
    "execution_role",
    "finding_priorities",
    "lead_reason",
    "low_priority",
    "magnitude",
    "measure",
    "perf_facets",
    "worth_size",
]
