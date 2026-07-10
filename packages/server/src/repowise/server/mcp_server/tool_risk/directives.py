"""PR-mode directive assembly for get_risk."""

from __future__ import annotations

from typing import Any

from repowise.core.persistence.database import get_session
from repowise.core.persistence.decision_graph import get_governing_decisions, list_conflict_edges
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
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


def _breaking_change_directive(repo_alias: str) -> list[dict[str, Any]]:
    """Breaking-change half of the PR directive: incompatible provider changes.

    Reads the persisted breaking-change report (current HEAD vs the previously
    indexed contracts), filtered to providers in the changed repo, and reports
    each change with the consumers it endangers across repos. Returns an empty
    list when not in workspace mode or no report is available. Never raises.
    """
    out: list[dict[str, Any]] = []
    try:
        if not _is_workspace_mode():
            return out
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_breaking_changes", False):
            return out
        for change in enricher.get_breaking_changes_for_repo(repo_alias):
            if len(out) >= _BC_PROVIDER_LIMIT:
                break
            consumers = change.get("impacted_consumers", [])
            # Only surface changes that actually endanger a cross-repo consumer —
            # an internal-only removed endpoint isn't a cross-repo break.
            cross = [c for c in consumers if c.get("repo") != repo_alias]
            if not cross:
                continue
            out.append(
                {
                    "contract_id": change.get("contract_id"),
                    "type": change.get("contract_type"),
                    "kind": change.get("kind"),
                    "severity": change.get("severity"),
                    "detail": change.get("detail"),
                    "impacted_consumers": [
                        {
                            "repo": c.get("repo"),
                            "service": c.get("service"),
                            "file": c.get("file"),
                        }
                        for c in cross[:_BC_CONSUMER_LIMIT]
                    ],
                }
            )
    except Exception:
        return []
    return out


def _conformance_directive(repo_alias: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        for v in scoped.get("violations", [])[:_CF_VIOLATION_LIMIT]:
            violations.append(
                {
                    "source": v.get("source"),
                    "target": v.get("target"),
                    "rule": f"{v.get('rule_source')} !-> {v.get('rule_target')}",
                    "edge_kind": v.get("edge_kind"),
                    "description": v.get("rule_description") or None,
                }
            )
        for c in scoped.get("cycles", [])[:_CF_CYCLE_LIMIT]:
            cycles.append({"nodes": c.get("nodes", []), "length": c.get("length", 0)})
    except Exception:
        return [], []
    return violations, cycles


def _cross_repo_directive(repo_alias: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cross-repo half of the PR directive: downstream services in other repos.

    Resolves the changed repo to its system-graph nodes and ranks reachable
    services in OTHER repos by impact, splitting structural (``will_break``) from
    behavioral co-change (``missing_cochanges``). Returns two empty lists when
    not in workspace mode or no system graph is available. Never raises.
    """
    will_break_consumers: list[dict[str, Any]] = []
    missing_cross_repo_cochanges: list[dict[str, Any]] = []
    try:
        if not _is_workspace_mode():
            return will_break_consumers, missing_cross_repo_cochanges
        enricher = _state._cross_repo_enricher
        raw_graph = enricher.get_system_graph() if enricher is not None else None
        if not raw_graph:
            return will_break_consumers, missing_cross_repo_cochanges

        from repowise.core.workspace.blast_radius import cross_repo_blast_radius
        from repowise.core.workspace.system_graph import SystemGraph

        result = cross_repo_blast_radius(SystemGraph.from_dict(raw_graph), [repo_alias])
        for n in result.impacted:
            if n.repo == repo_alias:
                continue  # cross-repo only — intra-repo impact is the single-repo blast
            if n.structural:
                if len(will_break_consumers) < _XR_WILL_BREAK_LIMIT:
                    will_break_consumers.append(
                        {
                            "repo": n.repo,
                            "service": n.name,
                            "distance": n.distance,
                            "score": n.score,
                            "via": n.edge_kinds,
                        }
                    )
            elif len(missing_cross_repo_cochanges) < _XR_COCHANGE_LIMIT:
                missing_cross_repo_cochanges.append(
                    {"repo": n.repo, "service": n.name, "score": n.score}
                )
    except Exception:
        return [], []
    return will_break_consumers, missing_cross_repo_cochanges


def _trim_blast_lists(
    pr_blast_radius: dict[str, Any],
    exclude_spec: Any,
    collector: OmissionCollector | None = None,
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
        if len(value) > cap:
            trimmed_blast[key] = value[:cap]
            trimmed_blast[f"{key}_truncated_total"] = len(value)
            if collector is not None:
                collector.add(
                    f"pr_blast_radius.{key} beyond cap={cap} ({len(value) - cap} dropped)",
                    value[cap:],
                )
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
            seen_dr_ids: set[str] = set()
            for cf in changed_files:
                for dr in await get_governing_decisions(_gr_session, _gr_repo_id, cf):
                    if dr.id in seen_dr_ids:
                        continue
                    seen_dr_ids.add(dr.id)
                    reason = _governance_reason(dr, conflict_decision_ids)
                    if reason is None:
                        continue
                    governance_risk.append(
                        {
                            "file": cf,
                            "decision_id": dr.id,
                            "title": dr.title,
                            "status": dr.status,
                            "reason": reason,
                        }
                    )
                    if len(governance_risk) >= 5:
                        break
                if len(governance_risk) >= 5:
                    break
    except Exception:
        pass
    return governance_risk


def _governance_reason(dr: Any, conflict_decision_ids: set[str]) -> str | None:
    """Map a governing decision to a directive reason, or None when clean."""
    staleness = dr.staleness_score or 0.0
    if dr.status == "active" and staleness >= 0.5:
        return "stale_governance"
    if dr.status == "superseded":
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
    alias: str,
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

    trimmed_blast = _trim_blast_lists(pr_blast_radius, exclude_spec, collector)
    response["pr_blast_radius"] = trimmed_blast

    # Directive: 3 short lists the agent can read in one glance. Each
    # entry is a file path (string), never a dossier. Designed to answer
    # "what should I do about this PR" in three lines.

    will_break = filter_path_list(
        [p for p in (_as_path(e) for e in trimmed_blast.get("transitive_affected", [])) if p],
        exclude_spec,
    )[:5]
    missing_cochanges = filter_path_list(
        [p for p in (_as_path(e) for e in trimmed_blast.get("cochange_warnings", [])) if p],
        exclude_spec,
    )[:3]
    # Scope to the PR: the directive answers "what should I do about
    # THIS diff", so only changed files belong here. Repo-wide test
    # gaps stay available in pr_blast_radius.test_gaps for deeper
    # review — surfacing them in the directive made unrelated files
    # ("alembic/env.py has no tests") read as failings of the PR.
    # Read from the untrimmed analyzer payload: the trimmed list is
    # capped at 10 repo-wide entries and may have already dropped the
    # changed files we're looking for.
    changed_set = set(changed_files)
    missing_tests = filter_path_list(
        [
            p
            for p in (_as_path(e) for e in pr_blast_radius.get("test_gaps", []))
            if p and p in changed_set
        ],
        exclude_spec,
    )[:3]

    gov_count = len(governance_risk)
    gov_suffix = f" {gov_count} governance risk(s) detected." if gov_count > 0 else ""

    # Cross-repo directive (workspace mode only). Resolve the changed repo to
    # its system-graph nodes and walk reachability to find downstream
    # services in OTHER repos — split structural (will break) from behavioral
    # (co-change only). Repo-scoped: it answers "can this PR's repo break
    # something across a repo boundary?" using the same reachability the map
    # and get_blast_radius use.
    will_break_consumers, missing_cross_repo_cochanges = _cross_repo_directive(alias)
    xr_suffix = ""
    if will_break_consumers or missing_cross_repo_cochanges:
        xr_suffix = (
            f" Cross-repo: {len(will_break_consumers)} consumer service(s) may break, "
            f"{len(missing_cross_repo_cochanges)} cross-repo co-changer(s) missing."
        )

    # Breaking-change guard — incompatible provider changes (removed route /
    # field, type change, ...) in this repo and the consumers they endanger.
    # Schema-level truth, distinct from the topology-level will_break_consumers.
    breaking_changes = _breaking_change_directive(alias)
    bc_suffix = ""
    if breaking_changes:
        bc_consumers = sum(len(b["impacted_consumers"]) for b in breaking_changes)
        bc_suffix = (
            f" Breaking changes: {len(breaking_changes)} provider contract(s) changed "
            f"incompatibly, endangering {bc_consumers} consumer(s)."
        )

    # Architecture conformance — declared dependency-rule violations and
    # dependency cycles this repo participates in. Governance-level truth,
    # distinct from the topology / schema directives above.
    conformance_violations, dependency_cycles = _conformance_directive(alias)
    cf_suffix = ""
    if conformance_violations or dependency_cycles:
        cf_suffix = (
            f" Conformance: {len(conformance_violations)} architecture rule "
            f"violation(s), {len(dependency_cycles)} dependency cycle(s) involving "
            f"this repo."
        )

    response["directive"] = {
        "will_break": will_break,
        "missing_cochanges": missing_cochanges,
        "missing_tests": missing_tests,
        "will_break_consumers": will_break_consumers,
        "missing_cross_repo_cochanges": missing_cross_repo_cochanges,
        "breaking_changes": breaking_changes,
        "conformance_violations": conformance_violations,
        "dependency_cycles": dependency_cycles,
        "governance_risk": governance_risk,
        "overall_risk_score": trimmed_blast.get("overall_risk_score"),
        "summary": (
            f"PR touches {len(changed_files)} file(s). "
            f"~{len(will_break)} downstream file(s) likely affected, "
            f"{len(missing_cochanges)} historical co-changer(s) missing, "
            f"{len(missing_tests)} file(s) without tests."
            f"{gov_suffix}{xr_suffix}{bc_suffix}{cf_suffix}"
        ),
    }
