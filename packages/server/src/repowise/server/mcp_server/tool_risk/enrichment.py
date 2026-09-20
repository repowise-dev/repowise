"""Post-assessment result mutation for get_risk (cross-repo, deps, health)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

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
    cross_state = enricher.cross_repo_analysis
    contract_state = enricher.contract_analysis
    for r in results:
        target = r["target"]
        cross_partners = enricher.get_cross_repo_partners(alias, target)
        provider_links = enricher.get_contract_links_as_provider(alias, target)
        consumer_links = enricher.get_contract_links_as_consumer(alias, target)
        package_consumers = enricher.get_package_consumers(alias)

        consumers = [_contract_consumer(link, alias, target) for link in provider_links]
        r["consumers"] = consumers[:_RELATIONSHIP_LIMIT]
        r["consumers_total"] = len(consumers)
        r["consumers_emitted"] = len(r["consumers"])
        r["consumers_truncated"] = len(r["consumers"]) < len(consumers)
        cap_collection(
            r,
            "consumers",
            consumers,
            _RELATIONSHIP_LIMIT,
            collector if include_graph else None,
            label=f"{target} :: consumers beyond cap={_RELATIONSHIP_LIMIT}",
            preserve_counts=True,
        )
        r["relationship_analysis"]["consumers"] = {
            **contract_state,
            "scope": "workspace_contract_links",
            "evidence_kind": "contract",
        }

        co_change_links = [
            _cross_repo_co_change(alias, target, partner) for partner in cross_partners
        ]
        provider_rows = [
            _contract_consumer(link, alias, target)
            for link in provider_links
            if link.get("consumer_repo") != alias
        ]
        consumer_rows = [
            _contract_provider(link, alias, target)
            for link in consumer_links
            if link.get("provider_repo") != alias
        ]
        package_rows = [_package_consumer(link, target) for link in package_consumers]
        cross_repo_links = [*provider_rows, *consumer_rows, *package_rows, *co_change_links]
        priority = {"contract": 0, "structural": 1, "historical": 2}
        cross_repo_links.sort(
            key=lambda row: (
                priority.get(row["evidence_kind"], 9),
                row.get("target_repository", row.get("consumer_repository", "")),
                row.get("target_file", row.get("consumer_file", "")),
                row.get("source_repository", row.get("provider_repository", "")),
            )
        )
        r["cross_repo_links"] = cross_repo_links[:_RELATIONSHIP_LIMIT]
        r["cross_repo_links_total"] = len(cross_repo_links)
        r["cross_repo_links_emitted"] = len(r["cross_repo_links"])
        r["cross_repo_links_truncated"] = len(r["cross_repo_links"]) < len(cross_repo_links)
        cap_collection(
            r,
            "cross_repo_links",
            cross_repo_links,
            _RELATIONSHIP_LIMIT,
            collector if include_graph else None,
            label=f"{target} :: cross_repo_links beyond cap={_RELATIONSHIP_LIMIT}",
            preserve_counts=True,
        )
        r["relationship_analysis"]["cross_repo"] = _combined_analysis(
            cross_state, contract_state, cross_repo_links
        )

        if not cross_repo_links:
            continue

        affected_repos = sorted(
            {
                repo
                for row in cross_repo_links
                for repo in (
                    row.get("source_repository"),
                    row.get("target_repository"),
                    row.get("provider_repository"),
                    row.get("consumer_repository"),
                )
                if repo and repo != alias
            }
        )
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
        # Compatibility window: the old name remains, but every row and the
        # sibling marker state precisely that it is historical co-change.
        impact["cross_repo_consumers"] = legacy_co_changes[:_RELATIONSHIP_LIMIT]
        impact["cross_repo_consumers_semantics"] = "historical_co_change_partners"
        impact["cross_repo_consumers_deprecated"] = True
        impact["cross_repo_consumers_total"] = len(legacy_co_changes)
        impact["cross_repo_consumers_emitted"] = len(impact["cross_repo_consumers"])
        impact["cross_repo_consumers_truncated"] = len(impact["cross_repo_consumers"]) < len(
            legacy_co_changes
        )
        cap_collection(
            impact,
            "cross_repo_consumers",
            legacy_co_changes,
            _RELATIONSHIP_LIMIT,
            collector,
            label=f"{target} :: cross_repo_consumers beyond cap={_RELATIONSHIP_LIMIT}",
            preserve_counts=True,
        )
        impact["cross_repo_consumers_analysis"] = cross_state
        impact["affected_repos"] = affected_repos
        impact["affected_repos_total"] = len(affected_repos)
        impact["affected_repos_emitted"] = len(affected_repos)
        impact["affected_repos_truncated"] = False
        cross_provider_links = [
            link for link in provider_links if link.get("consumer_repo") != alias
        ]
        cross_consumer_links = [
            link for link in consumer_links if link.get("provider_repo") != alias
        ]
        if cross_provider_links:
            contract_consumers = [
                {
                    "consumer_repo": lk["consumer_repo"],
                    "consumer_file": lk["consumer_file"],
                    "contract_id": lk["contract_id"],
                    "type": lk["contract_type"],
                    "relationship_type": "contract_consumer",
                    "direction": "provider_to_consumer",
                    "evidence_kind": "contract",
                }
                for lk in cross_provider_links
            ]
            impact["contract_consumers"] = contract_consumers[:_RELATIONSHIP_LIMIT]
            impact["contract_consumers_total"] = len(cross_provider_links)
            impact["contract_consumers_emitted"] = len(impact["contract_consumers"])
            impact["contract_consumers_truncated"] = len(impact["contract_consumers"]) < len(
                cross_provider_links
            )
            cap_collection(
                impact,
                "contract_consumers",
                contract_consumers,
                _RELATIONSHIP_LIMIT,
                collector,
                label=f"{target} :: contract_consumers beyond cap={_RELATIONSHIP_LIMIT}",
                preserve_counts=True,
            )
        if cross_consumer_links:
            contract_providers = [
                {
                    "provider_repo": lk["provider_repo"],
                    "provider_file": lk["provider_file"],
                    "contract_id": lk["contract_id"],
                    "type": lk["contract_type"],
                    "relationship_type": "contract_provider",
                    "direction": "provider_to_consumer",
                    "evidence_kind": "contract",
                }
                for lk in cross_consumer_links
            ]
            impact["contract_providers"] = contract_providers[:_RELATIONSHIP_LIMIT]
            impact["contract_providers_total"] = len(cross_consumer_links)
            impact["contract_providers_emitted"] = len(impact["contract_providers"])
            impact["contract_providers_truncated"] = len(impact["contract_providers"]) < len(
                cross_consumer_links
            )
            cap_collection(
                impact,
                "contract_providers",
                contract_providers,
                _RELATIONSHIP_LIMIT,
                collector,
                label=f"{target} :: contract_providers beyond cap={_RELATIONSHIP_LIMIT}",
                preserve_counts=True,
            )


async def _enrich_health(results: list[dict], ctx: Any, repo_id: str) -> None:
    """Attach per-file health_score, coverage, and top_biomarkers from the health
    tables. Conservative: missing data → no field, never invented. Never raises.
    """
    try:
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

            f_res = await _h_session.execute(
                select(HealthFinding)
                .where(
                    HealthFinding.repository_id == repo_id,
                    HealthFinding.file_path.in_(target_paths),
                    HealthFinding.status == "open",
                )
                .order_by(HealthFinding.health_impact.desc())
            )
            top_by_file: dict[str, list[dict]] = {}
            for f in f_res.scalars().all():
                lst = top_by_file.setdefault(f.file_path, [])
                if len(lst) >= 3:
                    continue
                lst.append(
                    {
                        "biomarker_type": f.biomarker_type,
                        "severity": f.severity,
                        "function_name": f.function_name,
                        "impact": round(f.health_impact, 2),
                    }
                )

        for r in results:
            path = r.get("target")
            m = metric_map.get(path)
            if m is not None:
                r["health_score"] = round(m.score, 2)
                if m.line_coverage_pct is not None:
                    r["coverage_pct"] = round(m.line_coverage_pct, 2)
                if m.branch_coverage_pct is not None:
                    r["branch_coverage_pct"] = round(m.branch_coverage_pct, 2)
            if path in top_by_file:
                r["top_biomarkers"] = top_by_file[path]
    except Exception:
        pass
