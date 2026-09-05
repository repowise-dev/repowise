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
from repowise.server.mcp_server._budget.hooks import run_post_enforce, run_post_shed

DEFAULT_RESPONSE_CHARS = 24_000
EXPANDED_RESPONSE_CHARS = 32_000
_FINAL_HEADROOM_CHARS = 1_200

#: Floor for a long text field the guard trims in place. Below this the excerpt
#: stops being worth reading and the omission ref is the whole answer.
_MIN_KEPT_TEXT_CHARS = 800


@dataclass(frozen=True)
class ResponseBudgetContract:
    """One tool's priority projection under the shared response budget."""

    strategy: Literal["blocks", "targets"]
    shed_order: tuple[str, ...] = ()
    expansion_argument: str | None = "include"
    protected: tuple[str, ...] = ()


#: What a tool gets when it declares no priority of its own. An empty shed
#: order leaves the ordinary pass a no-op; the final size guard is what this
#: buys. Declining to rank your evidence has the guard rank it, and is never a
#: choice to be delivered unbounded.
_DEFAULT_CONTRACT = ResponseBudgetContract("blocks")

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
            # Cheapest loss first. Diff-shape context and history go before the
            # delta and the tests, so what to do survives what the diff weighs.
            "exclude_patterns",
            "change_shape.independent_changes",
            "change_shape",
            "fix_history.files[]",
            "fix_history.files",
            "fix_history",
            "prior_fixes",
            "branch_overlap",
            "cross_repo",
            "impacted_tests",
            "health_delta.limits",
            "health_delta.skipped",
            "health_delta.top_findings[]",
        ),
        protected=(
            "directive",
            "health_delta",
            "classification",
            "risk_percentile",
            "score",
        ),
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
        protected=("answer", "confidence", "citations", "next_action_hint", "degraded"),
    ),
    # Whole-block drops served 0 of 50 pages, 0 of 12 episodes and 0 of 58
    # mined rationale comments across the two modes. Trimming runs to
    # exhaustion before anything is dropped.
    #
    # origin_story and git_archaeology are path mode's; related_documentation is
    # search mode's; code_rationale is set by both.
    "get_why": ResponseBudgetContract(
        "blocks",
        (
            # Titles the decisions lane already carries.
            "origin_story.linked_decisions",
            # Every tail trimmed before any lane is dropped, cheapest loss
            # first. Episodes last here; the path fitter sheds them first for a
            # different reason, documented on _fit_path_response.
            # Candidates before accepted decisions: a candidate is a review
            # request, so losing one to the budget costs the reader nothing
            # they were meant to act on now.
            "history[]",
            "candidates[]",
            "decisions[]",
            "git_archaeology.file_commits[]",
            "git_archaeology.cross_references[]",
            "git_archaeology.git_log[]",
            "related_documentation[]",
            "code_rationale[]",
            "episodes[]",
            # Whole-block drops, only once trimming has run out.
            "git_archaeology.file_commits",
            "git_archaeology.cross_references",
            "git_archaeology.git_log",
            "related_documentation",
            "code_rationale",
            "history",
            "candidates",
            "origin_story",
        ),
        expansion_argument=None,
        protected=(
            "mode",
            "query",
            "path",
            "paths",
            "target_context",
            "alignment",
            "answer_basis",
        ),
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
            "refactoring_opportunities[]",
            "refactoring_summary",
            "performance_opportunities[]",
            "performance_summary",
            "high_leverage_files[]",
            "secondary_rankings",
        ),
        protected=(
            "mode",
            "directive",
            # Both pillar leads are bounded by construction and are the only
            # actionable content a bare dashboard carries for them, so shedding
            # one would leave that pillar with counts and nothing to do.
            "performance_directive",
            "refactoring_directive",
            "opportunity_id",
            "model_state",
            "targets",
            "unresolved",
            "known_modules",
            "kpis",
            "distribution",
            "gap_analysis",
        ),
    ),
    # The tool caps source at 600 *lines*, and 600 lines of dense code measured
    # 79k chars — far past the ceiling that line cap was sized against. Callee
    # bodies are context for the root symbol, so they go first. ``source`` is
    # protected so the guard trims it to what fits rather than dropping it: a
    # symbol read with no body is a wasted call even with a recoverable ref.
    "get_symbol": ResponseBudgetContract(
        "blocks",
        ("callee_bodies.callees[]", "candidates[]"),
        expansion_argument=None,
        protected=(
            "source",
            "symbol_id",
            "file",
            "name",
            "qualified_name",
            "signature",
            "kind",
            "start_line",
            "end_line",
            "verified",
            "continuation",
            "continuation_reference",
            "error",
        ),
    ),
    # Same order the tool used, enforced where the size is rechecked after the
    # trust envelope: its own pass reserved 400 chars for a collector and the
    # metadata that followed added roughly 2.6k.
    "search_codebase": ResponseBudgetContract(
        "blocks",
        ("candidates", "results[]"),
        expansion_argument=None,
        protected=("results", "mode", "exact_match"),
    ),
    "get_dead_code": ResponseBudgetContract(
        "blocks",
        # Same order the tool used for its own pass, then the ranked tiers
        # themselves, least-confident first.
        (
            "impact",
            "by_directory",
            "by_owner",
            "tiers.low.findings[]",
            "tiers.medium.findings[]",
            "tiers.high.findings[]",
        ),
        expansion_argument=None,
        protected=("summary", "tiers", "workspace", "error", "finding", "resolved"),
    ),
    # One ranked list. ``total_entry_points`` is derived before shedding, so
    # the count stays exact.
    "get_execution_flows": ResponseBudgetContract(
        "blocks",
        ("flows[]",),
        expansion_argument=None,
        protected=("total_entry_points",),
    ),
    "get_dependency_path": ResponseBudgetContract(
        "blocks",
        (
            "visual_context.shared_neighbors",
            "visual_context.bridge_suggestions",
            "visual_context.nearest_common_ancestors",
            "visual_context",
        ),
        expansion_argument=None,
        protected=("distance", "explanation"),
    ),
    # These three cap their one list at 25 rows by construction and measured
    # under 1.4k. They publish integer ``*_truncated`` counts of their own,
    # which a shed order would overwrite with a bool, so they declare none and
    # ride the size guard.
    "get_architecture": ResponseBudgetContract(
        "blocks",
        ("role_breakdown",),
        expansion_argument=None,
        protected=("summary", "architecture_type", "score", "core_members", "error"),
    ),
    "get_conformance": ResponseBudgetContract(
        "blocks",
        expansion_argument=None,
        protected=("summary", "violation_count", "cycle_count", "total_cycles", "error"),
    ),
    "get_blast_radius": ResponseBudgetContract(
        "blocks",
        ("impact_score_semantics",),
        expansion_argument=None,
        protected=("summary", "targets", "total_impacted", "unresolved_targets"),
    ),
    "list_repos": ResponseBudgetContract(
        "blocks",
        ("repos[]",),
        expansion_argument=None,
        protected=("workspace", "workspace_root", "default_repo"),
    ),
    # No shed order: the enabled path returns generated code whose shape we
    # have not measured, and ranking blocks we have not seen would be a guess.
    # It rides the final size guard until someone measures it.
    "generate_refactoring_code": ResponseBudgetContract(
        "blocks",
        expansion_argument=None,
        protected=("suggestion_id", "resolved", "error"),
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
            # An earlier pass may already have trimmed this list, so its total
            # describes the population, not what is left to drop here.
            total = max(len(value), int(result.get(f"{key}_total") or 0))
            result[f"{key}_total"] = total
            result[f"{key}_emitted"] = 0
            result[f"{key}_omitted"] = total
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
            # Keep as much as the budget actually allows rather than a fixed
            # floor: cutting a 33k body to 800 characters costs the caller the
            # whole read to save bytes the budget never asked for.
            room = limit - (before - len(value)) - len(suffix)
            container[key] = value[: max(_MIN_KEPT_TEXT_CHARS, room)] + suffix
        result["truncated"] = True
        if response_chars(result) >= before:
            return


def enforce_response_budget(
    tool: str,
    result: Any,
    *,
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    repo_root: str | Path | None = None,
) -> Any:
    """Apply *tool*'s contract to its final, metadata-complete result.

    A tool with no declared contract is budgeted under :data:`_DEFAULT_CONTRACT`
    rather than returned untouched: an undeclared priority is a missing shed
    order, never a claim that the response is small.
    """
    if not isinstance(result, dict):
        return result
    contract = _CONTRACTS.get(tool, _DEFAULT_CONTRACT)

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
        run_post_shed(tool, result, collector)
        collector.attach(result)

    if response_chars(result) > limit:
        emergency = OmissionCollector(tool, repo_root=repo_root)
        _emergency_fit(result, contract, emergency, working_limit)
        emergency.attach(result)

    run_post_enforce(tool, result)

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
