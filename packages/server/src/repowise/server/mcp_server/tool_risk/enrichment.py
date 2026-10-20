"""Post-assessment result mutation for get_risk (cross-repo, deps, health)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.persistence.crud.analysis.shown import ungated
from repowise.core.persistence.database import get_session
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import (
    _is_workspace_mode,
)

_RELATIONSHIP_LIMIT = 5


def _contract_consumer(link: dict, provider_repo: str, provider_file: str) -> dict:
    row = {
        "source_repository": provider_repo,
        "source_file": provider_file,
        "target_repository": link["consumer_repo"],
        "target_file": link["consumer_file"],
        "provider_repository": provider_repo,
        "provider_file": provider_file,
        "consumer_repository": link["consumer_repo"],
        "consumer_file": link["consumer_file"],
        "contract_id": link["contract_id"],
        "contract_type": link["contract_type"],
        "relationship_type": "contract_consumer",
        "direction": "provider_to_consumer",
        "evidence_kind": "contract",
    }
    for key in ("match_type", "confidence"):
        if link.get(key) is not None:
            row[key] = link[key]
    return row


def _contract_provider(link: dict, consumer_repo: str, consumer_file: str) -> dict:
    row = {
        "source_repository": link["provider_repo"],
        "source_file": link["provider_file"],
        "target_repository": consumer_repo,
        "target_file": consumer_file,
        "provider_repository": link["provider_repo"],
        "provider_file": link["provider_file"],
        "consumer_repository": consumer_repo,
        "consumer_file": consumer_file,
        "contract_id": link["contract_id"],
        "contract_type": link["contract_type"],
        "relationship_type": "contract_provider",
        "direction": "provider_to_consumer",
        "evidence_kind": "contract",
    }
    for key in ("match_type", "confidence"):
        if link.get(key) is not None:
            row[key] = link[key]
    return row


def _cross_repo_co_change(alias: str, target: str, partner: dict) -> dict:
    row = {
        "source_repository": alias,
        "source_file": target,
        "target_repository": partner["repo"],
        "target_file": partner["file"],
        "relationship_type": "co_change",
        "direction": "undirected",
        "evidence_kind": "historical",
        "provenance": "workspace_git_history",
        "strength": partner.get("strength", 0),
    }
    if partner.get("frequency") is not None:
        row["support"] = partner["frequency"]
    if partner.get("last_date"):
        row["last_observed_at"] = partner["last_date"]
    return row


def _package_consumer(link: dict, target: str) -> dict:
    """Normalize a repository-level package consumer without inventing a file edge."""
    return {
        "source_repository": link["source_repo"],
        "source_file": link.get("source_manifest") or None,
        "target_repository": link["target_repo"],
        "target_file": None,
        "queried_file": target,
        "relationship_type": "package_dependency",
        "dependency_kind": link.get("kind") or "unknown",
        "direction": "consumer_to_dependency",
        "evidence_kind": "structural",
        "claim": "repository_structural_reach",
        "runtime_breakage_claim": False,
        "granularity": "repository",
        "provenance": "package_manifest",
    }


def _combined_analysis(cross_state: dict, contract_state: dict, rows: list[dict]) -> dict:
    statuses = {cross_state.get("status"), contract_state.get("status")}
    if statuses == {"unavailable"}:
        status = "unavailable"
    elif statuses & {"partial", "degraded"} or statuses == {"available", "unavailable"}:
        status = "partial"
    else:
        status = "available"
    return {
        "status": status,
        "scope": "workspace_file_and_repository_relationships",
        "evidence_kinds": sorted({row["evidence_kind"] for row in rows}),
        "sources": {
            "cross_repo_overlay": cross_state,
            "contracts": contract_state,
        },
    }


def _set_capped(
    container: dict,
    key: str,
    population: list[dict],
    collector: OmissionCollector | None,
    target: str,
    extra: dict | None = None,
) -> None:
    """Write *key* capped at the relationship limit, then its counts.

    *extra* fields land between the list and its counts, where the wire
    format puts them.
    """
    shown = population[:_RELATIONSHIP_LIMIT]
    container[key] = shown
    container.update(extra or {})
    container[f"{key}_total"] = len(population)
    container[f"{key}_emitted"] = len(shown)
    container[f"{key}_truncated"] = len(shown) < len(population)
    cap_collection(
        container,
        key,
        population,
        _RELATIONSHIP_LIMIT,
        collector,
        label=f"{target} :: {key} beyond cap={_RELATIONSHIP_LIMIT}",
        preserve_counts=True,
    )


_EVIDENCE_PRIORITY = {"contract": 0, "structural": 1, "historical": 2}


def _cross_repo_links(
    alias: str,
    target: str,
    cross_partners: list[dict],
    provider_links: list[dict],
    consumer_links: list[dict],
    package_consumers: list[dict],
) -> list[dict]:
    """Every cross-repo row for *target*, contract evidence first."""
    links = [
        *(
            _contract_consumer(link, alias, target)
            for link in provider_links
            if link.get("consumer_repo") != alias
        ),
        *(
            _contract_provider(link, alias, target)
            for link in consumer_links
            if link.get("provider_repo") != alias
        ),
        *(_package_consumer(link, target) for link in package_consumers),
        *(_cross_repo_co_change(alias, target, partner) for partner in cross_partners),
    ]
    links.sort(
        key=lambda row: (
            _EVIDENCE_PRIORITY.get(row["evidence_kind"], 9),
            row.get("target_repository", row.get("consumer_repository", "")),
            row.get("target_file", row.get("consumer_file", "")),
            row.get("source_repository", row.get("provider_repository", "")),
        )
    )
    return links


def _affected_repos(alias: str, links: list[dict]) -> list[str]:
    return sorted(
        {
            repo
            for row in links
            for repo in (
                row.get("source_repository"),
                row.get("target_repository"),
                row.get("provider_repository"),
                row.get("consumer_repository"),
            )
            if repo and repo != alias
        }
    )


def _legacy_contract_row(link: dict, side: str, relationship_type: str) -> dict:
    """A ``cross_repo_impact`` contract row naming the *side* (consumer or provider)."""
    return {
        f"{side}_repo": link[f"{side}_repo"],
        f"{side}_file": link[f"{side}_file"],
        "contract_id": link["contract_id"],
        "type": link["contract_type"],
        "relationship_type": relationship_type,
        "direction": "provider_to_consumer",
        "evidence_kind": "contract",
    }


def _attach_impact(
    r: dict,
    alias: str,
    cross_state: dict,
    cross_partners: list[dict],
    links: dict[str, list[dict]],
    collector: OmissionCollector | None,
) -> None:
    """The legacy ``cross_repo_impact`` block, for a target with cross-repo rows.

    ``cross_repo_consumers`` keeps its old name; each row and the sibling
    marker say it is historical co-change. The deprecated list is left out
    when empty, so it never reads as "no consumers" beside ``affected_repos``.
    """
    target = r["target"]
    impact = r.setdefault("cross_repo_impact", {})
    legacy_co_changes = [
        {
            "repo": p["repo"],
            "file": p["file"],
            "strength": p["strength"],
            "relationship_type": "co_change",
            "direction": "undirected",
            "evidence_kind": "historical",
            "provenance": "workspace_git_history",
        }
        for p in cross_partners
    ]
    if legacy_co_changes:
        _set_capped(
            impact,
            "cross_repo_consumers",
            legacy_co_changes,
            collector,
            target,
            extra={
                "cross_repo_consumers_semantics": "historical_co_change_partners",
                "cross_repo_consumers_deprecated": True,
            },
        )
    affected = _affected_repos(alias, links["all"])
    impact["cross_repo_consumers_analysis"] = cross_state
    impact["affected_repos"] = affected
    impact["affected_repos_total"] = len(affected)
    impact["affected_repos_emitted"] = len(affected)
    impact["affected_repos_truncated"] = False
    provider_side = [lk for lk in links["provider"] if lk.get("consumer_repo") != alias]
    consumer_side = [lk for lk in links["consumer"] if lk.get("provider_repo") != alias]
    if provider_side:
        rows = [_legacy_contract_row(lk, "consumer", "contract_consumer") for lk in provider_side]
        _set_capped(impact, "contract_consumers", rows, collector, target)
    if consumer_side:
        rows = [_legacy_contract_row(lk, "provider", "contract_provider") for lk in consumer_side]
        _set_capped(impact, "contract_providers", rows, collector, target)


def _enrich_target(
    r: dict,
    enricher: Any,
    states: tuple[dict, dict],
    alias: str,
    collector: OmissionCollector | None,
    include_graph: bool,
) -> None:
    """Typed workspace relationships for one target card.

    *states* is the enricher's ``(cross_repo_analysis, contract_analysis)``.
    """
    target = r["target"]
    cross_state, contract_state = states
    cross_partners = enricher.get_cross_repo_partners(alias, target)
    provider_links = enricher.get_contract_links_as_provider(alias, target)
    consumer_links = enricher.get_contract_links_as_consumer(alias, target)
    package_consumers = enricher.get_package_consumers(alias)
    graph_collector = collector if include_graph else None

    consumers = [_contract_consumer(link, alias, target) for link in provider_links]
    _set_capped(r, "consumers", consumers, graph_collector, target)
    r["relationship_analysis"]["consumers"] = {
        **contract_state,
        "scope": "workspace_contract_links",
        "evidence_kind": "contract",
    }
    links = _cross_repo_links(
        alias, target, cross_partners, provider_links, consumer_links, package_consumers
    )
    _set_capped(r, "cross_repo_links", links, graph_collector, target)
    r["relationship_analysis"]["cross_repo"] = _combined_analysis(
        cross_state, contract_state, links
    )
    if links:
        _attach_impact(
            r,
            alias,
            cross_state,
            cross_partners,
            {"all": links, "provider": provider_links, "consumer": consumer_links},
            collector,
        )


async def _enrich_cross_repo(
    results: list[dict],
    alias: str,
    collector: OmissionCollector | None = None,
    *,
    include_graph: bool = False,
) -> None:
    """Add typed workspace relationships without changing dependency counts.

    Co-change is historical and undirected. Consumers exist only when a typed
    provider-to-consumer contract link exists. Every normalized cross-repo row
    retains both repository identities, direction, type, and evidence kind.
    """
    enricher = _state._cross_repo_enricher
    if enricher is None or not _is_workspace_mode():
        return
    states = (enricher.cross_repo_analysis, enricher.contract_analysis)
    for r in results:
        _enrich_target(r, enricher, states, alias, collector, include_graph)


async def _enrich_health(results: list[dict], ctx: Any, repo_id: str) -> None:
    """Attach per-file health_score, coverage, and top_biomarkers from the health
    tables. Conservative: missing data → no field, never invented. Never raises.

    Coverage comes from the stored coverage rows, the source every other surface
    reads, not the copy the health pass took of them.
    """
    try:
        from repowise.core.persistence.crud import load_coverage_for_repo
        from repowise.core.persistence.models import HealthFileMetric, HealthFinding

        target_paths = [r["target"] for r in results if r.get("target")]
        if not target_paths:
            return
        async with get_session(ctx.session_factory) as _h_session:
            m_res = await _h_session.execute(
                select(HealthFileMetric).where(
                    HealthFileMetric.repository_id == repo_id,
                    HealthFileMetric.file_path.in_(target_paths),
                )
            )
            metric_map = {m.file_path: m for m in m_res.scalars().all()}
            coverage_map = {
                c.file_path: c
                for c in await load_coverage_for_repo(
                    _h_session, repo_id, file_paths=target_paths, include_covered_lines=False
                )
            }

            f_res = await _h_session.execute(
                select(HealthFinding)
                .where(
                    HealthFinding.repository_id == repo_id,
                    HealthFinding.file_path.in_(target_paths),
                    HealthFinding.status == "open",
                    HealthFinding.biomarker_type.not_in(excluded_types()),
                    # get_risk is about history: its history markers stay.
                    ungated(
                        "health",
                        HealthFinding.file_path,
                        HealthFinding.biomarker_type,
                        hold_history=False,
                    ),
                )
                .order_by(HealthFinding.health_impact.desc())
            )
            top_by_file: dict[str, list[dict]] = {}
            for f in f_res.scalars().all():
                lst = top_by_file.setdefault(f.file_path, [])
                if len(lst) >= 3:
                    continue
                # ``function_name`` absent rather than null on a file-level
                # biomarker, matching the coverage fields just below: an absent
                # key and a null one say the same thing, and only one is billed.
                lst.append(
                    {
                        "biomarker_type": f.biomarker_type,
                        "severity": f.severity,
                        **({"function_name": f.function_name} if f.function_name else {}),
                        "impact": round(f.health_impact, 2),
                    }
                )

        for r in results:
            path = r.get("target")
            m = metric_map.get(path)
            if m is not None and m.score is not None:
                r["health_score"] = round(m.score, 2)
            c = coverage_map.get(path)
            if c is not None:
                r["line_coverage_pct"] = round(c.line_coverage_pct, 2)
                if c.branch_coverage_pct is not None:
                    r["branch_coverage_pct"] = round(c.branch_coverage_pct, 2)
            if path in top_by_file:
                r["top_biomarkers"] = top_by_file[path]
    except Exception:
        pass
