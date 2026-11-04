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

#: Keys built only when ``include`` asks (blocks.py, pillars.py): a missing
#: include, not a misspelling, is why such a key is absent.
_INCLUDE_OF_KEY = {
    **dict.fromkeys(("performance_opportunities", "performance_summary"), "performance"),
    **dict.fromkeys(
        (
            "refactoring_opportunities",
            "refactoring_summary",
            "refactoring_plans",
            "suggestion_legend",
        ),
        "refactoring",
    ),
    **{k: k for k in ("trend", "coverage", "doc_drift", "untested_complexity", "what_if")},
}

#: Which control accepts each value, to name the right one for a swapped value.
_CONTROL_OF_VALUE = {**dict.fromkeys(COUNTS, "counts"), **dict.fromkeys(SCOPES, "scope")}

_PLAN_COMPANIONS = frozenset(
    {"refactoring_plans_status", "refactoring_plans_opportunities_total"}
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
        | {
            suffix
            for k in req.only_list
            for suffix in (
                f"{k}_total",
                f"{k}_emitted",
                f"{k}_reduced_reason",
                f"{k}_scope",
                f"{k}_hidden",
                f"{k}_counts",
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
    # In targeted mode, the keys this call could have kept are the ones in
    # the unprojected result, minus the always-kept orientation keys, _meta,
    # and the *_total / *_emitted (and other) siblings of a kept key. Derived
    # here, before the filter drops them, so a key added later is named
    # without a code change. Dashboard mode keeps the fixed hint below.
    keepable: list[str] | None = None
    if result.get("mode") == "targets":
        keepable = [
            k
            for k in result
            if k not in _ALWAYS_KEPT
            and k != "_meta"
            and k not in ("scope", "counts")
            and not any(
                k.endswith(suffix)
                for suffix in ("_total", "_emitted", "_reduced_reason", "_scope", "_hidden", "_counts")
            )
        ]
        # The targeted mode's data blocks are metrics, findings and trends;
        # trends is absent when there is no trend history, but it is still a
        # key this call could have kept, so name it anyway.
        for canonical in ("metrics", "findings", "trends"):
            if canonical not in keepable:
                keepable.append(canonical)
    result = {k: v for k, v in result.items() if k in keep}
    if unknown:
        result["unknown_only_keys"] = unknown
        _stamp_collection(result, "unknown_only_keys", total=len(unknown))
        result["unknown_only_keys_hint"] = _unknown_only_hint(req, unknown, keepable)
    return result


def _unknown_only_hint(
    req: HealthRequest, unknown: list[str], keepable: list[str] | None = None
) -> str:
    """The hint for an unknown ``only`` key.

    In targeted mode, name the keys this call could have kept, derived from
    the unprojected result — the fixed dashboard examples (fix_first and
    friends) never exist in that mode, so naming them steers the caller
    toward keys that return nothing here. Otherwise the generic hint, led
    by the include each include-gated miss needs.
    """
    if keepable:
        return (
            "`only` names response keys for this targeted call, such as "
            + ", ".join(keepable)
            + ". See docs/agent/MCP_TOOLS.md."
        )
    needs = [
        f'only=["{raw}"] needs include=["{include}"].'
        for raw, resolved in zip(req.only, req.only_list, strict=True)
        if raw in unknown
        and (include := _INCLUDE_OF_KEY.get(resolved))
        and include not in req.include_set
    ]
    return " ".join(dict.fromkeys([*needs, _UNKNOWN_ONLY_HINT]))
