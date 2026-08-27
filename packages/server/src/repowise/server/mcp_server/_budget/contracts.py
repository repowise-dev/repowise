"""Product response budgets enforced at the final MCP result boundary.

Tools decide which evidence is cheapest to lose, but accounting, omission
storage, tier selection, and the final-size check live here. The middleware
calls :func:`enforce_response_budget` after trust metadata and float
quantisation have produced the object the MCP client will receive.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from repowise.server.mcp_server._budget.budgeter import (
    effective_char_budget,
    fit_to_budget,
    response_chars,
    truncate_to_budget,
)
from repowise.server.mcp_server._budget.collector import OmissionCollector

DEFAULT_RESPONSE_CHARS = 24_000
EXPANDED_RESPONSE_CHARS = 32_000
_FINAL_HEADROOM_CHARS = 1_200


@dataclass(frozen=True)
class ResponseBudgetContract:
    """One tool's priority projection under the shared response budget."""

    strategy: Literal["blocks", "targets"]
    shed_order: tuple[str, ...] = ()
    expansion_argument: str | None = "include"
    protected: tuple[str, ...] = ()


_CONTRACTS: dict[str, ResponseBudgetContract] = {
    "get_context": ResponseBudgetContract("targets", protected=("targets",)),
    "get_risk": ResponseBudgetContract(
        "blocks",
        (
            "global_hotspots",
            "pr_blast_radius.guarding_tests",
            "pr_blast_radius",
            "directive.test_recommendations[]",
            "directive.tests_to_run[]",
            "directive.may_break[]",
            "targets[]",
        ),
        protected=("directive", "targets"),
    ),
    "get_change_risk": ResponseBudgetContract(
        "blocks",
        (
            "exclude_patterns",
            "prior_fixes",
            "impacted_tests",
            "cross_repo",
            "fix_history.files[]",
            "fix_history.files",
        ),
        protected=("classification", "risk_percentile", "risk_authority", "score"),
    ),
    "get_answer": ResponseBudgetContract(
        "blocks",
        (
            "episodes",
            "flow_path",
            "retrieval[]",
            "retrieval",
            "code_rationale",
            "quotes",
            "symbol_bodies[]",
            "symbol_bodies",
            "best_guesses[]",
            "best_guesses",
            "candidates[]",
            "candidates",
            "fallback_targets[]",
            "fallback_targets",
        ),
        expansion_argument="include",
        protected=("answer", "confidence", "citations", "next_action_hint"),
    ),
    "get_why": ResponseBudgetContract(
        "blocks",
        (
            "related_documentation",
            "episodes",
            "origin_story.linked_decisions",
            "decisions[]",
            "code_rationale",
            "git_archaeology.file_commits",
            "git_archaeology.cross_references",
            "git_archaeology.git_log",
            "origin_story",
        ),
        expansion_argument=None,
        protected=("mode", "query", "path", "paths", "target_context", "alignment"),
    ),
    "get_overview": ResponseBudgetContract(
        "blocks",
        (
            "cross_repo_topology",
            "tool_surface.opt_in",
            "tool_surface.tools[]",
            "tool_surface.recipes[]",
            "guided_tour_hint",
            "guided_tour",
            "reading_order_hint",
            "reading_order",
            "community_summary",
            "knowledge_map",
            "key_decisions",
            "outline_hint",
            "outline",
            "tool_surface",
            "repos[]",
            "key_modules[]",
            "content_md",
        ),
        protected=("title", "architecture", "entry_points"),
    ),
    "get_health": ResponseBudgetContract(
        "blocks",
        (
            "suggestion_legend",
            "coverage.files[]",
            "trend.recent[]",
            "trend.alerts[]",
            "churn_complexity[]",
            "test_findings[]",
            "top_findings[]",
            "findings[]",
            "worst_files[]",
            "modules[]",
            "trends[]",
            "metrics[]",
            "refactoring_plans[]",
            "performance_opportunities[]",
            "high_leverage_files[]",
            "secondary_rankings",
        ),
        protected=(
            "mode",
            "directive",
            "targets",
            "unresolved",
            "known_modules",
            "kpis",
            "distribution",
            "gap_analysis",
        ),
    ),
}


def response_budget_shed_order(tool: str) -> tuple[str, ...]:
    """Return a tool's shared shed order for compatibility projections."""
    contract = _CONTRACTS.get(tool)
    return contract.shed_order if contract is not None else ()


def _call_uses_expansion(
    contract: ResponseBudgetContract,
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> bool:
    if contract.expansion_argument is None:
        return False
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        return bool(kwargs.get(contract.expansion_argument))
    return bool(bound.arguments.get(contract.expansion_argument))


def _stamp_accounting(result: dict[str, Any], *, limit: int, tier: str) -> None:
    meta = result.setdefault("_meta", {})
    budget = meta.setdefault("response_budget", {})
    budget.update({"limit_chars": limit, "tier": tier, "serialized_chars": 0})
    for _ in range(3):
        measured = response_chars(result)
        if budget["serialized_chars"] == measured:
            break
        budget["serialized_chars"] = measured


async def resolve_response_budget_repo_root(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str | Path | None:
    """Resolve the selected repository so omission refs round-trip by alias."""
    try:
        bound = signature.bind_partial(*args, **kwargs)
        repo = bound.arguments.get("repo")
        if repo == "all":
            from repowise.server.mcp_server import _state

            return getattr(_state, "_workspace_root", None) or getattr(
                _state, "_repo_path", None
            )
        from repowise.server.mcp_server._helpers import _resolve_repo_context

        return (await _resolve_repo_context(repo)).path
    except Exception:
        from repowise.server.mcp_server import _state

        return getattr(_state, "_repo_path", None)


def _emergency_fit(
    result: dict[str, Any],
    contract: ResponseBudgetContract,
    collector: OmissionCollector,
    limit: int,
) -> None:
    """Bound an unexpectedly huge protected core without a false fit claim."""
    protected = {*contract.protected, "_meta"}
    removable = [
        key
        for key in result
        if key not in protected
        and key not in {"truncated", "omission_marker"}
        and not key.endswith(("_total", "_emitted", "_reduced_reason"))
    ]
    for key in sorted(removable, key=lambda item: response_chars(result[item]), reverse=True):
        if response_chars(result) <= limit:
            return
        value = result.pop(key)
        collector.add(f"{key} removed by final budget guard", value)
        if isinstance(value, list):
            result[f"{key}_total"] = len(value)
            result[f"{key}_emitted"] = 0
            result[f"{key}_reduced_reason"] = "response_budget"
        result["truncated"] = True

    while response_chars(result) > limit:
        before = response_chars(result)
        candidates: list[tuple[int, dict[str, Any], str, str, Any]] = []

        def visit(
            value: Any,
            path: str,
            found: list[tuple[int, dict[str, Any], str, str, Any]],
        ) -> None:
            if not isinstance(value, dict):
                return
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if child_path.startswith("_meta") or key.endswith(
                    ("_total", "_emitted", "_reduced_reason")
                ):
                    continue
                if isinstance(child, str) and len(child) > 800:
                    found.append((len(child), value, key, child_path, child))
                elif isinstance(child, list) and child:
                    found.append((response_chars(child), value, key, child_path, child))
                elif isinstance(child, dict):
                    visit(child, child_path, found)

        visit(result, "", candidates)
        if not candidates:
            dictionaries = [
                (response_chars(result[key]), key, result[key])
                for key in contract.protected
                if isinstance(result.get(key), dict) and result[key]
            ]
            if not dictionaries:
                return
            _, key, value = max(dictionaries, key=lambda item: item[0])
            marker = collector.add_inline(
                f"{key} replaced by final budget guard",
                json.dumps(value, separators=(",", ":"), default=str),
            )
            result[key] = {
                "omission_ref": marker,
                "reduced_reason": "response_budget",
            }
            result["truncated"] = True
            if response_chars(result) >= before:
                return
            continue
        _, container, key, path, value = max(candidates, key=lambda item: item[0])
        if isinstance(value, list):
            dropped = value[len(value) // 2 :] if len(value) > 1 else value[:]
            kept = value[: len(value) // 2] if len(value) > 1 else []
            collector.add(f"{path} reduced by final budget guard", dropped)
            container[key] = kept
            container[f"{key}_total"] = max(
                len(value), int(container.get(f"{key}_total") or 0)
            )
            container[f"{key}_emitted"] = len(kept)
            container[f"{key}_reduced_reason"] = "response_budget"
        else:
            marker = collector.add_inline(path, value)
            suffix = f"\n{marker}" if marker else "\n[reduced to fit]"
            container[key] = value[: max(0, 800 - len(suffix))] + suffix
        result["truncated"] = True
        if response_chars(result) >= before:
            return


def _reconcile_health_plan_status(result: dict[str, Any]) -> None:
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


def enforce_response_budget(
    tool: str,
    result: Any,
    *,
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    repo_root: str | Path | None = None,
) -> Any:
    """Apply *tool*'s contract to its final, metadata-complete result."""
    contract = _CONTRACTS.get(tool)
    if contract is None or not isinstance(result, dict):
        return result

    if repo_root is None:
        from repowise.server.mcp_server import _state

        repo_root = getattr(_state, "_repo_path", None)

    expanded = _call_uses_expansion(contract, signature, args, kwargs)
    declared = EXPANDED_RESPONSE_CHARS if expanded else DEFAULT_RESPONSE_CHARS
    limit = effective_char_budget(declared)
    tier = "expanded" if expanded else "default"
    if result.get("truncated"):
        result.setdefault("_meta", {}).setdefault("state", {})["truncated"] = True
    _stamp_accounting(result, limit=limit, tier=tier)

    collector = OmissionCollector(tool, repo_root=repo_root)
    headroom = min(_FINAL_HEADROOM_CHARS, max(100, limit // 4))
    working_limit = max(1, limit - headroom)
    if contract.strategy == "targets":
        truncate_to_budget(
            result,
            char_budget=working_limit,
            collector=collector,
            record_counts=True,
        )
    else:
        fit_to_budget(
            result,
            contract.shed_order,
            collector,
            char_budget=working_limit,
            headroom=0,
            record_counts=True,
        )
        if tool == "get_health":
            plans = result.get("refactoring_plans")
            profiles = result.get("validation_profiles")
            if isinstance(plans, list) and isinstance(profiles, list):
                referenced = {
                    plan.get("validation_profile_id")
                    for plan in plans
                    if isinstance(plan, dict) and plan.get("validation_profile_id")
                }
                kept_profiles = [
                    profile
                    for profile in profiles
                    if isinstance(profile, dict) and profile.get("id") in referenced
                ]
                dropped_profiles = [profile for profile in profiles if profile not in kept_profiles]
                if dropped_profiles:
                    collector.add("validation_profiles no longer referenced after response budgeting", dropped_profiles)
                    result["validation_profiles"] = kept_profiles
                    result["validation_profiles_emitted"] = len(kept_profiles)
                    result["validation_profiles_reduced_reason"] = "response_budget"
                    result["truncated"] = True
        collector.attach(result)

    if response_chars(result) > limit:
        emergency = OmissionCollector(tool, repo_root=repo_root)
        _emergency_fit(result, contract, emergency, working_limit)
        emergency.attach(result)

    if tool == "get_health":
        _reconcile_health_plan_status(result)

    if result.get("truncated"):
        result.setdefault("_meta", {}).setdefault("state", {})["truncated"] = True
    _stamp_accounting(result, limit=limit, tier=tier)
    if response_chars(result) > limit:
        result.setdefault("_meta", {}).setdefault("response_budget", {})[
            "enforcement_error"
        ] = "protected response fields exceed the declared budget"
        _stamp_accounting(result, limit=limit, tier=tier)
    return result


def budgeted_tool_names() -> frozenset[str]:
    """Names covered by the shared product response contract."""
    return frozenset(_CONTRACTS)
