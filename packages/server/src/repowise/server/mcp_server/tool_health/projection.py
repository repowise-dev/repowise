"""The last shaping passes over a get_health response, after every block is built."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.counts import COUNTS, DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, SCOPES
from repowise.server.mcp_server.tool_health.paging import _stamp_collection
from repowise.server.mcp_server.tool_health.request import HealthRequest

# Orientation and caller-error reports survive any projection: without
# ``unresolved``, a typo'd target would read as an empty, healthy result.
_ALWAYS_KEPT = frozenset(
    {
        "mode",
        "targets",
        "targets_total",
        "targets_emitted",
        "unresolved",
        "unresolved_total",
        "unresolved_emitted",
        "known_modules",
        "known_modules_total",
        "known_modules_emitted",
        "unknown_include_keys",
        "unknown_include_keys_total",
        "unknown_include_keys_emitted",
        # A rejected filter value is a caller-error report too.
        "ignored_arguments",
        "ignored_arguments_hint",
        "recovery",
    }
)

# The miss seen in practice is a dimension or pillar name passed as a key.
_UNKNOWN_ONLY_HINT = (
    "`only` names response keys, such as fix_first, top_findings, "
    "performance_opportunities or refactoring_plans; a dimension such as "
    "performance is an `include` value. See docs/agent/MCP_TOOLS.md."
)

#: Which control accepts each value, to name the right one for a swapped value.
_CONTROL_OF_VALUE = {**dict.fromkeys(COUNTS, "counts"), **dict.fromkeys(SCOPES, "scope")}

_PLAN_COMPANIONS = frozenset(
    {"refactoring_plans_status", "refactoring_plans_opportunities_total"}
)

_ONLY_SUFFIXES = (
    "_total",
    "_emitted",
    "_reduced_reason",
    "_scope",
    "_hidden",
    "_counts",
)

_NON_ONLY_KEYS = (
    _ALWAYS_KEPT
    | _PLAN_COMPANIONS
    | {"_meta", "scope", "counts", "unscored_files", "counts_not_applied_to"}
)


def _name_rejected_controls(
    result: dict[str, Any], req: HealthRequest, reported_scope: str, reported_counts: str
) -> None:
    """A misspelled control falls back to the default, answering a different
    question under the name the caller asked for. The routes and the CLI
    reject outright; MCP cannot, so it names the value it dropped."""
    rejected = {
        name: raw
        for name, raw, resolved in (
            ("scope", req.scope, reported_scope),
            ("counts", req.counts, reported_counts),
        )
        if raw is not None and raw != resolved
    }
    if rejected:
        result["ignored_arguments"] = {**result.get("ignored_arguments", {}), **rejected}
    swapped = [
        f'{name}="{raw}" is a {other} value: did you mean {other}="{raw}"?'
        for name, raw in rejected.items()
        if (other := _CONTROL_OF_VALUE.get(raw)) and other != name
    ]
    if swapped:
        result["ignored_arguments_hint"] = " ".join(swapped)


def _name_uncounted_blocks(result: dict[str, Any], reported_counts: str) -> None:
    """Snapshots and the two ranked queues have no stored split to re-read, so
    they stay calibrated. Named, or a projected headline reads as if
    everything beside it were projected too."""
    if reported_counts != "code_shape":
        return
    unprojected = [
        key
        for key in ("trend", "trends", "refactoring_opportunities", "performance_opportunities")
        if key in result
    ]
    if unprojected:
        result["counts_not_applied_to"] = unprojected


def _unknown_only_hint(result: dict[str, Any]) -> str:
    """In targeted mode the unprojected result is complete, so name the keys
    this call can actually keep; dashboard blocks are built lazily."""
    if result.get("mode") != "targets":
        return _UNKNOWN_ONLY_HINT
    # ``trends`` is gated on ``wants("trends")`` at load time, so include it
    # beside whatever blocks this targeted result built.
    keys = [
        k
        for k in dict.fromkeys((*result, "trends"))
        if k not in _NON_ONLY_KEYS and not k.endswith(_ONLY_SUFFIXES)
    ]
    return (
        f"`only` names response keys, such as {', '.join(keys)}; a dimension "
        "such as performance is an `include` value. See docs/agent/MCP_TOOLS.md."
    )


def _project(
    result: dict[str, Any], req: HealthRequest, reported_scope: str, reported_counts: str
) -> dict[str, Any]:
    """Keep only what ``only`` names, plus what a caller needs to orient in it.

    ``include`` only adds blocks; ``only`` is the subtract half. Applied last
    so it can drop anything above, while ``mode`` and ``_meta`` always survive.
    """
    if not req.only:
        return result
    # Each kept list keeps its ``*_total`` siblings, so truncation stays visible,
    # and the scope its total is over, with what that scope left out.
    keep = (
        set(req.only_list)
        | _ALWAYS_KEPT
        | {f"{k}{suffix}" for k in req.only_list for suffix in _ONLY_SUFFIXES}
    )
    # Which reading the numbers are on, only when it is not the default.
    if reported_scope != DEFAULT_SCOPE:
        keep |= {"scope"}
    if reported_counts != DEFAULT_COUNTS:
        keep |= {"counts", "unscored_files", "counts_not_applied_to"}
    if "refactoring_plans" in req.only_set:
        keep |= _PLAN_COMPANIONS
    # Name absent keys, so a misspelling is not mistaken for missing data.
    # Reported by what the caller passed, so a resolved alias is not "unknown".
    unknown = sorted(
        raw for raw, resolved in zip(req.only, req.only_list, strict=True) if resolved not in result
    )
    hint = _unknown_only_hint(result)
    result = {k: v for k, v in result.items() if k in keep}
    if unknown:
        result["unknown_only_keys"] = unknown
        _stamp_collection(result, "unknown_only_keys", total=len(unknown))
        result["unknown_only_keys_hint"] = hint
    return result
