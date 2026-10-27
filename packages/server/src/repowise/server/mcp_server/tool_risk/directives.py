"""PR-mode directive assembly for get_risk."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.risk_semantics import (
    structural_impact_contract,
)
from repowise.core.analysis.test_reachability import tests_matching_by_name
from repowise.core.analysis.test_selection import (
    Selection,
    is_runnable_test,
    selected_by_change,
)
from repowise.core.co_change import MIN_CO_CHANGE_SUPPORT
from repowise.core.persistence.crud.authority import decision_currencies
from repowise.core.persistence.database import get_session
from repowise.core.persistence.decision_graph import list_conflict_edges
from repowise.core.persistence.models import DecisionNodeLink, DecisionRecord
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _is_workspace_mode,
    filter_path_list,
    is_excluded,
)
from repowise.server.mcp_server._test_selection import (
    UNAVAILABLE_REASON,
    basis_of,
    split_apart,
)
from repowise.server.mcp_server.tool_risk.directives_pr import (
    _WORKSPACE_FAMILIES,  # noqa: F401  re-exported
    APART_FIELDS,
    _as_path,
    _drop_family,
    _project_recommendation,  # noqa: F401  re-exported
    blast_paths,
    breaking_summary,
    cap_lists,
    conformance_summary,
    count_capped_families,
    coverage_fields,
    cross_repo_fields,
    cross_repo_summary,
    drop_withheld_families,
    governance_summary,
    missing_tests,
    next_calls,
    project_recommendations,
    reach_band,
    recommendation_fields,
    tests_summary,
    tests_to_run_fields,
    trim_target_cochanges,
)

#: Caps on the cross-repo directive lists — kept tight so the PR directive stays
#: glanceable. The full impact set is on get_blast_radius / the REST endpoint.
_XR_WILL_BREAK_LIMIT = 5
_XR_COCHANGE_LIMIT = 3
#: Caps on the breaking-change directive — providers and consumers-per-provider.
#: The full report is on GET /api/workspace/breaking-changes.
_BC_PROVIDER_LIMIT = 5
_BC_CONSUMER_LIMIT = 5
#: Caps on the conformance directive — violations and cycles that touch the repo.
#: The full report is on GET /api/workspace/conformance.
_CF_VIOLATION_LIMIT = 5
_CF_CYCLE_LIMIT = 3

#: Cap on the production files in reverse-import reach. Tests reached the same
#: way join ``tests_to_run``.
_MAY_BREAK_LIMIT = 5
#: Cap on the coverage-backed run-list. A validate-this-change set can be longer
#: than the may-break lists (it is what you actually run), but stays glanceable;
#: the overflow and full typed rows live in pr_blast_radius.test_impact.
_TESTS_TO_RUN_LIMIT = 10
#: Run-all reasons carried on the directive; the rest go to the omission store.
_RUN_ALL_REASONS_LIMIT = 3
#: Cap on the edit-list: the test files this change will probably need edited.
_TESTS_TO_UPDATE_LIMIT = 3


def _breaking_change_directive(
    repo_alias: str, collector: OmissionCollector | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Contract-comparison half of the PR directive: incompatibilities and warnings.

    Reads the persisted breaking-change report (current HEAD vs the previously
    indexed contracts), filtered to providers in the changed repo, and reports
    each finding with its endpoint-exposed consumers across repos. Carries every
    contract type the report holds, ``code`` (a published package symbol)
    included. Returns ``(changes, dropped)`` where ``dropped`` counts the
    cross-repo changes the cap left out — a shared-package bump can produce
    more than the cap, and the ids sort by contract type, so silence here would
    read as "nothing else broke". Empty when not in workspace mode or no report
    is available. Never raises.
    """
    out: list[dict[str, Any]] = []
    dropped = 0
    try:
        if not _is_workspace_mode():
            return out, 0
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_breaking_changes", False):
            return out, 0
        for change in enricher.get_breaking_changes_for_repo(repo_alias):
            consumers = change.get("impacted_consumers", [])
            # This directive is cross-repo scoped, so retain only findings with
            # endpoint-exposed consumers outside the provider repository.
            cross = [c for c in consumers if c.get("repo") != repo_alias]
            if not cross:
                continue
            entry = {
                "contract_id": change.get("contract_id"),
                "type": change.get("contract_type"),
                "kind": change.get("kind"),
                "severity": change.get("severity"),
                "detail": change.get("detail"),
                "provider_file": change.get("provider_file"),
                **({"side": side} if (side := change.get("side")) else {}),
                **(
                    {"comparison_source": source}
                    if (source := change.get("comparison_source"))
                    else {}
                ),
                **({"comparison_key": key} if (key := change.get("comparison_key")) else {}),
                **({"field_name": field_name} if (field_name := change.get("field_name")) else {}),
                # The changed symbol itself, when the contract bound to one.
                # It is what the reader passes to get_symbol to see the
                # signature that broke.
                **(
                    {"provider_symbol_id": psid}
                    if (psid := change.get("provider_symbol_id"))
                    else {}
                ),
                "impacted_consumers": [
                    # symbol_id only when the contract bound to one: it is
                    # what the reader can pass to get_symbol, and a null
                    # would just cost budget.
                    {
                        "repo": c.get("repo"),
                        "service": c.get("service"),
                        "file": c.get("file"),
                        **({"symbol_id": sid} if (sid := c.get("symbol_id")) else {}),
                    }
                    for c in cross
                ],
            }
            cap_collection(
                entry,
                "impacted_consumers",
                entry["impacted_consumers"],
                _BC_CONSUMER_LIMIT,
                collector,
                label=(
                    f"breaking_changes.{entry.get('contract_id')}.impacted_consumers "
                    f"beyond cap={_BC_CONSUMER_LIMIT}"
                ),
            )
            out.append(entry)
    except Exception:
        return [], 0
    total = len(out)
    visible = out[:_BC_PROVIDER_LIMIT]
    dropped = total - len(visible)
    if dropped and collector is not None:
        collector.add(
            f"directive.breaking_changes beyond cap={_BC_PROVIDER_LIMIT}",
            out[_BC_PROVIDER_LIMIT:],
        )
    return visible, dropped


def _conformance_directive(
    repo_alias: str,
    collector: OmissionCollector | None = None,
    totals: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Conformance half of the PR directive: architecture findings touching this repo.

    Reads the persisted conformance report (rule violations + dependency cycles
    over the system graph) and returns those that involve the changed repo, so a
    diff that participates in a denied dependency or a circular dependency is
    flagged. Returns two empty lists when not in workspace mode or no report is
    available. Never raises.
    """
    violations: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    try:
        if not _is_workspace_mode():
            return violations, cycles
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_conformance", False):
            return violations, cycles
        scoped = enricher.get_conformance_for_repo(repo_alias)
        for v in scoped.get("violations", []):
            violations.append(
                {
                    "source": v.get("source"),
                    "target": v.get("target"),
                    "rule": f"{v.get('rule_source')} !-> {v.get('rule_target')}",
                    "edge_kind": v.get("edge_kind"),
                    "description": v.get("rule_description") or None,
                }
            )
        for c in scoped.get("cycles", []):
            cycles.append({"nodes": c.get("nodes", []), "length": c.get("length", 0)})
    except Exception:
        return [], []
    if totals is not None:
        totals["conformance_violations"] = len(violations)
        totals["dependency_cycles"] = len(cycles)
    if collector is not None:
        if len(violations) > _CF_VIOLATION_LIMIT:
            collector.add(
                f"directive.conformance_violations beyond cap={_CF_VIOLATION_LIMIT}",
                violations[_CF_VIOLATION_LIMIT:],
            )
        if len(cycles) > _CF_CYCLE_LIMIT:
            collector.add(
                f"directive.dependency_cycles beyond cap={_CF_CYCLE_LIMIT}",
                cycles[_CF_CYCLE_LIMIT:],
            )
    return violations[:_CF_VIOLATION_LIMIT], cycles[:_CF_CYCLE_LIMIT]


def _cross_repo_relationships(
    repo_alias: str, collector: OmissionCollector | None = None
) -> dict[str, Any]:
    """Cross-repo half of the PR directive: downstream services in other repos.

    Resolves the changed repo to its system-graph nodes and ranks reachable
    services in OTHER repos by impact, splitting structural reach (the legacy
    ``will_break_consumers`` field) from behavioral co-change
    (``missing_cochanges``). Structural reach is not runtime-breakage proof.
    Returns two empty lists when not in workspace mode or no system graph is
    available. Never raises.
    """
    unavailable = {
        "structural": [],
        "structural_total": 0,
        "historical": [],
        "historical_total": 0,
        "analysis": {"status": "unavailable", "reason": "system_graph_unavailable"},
    }
    try:
        if not _is_workspace_mode():
            return unavailable
        enricher = _state._cross_repo_enricher
        raw_graph = enricher.get_system_graph() if enricher is not None else None
        if not raw_graph:
            return unavailable

        from repowise.core.workspace.blast_radius import cross_repo_blast_radius
        from repowise.core.workspace.system_graph import SystemGraph

        structural: list[dict[str, Any]] = []
        historical: list[dict[str, Any]] = []
        result = cross_repo_blast_radius(SystemGraph.from_dict(raw_graph), [repo_alias])
        for n in result.impacted:
            if n.repo == repo_alias:
                continue  # cross-repo only — intra-repo impact is the single-repo blast
            if n.structural:
                structural.append(
                    {
                        "repo": n.repo,
                        "service": n.name,
                        "consumer_repository": n.repo,
                        "dependency_repository": repo_alias,
                        "distance": n.distance,
                        "direct": n.distance == 1,
                        "score": n.score,
                        "via": n.edge_kinds,
                        "relationship_type": "structural_dependency",
                        "direction": "consumer_to_dependency",
                        "evidence_kind": "structural",
                        "claim": "structural_reach",
                        "runtime_breakage_claim": False,
                    }
                )
            else:
                historical.append(
                    {
                        "repo": n.repo,
                        "service": n.name,
                        "source_repository": repo_alias,
                        "target_repository": n.repo,
                        "score": n.score,
                        "relationship_type": "co_change",
                        "direction": "undirected",
                        "evidence_kind": "historical",
                        "provenance": "workspace_system_graph",
                    }
                )
        if collector is not None:
            if len(structural) > _XR_WILL_BREAK_LIMIT:
                collector.add(
                    f"directive.will_break_consumers beyond cap={_XR_WILL_BREAK_LIMIT}",
                    structural[_XR_WILL_BREAK_LIMIT:],
                )
            if len(historical) > _XR_COCHANGE_LIMIT:
                collector.add(
                    f"directive.missing_cross_repo_cochanges beyond cap={_XR_COCHANGE_LIMIT}",
                    historical[_XR_COCHANGE_LIMIT:],
                )
        return {
            "structural": structural[:_XR_WILL_BREAK_LIMIT],
            "structural_total": len(structural),
            "historical": historical[:_XR_COCHANGE_LIMIT],
            "historical_total": len(historical),
            "analysis": {
                "status": "partial" if structural else "available",
                "scope": "workspace_system_graph",
                "evidence_resolution": "aggregated_path_edge_kinds",
                "inference_detail": "unavailable",
                "generated_at": raw_graph.get("generated_at"),
                "repo_provenance": raw_graph.get("repo_provenance", {}),
                "freshness": {
                    "status": "unavailable",
                    "reason": "live_repository_heads_not_compared",
                },
                **({"reason": "per_edge_match_provenance_not_retained"} if structural else {}),
                # How far to trust the edge set, and this repo's unlinked calls.
                **(
                    {"graph_evidence": evidence}
                    if (evidence := enricher.graph_evidence_brief())
                    else {}
                ),
                **(
                    {"http_calls": split} if (split := enricher.http_call_split(repo_alias)) else {}
                ),
            },
        }
    except Exception:
        return {
            **unavailable,
            "analysis": {"status": "degraded", "reason": "analysis_failed"},
        }


def _cross_repo_directive(repo_alias: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compatibility wrapper returning the two legacy directive lists."""
    relationships = _cross_repo_relationships(repo_alias)
    return relationships["structural"], relationships["historical"]


def _trim_blast_lists(
    pr_blast_radius: dict[str, Any],
    exclude_spec: Any,
    collector: OmissionCollector | None = None,
    *,
    full_scale: bool = False,
) -> dict[str, Any]:
    """Cap the noisy ``pr_blast_radius`` lists, capturing what gets dropped.

    ``pr_blast_radius`` is the analyzer's own payload — preserve it for
    callers that want the full picture, but drop excluded paths and truncate
    the noisy lists so we stay well under the 25k-token transport ceiling on
    PRs that touch many files. With a *collector*, every entry trimmed for
    size is persisted to the omission store (excluded paths are not — they
    are filtered by policy, not budget).
    """
    trimmed_blast: dict[str, Any] = dict(pr_blast_radius)
    # The structural score is uncalibrated and never sees the diff, so the MCP
    # reply carries only its band (``directive.reach``). The number and its
    # scale ride with ``include=["scales"]``; REST and the CLI keep the alias.
    structural_score = trimmed_blast.get("structural_impact_score")
    contract = structural_impact_contract(float(structural_score or 0.0), full_scale=True)
    for key in contract:
        trimmed_blast.pop(key, None)
    if structural_score is not None and full_scale:
        trimmed_blast.update(
            {k: v for k, v in contract.items() if k.startswith("structural_impact_")}
        )
    for key, cap in (
        ("transitive_affected", 15),
        ("cochange_warnings", 10),
        ("test_gaps", 10),
        ("recommended_reviewers", 5),
    ):
        value = trimmed_blast.get(key)
        if not isinstance(value, list):
            continue
        if exclude_spec:
            value = [e for e in value if not is_excluded(_as_path(e), exclude_spec)]
            trimmed_blast[key] = value
        total = len(value)
        if len(value) > cap:
            trimmed_blast[key] = value[:cap]
            trimmed_blast[f"{key}_truncated_total"] = len(value)
            if collector is not None:
                collector.add(
                    f"pr_blast_radius.{key} beyond cap={cap} ({len(value) - cap} dropped)",
                    value[cap:],
                )
        trimmed_blast[f"{key}_total"] = total
        trimmed_blast[f"{key}_emitted"] = len(trimmed_blast.get(key, []))
        trimmed_blast[f"{key}_truncated"] = total > len(trimmed_blast.get(key, []))
    return trimmed_blast


async def _governance_directive(ctx: Any, changed_files: list[str]) -> list[dict[str, Any]]:
    """Governing decisions over *changed_files* that are stale, superseded, or
    contradicted. Bounded to 5 entries. Never raises (returns what it has).
    """
    governance_risk: list[dict[str, Any]] = []
    try:
        async with get_session(ctx.session_factory) as _gr_session:
            _gr_repo = await _get_repo(_gr_session)
            _gr_repo_id = _gr_repo.id
            conflict_edges = await list_conflict_edges(_gr_session, _gr_repo_id)
            conflict_decision_ids: set[str] = set()
            for ce in conflict_edges:
                conflict_decision_ids.add(ce.src_decision_id)
                conflict_decision_ids.add(ce.dst_decision_id)
            linked_rows = list(
                (
                    await _gr_session.execute(
                        select(DecisionRecord, DecisionNodeLink.node_id)
                        .join(
                            DecisionNodeLink,
                            DecisionNodeLink.decision_id == DecisionRecord.id,
                        )
                        .where(
                            DecisionNodeLink.repository_id == _gr_repo_id,
                            DecisionNodeLink.node_id.in_(changed_files),
                        )
                    )
                ).all()
            )
            by_file: dict[str, list[Any]] = {}
            for record, node_id in linked_rows:
                by_file.setdefault(node_id, []).append(record)
            # A directive tells a reviewer their change is constrained, so only
            # an accepted decision may raise one. Without the join a candidate
            # nobody had reviewed produced a stale-governance warning against a
            # pull request.
            currencies = await decision_currencies(
                _gr_session, _gr_repo_id, [r for r, _ in linked_rows]
            )
            seen_dr_ids: set[str] = set()
            for cf in changed_files:
                for dr in by_file.get(cf, []):
                    if dr.id in seen_dr_ids:
                        continue
                    seen_dr_ids.add(dr.id)
                    currency = currencies.get(dr.id)
                    if currency is None:
                        continue
                    reason = _governance_reason(dr, currency, conflict_decision_ids)
                    if reason is None:
                        continue
                    governance_risk.append(
                        {
                            "file": cf,
                            "decision_id": dr.id,
                            "title": dr.title,
                            "status": dr.status,
                            "currency": currency,
                            "reason": reason,
                        }
                    )
    except Exception:
        pass
    return governance_risk


def _governance_reason(dr: Any, currency: str, conflict_decision_ids: set[str]) -> str | None:
    """Map an accepted decision to a directive reason, or None when clean.

    *currency* is the effective currency from the acceptance, so the caller has
    already established the record is a decision rather than a candidate.
    ``needs_review`` is the derived answer to "have the files it names moved",
    which is the staleness test this used to apply to the column by hand.
    """
    if currency == "needs_review":
        return "stale_governance"
    if currency == "superseded":
        return "superseded_decision"
    if dr.id in conflict_decision_ids:
        return "contradicted_decision"
    return None


@dataclass(frozen=True)
class ChangeTests:
    """The selection for the PR's files (``_test_selection``), or why there is none."""

    result: dict[str, Any] = field(default_factory=dict)
    selection: Selection | None = None
    #: Files whose own code imports a changed file (no runner wiring).
    importers: set[str] = field(default_factory=set)
    #: pytest's collection roots, so a production ``test_*.py`` is not a test.
    roots: Any = None
    status: str = "selected"
    message: str = ""


def _tests_to_update(
    changed_files: list[str],
    tests: ChangeTests,
    pr_blast_radius: dict,
    exclude_spec: Any,
) -> list[dict[str, str]]:
    """Test files the change will probably need edited, strongest reason first.

    Only tests the selection runs because of a changed file (not a rule that
    runs them with every subset), that a runner collects, and that the change
    does not already touch. Among those: a test named for a changed file
    (``name_pair``), then one whose own code imports it (``imports``; a
    conftest or setup file reaches tests through runner wiring, not an
    import), then one that changes with it in git history (``co_change``). A
    path keeps its first reason.
    """
    selection = tests.selection
    if selection is None:
        return []
    changed = set(changed_files)
    eligible = set(
        filter_path_list(
            sorted(
                t
                for t in selection.test_files
                if selected_by_change(selection, t)
                and is_runnable_test(t, tests.roots)
                and t not in changed
            ),
            exclude_spec,
        )
    )
    named = tests_matching_by_name(changed_files, sorted(eligible))
    ordered: list[tuple[str | None, str]] = [
        (path, "name_pair")
        for source in changed_files
        if source in named
        for path in (named[source].all_tests or named[source].tests)
    ]
    ordered += [(path, "imports") for path in sorted(tests.importers)]
    # Indexing already drops pairs below the support floor; a row that still
    # records less is weak history, not a reason to edit a test.
    ordered += [
        (_as_path(e), "co_change")
        for e in pr_blast_radius.get("cochange_warnings") or []
        if isinstance(e, dict) and e.get("support", MIN_CO_CHANGE_SUPPORT) >= MIN_CO_CHANGE_SUPPORT
    ]
    rows: dict[str, str] = {}
    for path, reason in ordered:
        if path in eligible and path not in rows:
            rows[path] = reason
    return [{"path": path, "reason": reason} for path, reason in rows.items()]


def _tests_to_run(
    tests: ChangeTests, collector: OmissionCollector
) -> tuple[list[str], str, dict[str, Any]]:
    """``(run list, basis, extra directive fields)`` from the selection.

    The extra fields say whether every test must run and why, and why each
    emitted test is in the list; a failed selection says so and names nothing.
    """
    selection = tests.selection
    if selection is None:
        # Nothing vouches for a subset, so the answer is every test.
        return [], "none", {
            "tests_run_all": True,
            "tests_run_all_reasons": [tests.message, UNAVAILABLE_REASON],
            "tests_status": tests.status,
            "tests_status_reason": tests.message,
        }
    # In run order (``test_ranking``); the tests counted apart still run.
    population, *apart = split_apart(selection)
    counts = dict(zip(APART_FIELDS, apart, strict=True))
    for key, tests in counts.items():
        if tests:
            collector.add(f"directive.{key.removesuffix('_total')} ({len(tests)})", tests)
    reasons = list(selection.reasons)
    if len(reasons) > _RUN_ALL_REASONS_LIMIT:
        collector.add(
            f"directive.tests_run_all_reasons beyond cap={_RUN_ALL_REASONS_LIMIT} "
            f"({len(reasons) - _RUN_ALL_REASONS_LIMIT} dropped)",
            reasons[_RUN_ALL_REASONS_LIMIT:],
        )
    shown = dict.fromkeys(t.split("::", 1)[0] for t in population[:_TESTS_TO_RUN_LIMIT])
    extra: dict[str, Any] = {"tests_run_all": selection.run_all}
    extra.update((key, len(tests)) for key, tests in counts.items() if tests)
    if selection.run_all:
        extra["tests_run_all_reasons"] = reasons[:_RUN_ALL_REASONS_LIMIT]
    extra["tests_to_run_why"] = {p: selection.why[p] for p in shown if p in selection.why}
    return population, basis_of(selection), extra


def _build_pr_directive(
    response: dict,
    pr_blast_radius: dict,
    changed_files: list[str],
    exclude_spec: Any,
    collector: OmissionCollector,
    governance_risk: list[dict[str, Any]],
    test_paths: set[str],
    alias: str,
    *,
    tests: ChangeTests | None = None,
    full_scale: bool = False,
    include_tests: bool = False,
    include_blast: bool = False,
) -> None:
    """Assemble PR-mode output: trim co-change lists + blast radius, then build
    the directive block. Mutates *response* in place. *tests* is the
    selection the run and edit lists come from.
    """
    tests = tests or ChangeTests(status="unknown", message="No test selection was made.")
    # Everything trimmed is persisted via the collector, so the response
    # carries an expandable [repowise#<ref>] marker. Stage order is the order
    # rows reach the collector.
    trim_target_cochanges(response, collector)
    # The blast block mostly repeats the directive, so it ships on request;
    # unrequested, its trimmed rows are not omissions to recover.
    trimmed_blast = _trim_blast_lists(
        pr_blast_radius,
        exclude_spec,
        collector if include_blast else None,
        full_scale=full_scale,
    )
    if include_blast:
        response["pr_blast_radius"] = trimmed_blast

    # "may", not "will": a reverse-import walk over a file list that never sees
    # the diff, so it cannot know whether the symbol an importer uses changed.
    affected = blast_paths(pr_blast_radius.get("transitive_affected", []), exclude_spec)
    all_may_break = [p for p in affected if p not in test_paths]
    may_break = all_may_break[:_MAY_BREAK_LIMIT]
    all_missing_cochanges = blast_paths(pr_blast_radius.get("cochange_warnings", []), exclude_spec)
    all_tests_to_update = _tests_to_update(changed_files, tests, pr_blast_radius, exclude_spec)
    test_impact = pr_blast_radius.get("test_impact") or {}
    all_recommendations = list(test_impact.get("recommendations") or [])
    all_tests_to_run, tests_to_run_basis, tests_fields = _tests_to_run(tests, collector)
    coverage = test_impact.get("coverage") or {}
    gaps = missing_tests(pr_blast_radius, changed_files, coverage, exclude_spec)
    relationships = _cross_repo_relationships(alias, collector)
    breaking_changes, breaking_changes_dropped = _breaking_change_directive(alias, collector)
    conformance_totals: dict[str, int] = {}
    conformance_violations, dependency_cycles = _conformance_directive(
        alias, collector, conformance_totals
    )

    summary = (
        f"PR touches {len(changed_files)} file(s). "
        f"~{len(all_may_break)} downstream file(s) may be affected, "
        f"{len(all_missing_cochanges)} historical co-changer(s) missing, "
        f"{gaps.summary}"
        + tests_summary(
            len(all_tests_to_run),
            tests_to_run_basis,
            tests_fields,
            test_impact,
            include_tests,
            _TESTS_TO_RUN_LIMIT,
        )
        + governance_summary(governance_risk)
        + cross_repo_summary(relationships)
        + breaking_summary(breaking_changes, breaking_changes_dropped)
        + conformance_summary(conformance_violations, dependency_cycles)
    )
    directive = {
        "may_break": may_break,
        "missing_cochanges": all_missing_cochanges[:3],
        "reach": reach_band(pr_blast_radius.get("structural_impact_score")),
        **gaps.fields,
        **tests_to_run_fields(
            all_tests_to_run, tests_to_run_basis, tests_fields, _TESTS_TO_RUN_LIMIT
        ),
        # Tests to edit, not to run; a file can sit in both lists.
        "tests_to_update": all_tests_to_update,
        **(
            recommendation_fields(all_recommendations, _TESTS_TO_RUN_LIMIT)
            if include_tests
            else {}
        ),
        **coverage_fields(test_impact, coverage),
        "test_unknown_files": [],
        **cross_repo_fields(relationships),
        "breaking_changes": breaking_changes,
        "breaking_changes_truncated": breaking_changes_dropped,
        "conformance_violations": conformance_violations,
        "dependency_cycles": dependency_cycles,
        "governance_risk": governance_risk,
        "recommended_reviewers": trimmed_blast.get("recommended_reviewers") or [],
        "next_calls": next_calls(may_break),
        # Totals, not the capped list lengths: "~5" of 24 understates the reach.
        "summary": summary,
    }
    cap_lists(
        directive,
        [
            ("may_break", all_may_break, _MAY_BREAK_LIMIT),
            ("missing_cochanges", all_missing_cochanges, 3),
            ("missing_tests", gaps.population, 3),
            ("tests_to_run", all_tests_to_run, _TESTS_TO_RUN_LIMIT),
            ("tests_to_update", all_tests_to_update, _TESTS_TO_UPDATE_LIMIT),
            *(
                [("test_recommendations", all_recommendations, _TESTS_TO_RUN_LIMIT)]
                if include_tests
                else []
            ),
            (
                "files_without_measured_tests",
                list(test_impact.get("files_without_measured_tests") or []),
                10,
            ),
            ("test_unknown_files", list(test_impact.get("unknown_files") or []), 10),
            ("governance_risk", governance_risk, 5),
        ],
        collector,
    )
    project_recommendations(directive, response)
    count_capped_families(
        directive,
        [
            ("will_break_consumers", relationships["structural_total"]),
            ("missing_cross_repo_cochanges", relationships["historical_total"]),
            ("breaking_changes", len(breaking_changes) + breaking_changes_dropped),
            ("conformance_violations", conformance_totals.get("conformance_violations", 0)),
            ("dependency_cycles", conformance_totals.get("dependency_cycles", 0)),
        ],
    )
    drop_withheld_families(directive, gaps.usable, _is_workspace_mode())
    # The directive carries the reviewers, so the MCP blast copy would repeat
    # them. The REST blast radius is built separately and keeps its field.
    _drop_family(trimmed_blast, "recommended_reviewers")
    response["directive"] = directive
