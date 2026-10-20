"""The score's payload: include blocks, diagnostics, health order and drill-down."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.change_risk.render import LIMITS, size_text
from repowise.core.analysis.review_directive import review_order

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget.contracts import response_budget_shed_order
from repowise.server.mcp_server._change_health import (
    directive as _directive,
)
from repowise.server.mcp_server._change_health import (
    finding_detail as _finding_detail,
)
from repowise.server.mcp_server._change_health import (
    health_delta_block as _health_delta_block,
)
from repowise.server.mcp_server._change_health import (
    patch_coverage_action as _patch_coverage_action,
)
from repowise.server.mcp_server._helpers import (
    resolve_enum_argument,
)

# Compatibility projection for direct callers and older tests. The shared
# response contract remains the single source of truth for this order.
_SHED_ORDER = response_budget_shed_order("get_change_risk")

#: Per-field units and calibration. Identical on every call, so it is opt-in.
#: ``diagnostics`` carries the raw model mechanics; ``findings`` lifts the
#: top-findings cap. Both are projections, recoverable by an exact call.
#: ``evidence`` adds the change's evidence checklist, the record ``repowise
#: changes show`` and the REST change detail serve. ``tests`` adds why each
#: listed test runs and the evidence that decided each changed file.
_INCLUDE_BLOCKS = frozenset({"scales", "diagnostics", "findings", "evidence", "tests"})

#: Score mechanics that are identical or near-identical on every call, behind
#: ``include=["diagnostics"]`` so the action-first blocks lead. ``score`` and
#: ``fallback_band`` sit here: the percentile already carries the ranking, and
#: the raw number reads as a danger verdict it is not.
_DIAGNOSTIC_FIELDS = (
    "risk_authority",
    "score_measures",
    "score_unit",
    "baseline_sample_size",
    "features",
    "drivers",
    "score",
    "fallback_band",
)

#: Comparison mechanics on ``health_delta``: the analyzer fingerprint, the two
#: resolved revisions and the fixed limits text. Behind ``diagnostics`` too.
_HEALTH_DIAGNOSTIC_FIELDS = ("analyzer", "base", "head", "limits")

#: ``classification`` labels for the MCP surface. ``review_priority`` is a
#: tercile of the diff-shape rank, so the label names size, not a verdict.
#: Both ship only under ``include=["scales"]``: ``risk_percentile`` is the one
#: rank on the default response.
_DIFF_SIZE_LABELS = {
    "low": "Below-typical diff size",
    "moderate": "Typical diff size",
    "high": "Above-typical diff size",
}


def _path_patterns(value: list[str] | str | None) -> tuple[str, ...]:
    """A list of patterns, or one comma-separated string, as a pattern tuple.

    ``tuple("src/")`` would be one pattern per character, which matches nothing.
    """
    items = value.split(",") if isinstance(value, str) else value or ()
    return tuple(item.strip() for item in items if item.strip())


def _include_blocks(include: list[str] | None, ignored: list[dict]) -> set[str]:
    """The valid ``include`` blocks; unknown ones are recorded in ``ignored``."""
    return {
        block
        for block in (include or [])
        if resolve_enum_argument(block, _INCLUDE_BLOCKS, argument="include", ignored=ignored)
    }


def _shape_payload(result: Any, include_set: set[str], revspec: str | None) -> tuple[dict, dict]:
    """The score's payload trimmed to what the request asked for.

    Returns ``(payload, diagnostics)``; ``diagnostics`` holds the fields moved
    out of the payload when the caller did not ask for them.
    """
    payload = _pkg.change_risk_payload(result, scales="scales" in include_set)
    if "scales" not in include_set:
        payload.pop("review_priority")
        payload.pop("classification")
    elif payload.get("review_priority") in _DIFF_SIZE_LABELS:
        payload["classification"] = _DIFF_SIZE_LABELS[payload["review_priority"]]
    if "diagnostics" not in include_set:
        diagnostics = {f: payload.pop(f) for f in _DIAGNOSTIC_FIELDS if f in payload}
        # The fix record reads as files and counts; its rank is a second
        # percentile beside ``risk_percentile`` and only invited comparing them.
        payload["fix_history"].pop("percentile", None)
        payload["fix_history"].pop("density", None)
    else:
        diagnostics = {}
    # Fields that only repeat the request or say nothing: an empty exclude list,
    # a working-tree flag after an explicit revspec, a false ``is_fix``.
    if not payload["exclude_patterns"]:
        del payload["exclude_patterns"]
    if revspec is not None:
        del payload["working_tree"]
    if not payload["is_fix"]:
        del payload["is_fix"]
    return payload, diagnostics


def _attach_health(
    payload: dict, delta: Any, revspec: str | None, *, expand: bool, diagnostics: bool = False
) -> None:
    """Put the directive first and the compact delta second."""
    block = _health_delta_block(delta, revspec=revspec, full=diagnostics)
    if not diagnostics:
        for key in _HEALTH_DIAGNOSTIC_FIELDS:
            block.pop(key, None)
    if expand:
        from repowise.server.mcp_server._change_health import finding_row

        block["top_findings"] = [
            finding_row(f, revspec, inspect=True) for f in review_order(delta.findings)
        ]
        block["findings_emitted"] = len(delta.findings)
        block.pop("findings_reduced_reason", None)
        block.pop("all_findings_via", None)
    directive = _directive(delta, payload.get("impacted_tests"))
    if action := _patch_coverage_action(payload.get("patch_coverage")):
        directive["next_actions"].append(action)
    if payload.get("patch_coverage") and not diagnostics:
        payload["patch_coverage"] = _compact_patch_coverage(payload["patch_coverage"])
    ordered = {
        "directive": directive,
        "health_delta": block,
    }
    for key, value in payload.items():
        ordered[key] = value
    payload.clear()
    payload.update(ordered)


def _gated(block: dict) -> bool:
    """Whether any coverage gate was set for this change, so the block is a verdict."""
    nested = (block.get("risky") or {}, block.get("branches") or {})
    return (
        block.get("gate") not in (None, "not_set")
        or bool(block.get("path_gates"))
        or any(part.get("gate") not in (None, "not_set") for part in nested)
    )


def _compact_patch_coverage(block: dict) -> dict:
    """The block as status, freshness and counts when no current gate reads it.

    Ungated or stale coverage has no verdict to give, so the gate fields read
    null or ``not_set`` and every unmeasured file reads zero; what is left is
    whether anything was measured, how fresh it is, and the measured files.
    ``include=["diagnostics"]`` keeps the whole block.
    """
    scope = block.get("scope") or {}
    freshness = scope.get("freshness")
    gated = _gated(block)
    if gated and freshness == "current":
        return block
    counts = {status: n for status, n in (block.get("file_counts") or {}).items() if n}
    out: dict = {
        "status": "measured" if counts.get("measured") else "not_measured",
        "freshness": freshness,
    }
    if counts.get("measured"):
        for key in ("patch_coverage_pct", "covered_line_count", "coverable_line_count", "files"):
            out[key] = block.get(key)
    if counts:
        out["file_counts"] = counts
    if gated:
        # A configured gate's verdict (often no_data on stale coverage) stays visible.
        out["gate"] = block.get("gate")
        if block.get("path_gates"):
            out["path_gates"] = block["path_gates"]
    if (block.get("branches") or {}).get("partial_line_count"):
        out["branches"] = block["branches"]
    if errors := scope.get("config_errors"):
        out["config_errors"] = errors
    return out


def _diff_shape_sentence(payload: dict, diagnostics: dict) -> str:
    """One line for what the diff-shape rank says, and what it does not."""
    tail = " Mechanics: get_change_risk(include=['diagnostics'])." if diagnostics else ""
    return f"{size_text(payload.get('risk_percentile'))}. {LIMITS}{tail}"


def _drill_down(payload: dict, delta: Any, finding_id: str, revspec: str | None) -> dict:
    """Expand one ephemeral change finding, or say why it is not there."""
    match = next((f for f in delta.findings if f.change_finding_id == finding_id), None)
    if match is None:
        return {
            "error": f"No change finding {finding_id!r} in {payload.get('ref', 'this change')}.",
            "hint": "Ids are scoped to one comparison; re-run without finding_id to list them.",
            "available": [f.change_finding_id for f in review_order(delta.findings)[:10]],
        }
    return {
        "finding": _finding_detail(match, revspec),
        "ref": payload.get("ref"),
        "health_delta_status": delta.status,
    }
