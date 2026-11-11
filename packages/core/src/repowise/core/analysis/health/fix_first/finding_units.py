"""Fix-first units for findings with no plan of their own."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from repowise.core.analysis.health.rows import detail_map, field
from repowise.core.analysis.health.scoring import biomarker_dimension, ranked_severity
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.worth import (
    SIZE_MARKERS,
    low_priority,
    magnitude,
    measured_nloc,
    worst_after,
)
from repowise.core.analysis.next_call import ActionCommand

from . import text
from ._files import _action, _Files, _finish_shaped, _health_gain, _ShapeRank, _size_facts
from ._parts import (
    CLASS_MARKERS,
    MAX_FACTS,
    SIZE_BREAK_UP,
    Validate,
    _one_of,
    _risk,
    _tail,
    _Unit,
    _verify,
)
from .model import (
    FIX_IMPROVES,
    FixConfidence,
    FixEffortEstimate,
    FixFact,
    FixSource,
    FixStep,
    FixTarget,
)
from .plan_steps import _above_bar_reason, _evidence, _plan_body, _span, _stage_count
from .refactor_units import _break_up_title, _staged_title


def _finding_rank(finding: Any, files: _Files) -> _ShapeRank:
    path = field(finding, "file_path")
    marker = field(finding, "biomarker_type") or ""
    shape = files.shape(path, field(finding, "function_name"))
    deps = files.dependents(path)
    sized = marker in SIZE_MARKERS
    credited, central = files.reach(path, shape, deps, sized=sized)
    return _ShapeRank(
        shape,
        central,
        files.churning(path),
        changes=files.changes(
            path, field(finding, "function_name"), function=marker not in CLASS_MARKERS
        ),
        cloned=sized and files.cloned(path, shape),
        low=low_priority(marker, shape, error_kind=detail_map(finding).get("kind")),
        gain=files.gain(finding),
        # Breaking up the whole function takes out its whole CCN.
        ccn_removed=shape.get("ccn", 0) if sized else 0,
        dependents=deps,
        credited=credited,
    )


def _finding_unit(lead: Any, files: _Files, first: FixStep, validate: Validate | None) -> _Unit:
    path = field(lead, "file_path")
    marker = field(lead, "biomarker_type") or ""
    function = field(lead, "function_name")
    public_id = field(lead, "public_id")
    rank = _finding_rank(lead, files)
    led = files.extraction(lead)
    after = _plan_after(led, rank.shape, function) if led is not None else None
    if after is None or not _lowers_worst(after, rank.shape):
        led = None

    def fields() -> dict[str, Any]:
        where = function or text.basename(path)
        line = field(lead, "line_start")
        return {
            "title": text.clip(_finding_title(marker, function, where, rank.shape, led)),
            "target": FixTarget(path, function, line, field(lead, "line_end")),
            "why": files.why(
                path,
                measured=text.measured(where, rank.shape),
                fallback=text.problem(marker, where),
                cloned=rank.cloned,
                changes=rank.changes,
                function=function is not None,
            ),
            "facts": tuple(
                [
                    FixFact("finding", text.marker_label(marker)),
                    *_size_facts(rank.shape),
                    FixFact("severity", _severity(marker, field(lead, "severity"), rank.low)),
                    *files.common_facts(path, function=function is not None),
                ][:MAX_FACTS]
            ),
            "action": _action(text.first_sentence(suggestion_for(marker)), (first,), False),
            "gain": _health_gain(rank.gain, ceiling=True),
            "risk": _risk(1, files.dependents(path), function=function is not None),
            "verify": _verify(
                validate(path, function, line, field(lead, "line_end")) if validate else None
            ),
            "context": files.context(path, function),
            **_finding_plan_fields(led, path, public_id),
        }

    return _finish_shaped(
        rank,
        kind="finding",
        source_id=public_id or f"{path}::{marker}::{function or ''}",
        ready=False,
        confidence="medium",
        effort="M",
        improves=_one_of(biomarker_dimension(marker), FIX_IMPROVES, "defect"),
        fields=fields,
    )


def _finding_title(
    marker: str,
    function: str | None,
    where: str,
    shape: Mapping[str, int],
    led: Any = None,
) -> str:
    """A function past the break-up bar reads "Break up", or, when a stored
    plan leads it, the title that plan's own item would have."""
    if magnitude(shape) >= SIZE_BREAK_UP and function and marker in SIZE_MARKERS:
        if led is not None:
            return _led_title(where, shape, led)
        return f"Break up {where} ({text.size_brief(shape)})"
    return text.FINDING_TITLE.get(marker, "Address the finding in {where}").format(where=where)


def _plan_after(plan: Any, shape: Mapping[str, int], function: str | None) -> dict[str, Any] | None:
    """The largest function an Extract Method plan leaves (``worth.worst_after``)."""
    return worst_after(_evidence(plan), _plan_body(plan), _tail(function) or "", shape)


def _lowers_worst(after: Mapping[str, Any], shape: Mapping[str, int]) -> bool:
    """Whether the largest function a plan leaves is smaller than the one it
    lifts from."""
    if shape.get("ccn"):
        return after["ccn"] < shape["ccn"]
    return after["nloc"] < measured_nloc(shape)


#: A led title's caveat when the plan, applied as written, still leaves a
#: function at the break-up bar, as its step says (:func:`_above_bar_reason`).
_MORE_STAGES = " (more stages needed)"


def _led_title(where: str, shape: Mapping[str, int], plan: Any) -> str:
    body = _plan_body(plan)
    suffix = _MORE_STAGES if _above_bar_reason(plan) else ""
    stages = _stage_count(body)
    if stages:
        return _staged_title(where, stages) + suffix
    start, end = _span(plan)
    return _break_up_title(where, shape, f"{start}-{end}", suffix)


def _finding_plan_fields(led: Any, path: str, finding_id: str | None) -> dict[str, Any]:
    """What a finding item says about its plan: none, or the stored Extract
    Method plan its first step lifts (``led``), which the next call opens."""
    findings = (finding_id,) if finding_id else ()
    if led is None:
        return {
            "effort": FixEffortEstimate("M", "not sized; no stored plan covers this finding"),
            "confidence": FixConfidence(
                "medium", "Measured from the code; no stored plan has checked a fix."
            ),
            "source": FixSource(None, (), findings),
            "next_call": _file_findings_call(path),
        }
    plan_id = field(led, "public_id")
    next_call = _file_findings_call(path)
    if plan_id:
        next_call = ActionCommand.call(
            "The stored plan for the first step: its span, helper and checks",
            "get_health",
            {"plan_id": plan_id},
            cli=f"repowise health --plan {plan_id}",
        )
    return {
        # The plan sizes and checks its first lift only, so the item's own
        # effort and confidence stay the finding's.
        "effort": FixEffortEstimate("M", "not sized for the whole fix"),
        "confidence": FixConfidence(
            "medium", "Measured from the code; no plan checks the whole fix."
        ),
        "source": FixSource(None, (plan_id,) if plan_id else (), findings),
        "next_call": next_call,
    }


def _file_findings_call(path: str) -> ActionCommand:
    return ActionCommand.call(
        "Every open finding in the file, with its line and reason",
        "get_health",
        {"targets": [path], "include": ["biomarkers"]},
        cli=f"repowise health --file {path}",
    )


def _severity(marker: str, severity: str | None, low: str | None) -> str:
    """The severity as work is ranked (a code-shape marker tops out at high);
    on a later item it says the tier overrides it."""
    severity = ranked_severity(marker, severity) or "unknown"
    return severity if low is None else f"{severity}; lower priority by shape"
