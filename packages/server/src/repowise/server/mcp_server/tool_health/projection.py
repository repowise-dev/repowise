"""The last shaping passes over a get_health response, after every block is built."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.counts import DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE
from repowise.server.mcp_server.tool_health.paging import _stamp_collection
from repowise.server.mcp_server.tool_health.request import HealthRequest

# ``unresolved`` / ``known_modules`` survive any projection, for the same
# reason ``mode`` does. They are the block that stops an empty result
# reading as "this file is healthy" (A1), and projecting them away put a
# typo'd target straight back to silent: ``targets=["does/not/exist.py"],
# only=["metrics"]`` returned ``metrics: []`` and nothing else. A caller
# who has to ask for the error report in order to see it does not have an
# error report.
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
        # A rejected filter value is a caller-error report, so it
        # survives a projection for the same reason ``unresolved`` does.
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

    ``include`` could only ever add blocks, so asking for one extra block
    re-shipped the whole dashboard with it; ``only`` is the subtract half.
    Applied last so it can drop anything above, and ``mode`` / ``_meta`` always
    survive — a response the caller cannot orient in is not a saving.
    """
    if not req.only:
        return result
    # Every capped list's ``*_total`` sibling survives with it. The tool
    # documents "each carries a ``*_total`` sibling so truncation is never
    # silent", and the projection was quietly breaking exactly that promise:
    # ``only=["modules"]`` at ``limit=50`` returned 50 of 116 modules with
    # no ``modules_total`` to say so. Retaining it is not the caller's job —
    # a caller who knew to ask for the total would not need the guarantee.
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
    # Which reading the kept numbers are on, but only once it is not the
    # default: unconditionally would add three empty keys to every other
    # projection.
    if reported_scope != DEFAULT_SCOPE:
        keep |= {"scope"}
    if reported_counts != DEFAULT_COUNTS:
        keep |= {"counts", "unscored_files", "counts_not_applied_to"}
    if "refactoring_plans" in req.only_set:
        keep |= _PLAN_COMPANIONS
    # A key that does not exist in this response is named rather than
    # quietly yielding an empty one — same rule as ``unresolved`` above.
    # A misspelled projection is otherwise indistinguishable from a block
    # the repo genuinely has no data for. Reported against what the caller
    # actually passed, so an alias resolving to a present key is not "unknown".
    unknown = sorted(
        raw for raw, resolved in zip(req.only, req.only_list, strict=True) if resolved not in result
    )
    result = {k: v for k, v in result.items() if k in keep}
    if unknown:
        result["unknown_only_keys"] = unknown
        _stamp_collection(result, "unknown_only_keys", total=len(unknown))
    return result
