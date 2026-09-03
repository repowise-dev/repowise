"""PR-mode directive assembly for get_risk."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.analysis.risk_semantics import structural_impact_contract
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


def _as_path(entry: Any) -> str | None:
    """Best-effort file path from a blast-radius list entry (str or dict)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return (
            entry.get("file_path")
            or entry.get("path")
            or entry.get("file")
            or entry.get("missing_partner")
            or entry.get("partner")
        )
    return None


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

#: Caps on the may-break split. Production impact leads the directive, so it
#: keeps the larger budget; test fallout is a secondary signal capped tighter.
_MAY_BREAK_LIMIT = 5
_MAY_BREAK_TESTS_LIMIT = 3
#: Cap on the coverage-backed run-list. A validate-this-change set can be longer
#: than the may-break lists (it is what you actually run), but stays glanceable;
#: the overflow and full typed rows live in pr_blast_radius.test_impact.
_TESTS_TO_RUN_LIMIT = 10


def _breaking_change_directive(
    repo_alias: str, collector: OmissionCollector | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Breaking-change half of the PR directive: incompatible provider changes.

    Reads the persisted breaking-change report (current HEAD vs the previously
    indexed contracts), filtered to providers in the changed repo, and reports
    each change with the consumers it endangers across repos. Carries every
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
            # Only surface changes that actually endanger a cross-repo consumer —
            # an internal-only removed endpoint isn't a cross-repo break.
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
    # Re-derive so the scale tier follows the caller's include, not the
    # analyzer's default. The legacy field stays an exact alias.
    structural_score = trimmed_blast.get("structural_impact_score")
    if structural_score is not None:
        trimmed_blast.update(
            structural_impact_contract(float(structural_score), full_scale=full_scale)
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


def _governance_reason(
    dr: Any, currency: str, conflict_decision_ids: set[str]
) -> str | None:
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
    full_scale: bool = False,
) -> None:
    """Assemble PR-mode output: trim co-change lists + blast radius, then build
    the directive block. Mutates *response* in place. Behavior preserved.
    """
    # PR mode — drop global_hotspots (irrelevant to a specific diff), trim
    # per-target co-change lists, and synthesize a tight directive the
    # agent can act on without parsing the whole blast-radius dossier.
    # Everything trimmed below is persisted via the collector so the
    # response carries an expandable [repowise#<ref>] marker for it.
    for r in response["targets"].values():
        partners = r.get("co_change_partners") or []
        if len(partners) > 3:
            r["co_change_partners"] = partners[:3]
            collector.add(
                f"{r.get('target')} :: co_change_partners beyond 3",
                partners[3:],
            )
        emitted = len(r.get("co_change_partners") or [])
        total = r.get("co_change_partners_total", emitted)
        r["co_change_partners_emitted"] = emitted
        r["co_change_partners_truncated"] = emitted < total

    trimmed_blast = _trim_blast_lists(
        pr_blast_radius, exclude_spec, collector, full_scale=full_scale
    )
    response["pr_blast_radius"] = trimmed_blast

    # Directive: 3 short lists the agent can read in one glance. Each
    # entry is a file path (string), never a dossier. Designed to answer
    # "what should I do about this PR" in three lines.

    affected = filter_path_list(
        [p for p in (_as_path(e) for e in pr_blast_radius.get("transitive_affected", [])) if p],
        exclude_spec,
    )
    # "may", not "will": this is a reverse-import reachability walk over a file
    # list, and get_risk is never given a diff, so nothing here knows whether the
    # symbol an importer uses actually changed. The diff-backed fields below keep
    # "will".
    all_may_break = [p for p in affected if p not in test_paths]
    all_may_break_tests = [p for p in affected if p in test_paths]
    may_break = all_may_break[:_MAY_BREAK_LIMIT]
    may_break_tests = all_may_break_tests[:_MAY_BREAK_TESTS_LIMIT]

    all_missing_cochanges = filter_path_list(
        [p for p in (_as_path(e) for e in pr_blast_radius.get("cochange_warnings", [])) if p],
        exclude_spec,
    )
    missing_cochanges = all_missing_cochanges[:3]
    # Run-list: consume the analyzer's canonical typed population instead of
    # independently deriving test ids. Every row retains its basis through
    # de-duplication, sorting, exclusions, and the directive cap.
    test_impact = pr_blast_radius.get("test_impact") or {}
    all_recommendations = list(test_impact.get("recommendations") or [])
    test_recommendations = all_recommendations[:_TESTS_TO_RUN_LIMIT]
    test_recommendations_total = len(all_recommendations)
    recommendations_capped = test_recommendations_total > _TESTS_TO_RUN_LIMIT

    # Preserve the measured-first legacy projection and its existing scalar
    # domain. The additive typed rows above are the union of evidence kinds.
    guarding = pr_blast_radius.get("guarding_tests") or {}
    all_tests_to_run = list(guarding.get("tests_to_run") or [])
    tests_to_run_basis = guarding.get("basis") or "none"
    tests_to_run = all_tests_to_run[:_TESTS_TO_RUN_LIMIT]
    tests_to_run_total = len(all_tests_to_run)
    tests_capped = tests_to_run_total > _TESTS_TO_RUN_LIMIT
    if not all_recommendations:
        tests_to_run_suffix = ""
    else:
        basis_totals = test_impact.get("recommendations_by_primary_basis") or {}
        measured_total = int(basis_totals.get("measured", 0))
        inferred_total = int(basis_totals.get("inferred", 0))
        tests_to_run_suffix = (
            f" {test_recommendations_total} test recommendation(s): {measured_total} measured "
            f"and {inferred_total} inferred, not coverage-proven candidate(s); "
            f"each row carries its basis."
        )
    if recommendations_capped:
        tests_to_run_suffix += (
            f" Showing {_TESTS_TO_RUN_LIMIT} of {test_recommendations_total}; omitted "
            "typed rows are captured by the response omission marker."
        )

    # Keep legacy ``missing_tests`` as changed-file test gaps when analysis is
    # usable. The additive ``files_without_measured_tests`` field carries the
    # narrower coverage claim without making older clients misread inferred rows.
    coverage = test_impact.get("coverage") or {}
    coverage_freshness = coverage.get("freshness") or {}
    coverage_usable = coverage.get("status") in {"available", "partial"} and (
        coverage_freshness.get("status") != "stale"
    )
    if coverage_usable:
        full_gap_paths = {
            path
            for path in (_as_path(entry) for entry in pr_blast_radius.get("test_gaps") or [])
            if path and not (exclude_spec and is_excluded(path, exclude_spec))
        }
        all_missing_tests = [path for path in changed_files if path in full_gap_paths]
        missing_tests = all_missing_tests[:3]
        missing_tests_total = len(all_missing_tests)
        missing_tests_summary = (
            f"Showing {len(missing_tests)} of {missing_tests_total} changed-file test gap(s)."
        )
    else:
        missing_tests = []
        missing_tests_total = 0
        missing_tests_summary = (
            f"Coverage analysis is {coverage.get('status', 'unavailable')}; "
            "missing_tests is withheld rather than treated as empty evidence."
        )

    gov_count = len(governance_risk)
    gov_suffix = f" {gov_count} governance risk(s) detected." if gov_count > 0 else ""

    # Cross-repo directive (workspace mode only). Resolve the changed repo to
    # its system-graph nodes and walk reachability to find downstream
    # services in OTHER repos — split structural (will break) from behavioral
    # (co-change only). Repo-scoped: it answers "can this PR's repo break
    # something across a repo boundary?" using the same reachability the map
    # and get_blast_radius use.
    cross_repo_relationships = _cross_repo_relationships(alias, collector)
    will_break_consumers = cross_repo_relationships["structural"]
    missing_cross_repo_cochanges = cross_repo_relationships["historical"]
    will_break_total = cross_repo_relationships["structural_total"]
    missing_cross_repo_total = cross_repo_relationships["historical_total"]
    xr_suffix = ""
    if will_break_consumers or missing_cross_repo_cochanges:
        xr_suffix = (
            f" Cross-repo: showing {len(will_break_consumers)} of {will_break_total} "
            f"consumer service(s) in structural reach, showing "
            f"{len(missing_cross_repo_cochanges)} of {missing_cross_repo_total} "
            f"cross-repo co-changer(s) missing."
        )

    # Breaking-change guard — incompatible provider changes (removed route /
    # field, type change, ...) in this repo and the consumers they endanger.
    # Schema-level truth, distinct from the topology-level will_break_consumers.
    breaking_changes, breaking_changes_dropped = _breaking_change_directive(alias, collector)
    bc_suffix = ""
    if breaking_changes:
        bc_consumers = sum(len(b["impacted_consumers"]) for b in breaking_changes)
        bc_suffix = (
            f" Breaking changes: {len(breaking_changes)} provider contract(s) changed "
            f"incompatibly, endangering {bc_consumers} consumer(s)."
        )
        if breaking_changes_dropped:
            bc_suffix += f" {breaking_changes_dropped} more not listed."

    # Architecture conformance — declared dependency-rule violations and
    # dependency cycles this repo participates in. Governance-level truth,
    # distinct from the topology / schema directives above.
    conformance_totals: dict[str, int] = {}
    conformance_violations, dependency_cycles = _conformance_directive(
        alias, collector, conformance_totals
    )
    cf_suffix = ""
    if conformance_violations or dependency_cycles:
        cf_suffix = (
            f" Conformance: {len(conformance_violations)} architecture rule "
            f"violation(s), {len(dependency_cycles)} dependency cycle(s) involving "
            f"this repo."
        )

    directive = {
        "may_break": may_break,
        "may_break_tests": may_break_tests,
        "missing_cochanges": missing_cochanges,
        "missing_tests": missing_tests,
        "missing_tests_semantics": "changed_file_test_gap_compatibility_projection",
        "missing_tests_total": missing_tests_total,
        "missing_tests_emitted": len(missing_tests),
        "missing_tests_truncated": len(missing_tests) < missing_tests_total,
        "missing_tests_omitted": missing_tests_total - len(missing_tests),
        "files_without_measured_tests": [],
        "tests_to_run": tests_to_run,
        "tests_to_run_basis": tests_to_run_basis,
        "tests_to_run_total": tests_to_run_total,
        "tests_to_run_emitted": len(tests_to_run),
        "tests_to_run_truncated": tests_capped,
        "tests_to_run_omitted": tests_to_run_total - len(tests_to_run),
        "test_recommendations": test_recommendations,
        "test_recommendations_total": test_recommendations_total,
        "test_recommendations_emitted": len(test_recommendations),
        "test_recommendations_truncated": recommendations_capped,
        "test_recommendations_omitted": max(
            0, test_recommendations_total - len(test_recommendations)
        ),
        "test_analysis": test_impact.get("analysis") or {"status": "unavailable"},
        "coverage_analysis": coverage,
        "test_inference_analysis": test_impact.get("inference") or {"status": "unavailable"},
        "test_unknown_files": [],
        "will_break_consumers": will_break_consumers,
        "will_break_consumers_semantics": "structural_reach_only",
        "will_break_consumers_deprecated": True,
        "will_break_consumers_total": will_break_total,
        "will_break_consumers_emitted": len(will_break_consumers),
        "will_break_consumers_truncated": len(will_break_consumers) < will_break_total,
        "missing_cross_repo_cochanges": missing_cross_repo_cochanges,
        "missing_cross_repo_cochanges_total": missing_cross_repo_total,
        "missing_cross_repo_cochanges_emitted": len(missing_cross_repo_cochanges),
        "missing_cross_repo_cochanges_truncated": (
            len(missing_cross_repo_cochanges) < missing_cross_repo_total
        ),
        "cross_repo_relationship_analysis": cross_repo_relationships["analysis"],
        "breaking_changes": breaking_changes,
        "breaking_changes_truncated": breaking_changes_dropped,
        "conformance_violations": conformance_violations,
        "dependency_cycles": dependency_cycles,
        "governance_risk": governance_risk,
        "summary": (
            f"PR touches {len(changed_files)} file(s). "
            f"~{len(may_break)} downstream file(s) may be affected, "
            f"{len(may_break_tests)} test(s) may break, "
            f"{len(missing_cochanges)} historical co-changer(s) missing, "
            f"{missing_tests_summary}"
            f"{tests_to_run_suffix}{gov_suffix}{xr_suffix}{bc_suffix}{cf_suffix}"
        ),
    }
    for key, population, cap in (
        ("may_break", all_may_break, _MAY_BREAK_LIMIT),
        ("may_break_tests", all_may_break_tests, _MAY_BREAK_TESTS_LIMIT),
        ("missing_cochanges", all_missing_cochanges, 3),
        ("missing_tests", all_missing_tests if coverage_usable else [], 3),
        ("tests_to_run", all_tests_to_run, _TESTS_TO_RUN_LIMIT),
        ("test_recommendations", all_recommendations, _TESTS_TO_RUN_LIMIT),
        (
            "files_without_measured_tests",
            list(test_impact.get("files_without_measured_tests") or []),
            10,
        ),
        ("test_unknown_files", list(test_impact.get("unknown_files") or []), 10),
        ("governance_risk", governance_risk, 5),
    ):
        cap_collection(
            directive,
            key,
            population,
            cap,
            collector,
            label=f"directive.{key} beyond cap={cap}",
            preserve_counts=(key in {"missing_tests", "tests_to_run", "test_recommendations"}),
        )

    for key, total in (
        ("will_break_consumers", will_break_total),
        ("missing_cross_repo_cochanges", missing_cross_repo_total),
        ("breaking_changes", len(breaking_changes) + breaking_changes_dropped),
        ("conformance_violations", conformance_totals.get("conformance_violations", 0)),
        ("dependency_cycles", conformance_totals.get("dependency_cycles", 0)),
    ):
        emitted_count = len(directive.get(key) or [])
        is_legacy_count_family = key in {
            "will_break_consumers",
            "missing_cross_repo_cochanges",
        }
        if is_legacy_count_family or emitted_count < total:
            directive[f"{key}_total"] = total
            directive[f"{key}_emitted"] = emitted_count
        if emitted_count < total:
            directive[f"{key}_reduced_reason"] = "construction_cap"
            if key != "breaking_changes":
                directive[f"{key}_truncated"] = True
            directive[f"{key}_omitted"] = total - emitted_count

    response["directive"] = directive
