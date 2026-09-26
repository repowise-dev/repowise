"""Refactoring-plan projection for get_health: validation profiles and plan status."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from repowise.server.mcp_server._budget import (
    OmissionCollector,
    register_post_enforce,
    register_post_shed,
)


def _validation_profile(validation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Deduplicate a plan's repeated tests, targets, and command material."""
    encoded = json.dumps(validation, sort_keys=True, separators=(",", ":"), default=str)
    profile_id = "validation_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]
    tests = list(validation.get("tests") or [])
    targets = list(validation.get("targets") or [])
    profile = {
        key: value
        for key, value in validation.items()
        if key not in {"tests", "targets", "commands", "truncated"}
    }
    profile.update(
        {
            "id": profile_id,
            "tests": tests,
            "tests_total": int(validation.get("total") or len(tests)),
            "tests_emitted": len(tests),
            "targets": targets,
            "commands": list(validation.get("commands") or []),
            "commands_total": len(validation.get("commands") or []),
            "commands_emitted": len(validation.get("commands") or []),
        }
    )
    if profile["tests_emitted"] < profile["tests_total"]:
        profile["tests_reduced_reason"] = "analysis_source_cap"
    return profile_id, profile


def _finding_next_action(finding: Any, repo: str | None) -> dict[str, Any]:
    """Return one concrete source call for a finding without a stored plan."""
    path = str(getattr(finding, "file_path", ""))
    line_start = getattr(finding, "line_start", None)
    line_end = getattr(finding, "line_end", None)
    if path and line_start and line_end:
        arguments: dict[str, Any] = {"symbol_id": f"{path}:{line_start}-{line_end}"}
        if repo:
            arguments["repo"] = repo
        return {"tool": "get_symbol", "arguments": arguments}
    arguments = {"targets": [path], "include": ["skeleton"]}
    if repo:
        arguments["repo"] = repo
    return {"tool": "get_context", "arguments": arguments}


def _refactoring_plans_status(
    *,
    available_plans_total: int,
    plans_emitted: int,
    scoped: bool,
    has_eligible_metrics: bool,
    finding: Any | None,
    repo: str | None,
) -> dict[str, Any]:
    """Explain an explicitly requested plan projection deterministically."""
    if plans_emitted:
        return {"state": "available", "reason": None}
    if available_plans_total:
        return {
            "state": "available_not_emitted",
            "reason": "request_window_empty",
            "message": "Plans exist but the requested limit/cursor window emitted none.",
        }
    if scoped and not has_eligible_metrics:
        return {
            "state": "unavailable",
            "reason": "no_eligible_targets",
            "message": "No requested target resolved to an eligible stored health row.",
        }
    if not has_eligible_metrics:
        return {
            "state": "unavailable",
            "reason": "analysis_unavailable",
            "message": "No stored health analysis is available for this population.",
            "next_action": {
                "command": "repowise update",
                "reason": "run health analysis before interpreting scores or plans",
            },
        }
    if finding is None:
        return {
            "state": "empty",
            "reason": "no_applicable_findings",
            "message": "The eligible population has no applicable open findings.",
        }
    return {
        "state": "indeterminate",
        "reason": "plan_analysis_indeterminate",
        "message": "Findings exist, but stored plan evidence is absent.",
        "possible_causes": [
            "no_supported_structured_transformation",
            "refactoring_detector_disabled_or_failed",
        ],
        "next_action": _finding_next_action(finding, repo),
    }


def _prune_orphaned_validation_profiles(
    result: dict[str, Any], collector: OmissionCollector
) -> None:
    """Drop profiles whose plan the response budget removed.

    A validation profile only means anything next to the plan referencing it,
    so leaving one behind after its plan is shed hands the agent an id that
    resolves to nothing.
    """
    plans = result.get("refactoring_plans")
    profiles = result.get("validation_profiles")
    if not isinstance(plans, list) or not isinstance(profiles, list):
        return
    referenced = {
        plan.get("validation_profile_id")
        for plan in plans
        if isinstance(plan, dict) and plan.get("validation_profile_id")
    }
    kept = [
        profile
        for profile in profiles
        if isinstance(profile, dict) and profile.get("id") in referenced
    ]
    dropped = [profile for profile in profiles if profile not in kept]
    if not dropped:
        return
    collector.add("validation_profiles no longer referenced after response budgeting", dropped)
    result["validation_profiles"] = kept
    result["validation_profiles_emitted"] = len(kept)
    result["validation_profiles_reduced_reason"] = "response_budget"
    result["truncated"] = True


def _reconcile_plan_status(result: dict[str, Any]) -> None:
    """Keep plan availability honest after the final budget mutates collections."""
    status = result.get("refactoring_plans_status")
    plans = result.get("refactoring_plans")
    if not isinstance(status, dict) or status.get("state") != "available":
        return
    if plans is not None and (not isinstance(plans, list) or plans):
        return
    if not result.get("refactoring_plans_total", 0):
        return
    status.update(
        state="available_not_emitted",
        reason="response_budget",
        message="Plans exist but were removed by the final response budget.",
    )


register_post_shed("get_health", _prune_orphaned_validation_profiles)
register_post_enforce("get_health", _reconcile_plan_status)
