"""The last shaping passes over a get_health response, after every block is built."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.counts import DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE
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
        "recovery",
    }
)

_PLAN_COMPANIONS = frozenset(
    {
        "refactoring_plans_status",
        "validation_profiles",
        "validation_profiles_total",
        "validation_profiles_emitted",
        "validation_profiles_reduced_reason",
    }
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


def _project(
    result: dict[str, Any], req: HealthRequest, reported_scope: str, reported_counts: str
) -> dict[str, Any]:
    """Keep only what ``only`` names, plus what a caller needs to orient in it.

    ``include`` only adds blocks; ``only`` is the subtract half. Applied last
    so it can drop anything above, while ``mode`` and ``_meta`` always survive.
    """
    if not req.only:
        return result
    # Each kept list keeps its ``*_total`` siblings, so truncation stays visible.
    keep = (
        set(req.only_list)
        | _ALWAYS_KEPT
        | {
            suffix
            for k in req.only_list
            for suffix in (
                f"{k}_total",
                f"{k}_emitted",
                f"{k}_reduced_reason",
            )
        }
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
    result = {k: v for k, v in result.items() if k in keep}
    if unknown:
        result["unknown_only_keys"] = unknown
        _stamp_collection(result, "unknown_only_keys", total=len(unknown))
    return result
