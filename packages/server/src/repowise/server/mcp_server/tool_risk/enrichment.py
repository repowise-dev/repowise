"""Post-assessment result mutation for get_risk (cross-repo, deps, health)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _is_workspace_mode,
)


async def _enrich_cross_repo(results: list[dict], alias: str) -> None:
    """Annotate per-target results with cross-repo partners, affected repos and
    contract links (workspace mode only). Mutates *results* in place; no-op when
    no enricher data is available. Behavior preserved verbatim from inline form.
    """
    enricher = _state._cross_repo_enricher
    if enricher is None or not enricher.has_data or not _is_workspace_mode():
        return
    for r in results:
        target = r["target"]
        cross_partners = enricher.get_cross_repo_partners(alias, target)
        affected_repos = enricher.get_affected_repos(alias, target)
        if cross_partners or affected_repos:
            r["cross_repo_impact"] = {
                "cross_repo_consumers": [
                    {"repo": p["repo"], "file": p["file"], "strength": p["strength"]}
                    for p in cross_partners[:5]
                ],
                "affected_repos": affected_repos,
            }
            r["dependents_count"] = r.get("dependents_count", 0) + len(cross_partners)
            # Rebuild risk_summary with updated dependents count
            if "_base_dep_count" in r:
                r["risk_summary"] = r["risk_summary"].replace(
                    f"{r['_base_dep_count']} dependents",
                    f"{r['dependents_count']} dependents",
                )

        # Contract links (Phase 4)
        if not enricher.has_contract_data:
            continue
        provider_links = enricher.get_contract_links_as_provider(alias, target)
        consumer_links = enricher.get_contract_links_as_consumer(alias, target)
        if not (provider_links or consumer_links):
            continue
        impact = r.setdefault("cross_repo_impact", {})
        if provider_links:
            impact["contract_consumers"] = [
                {
                    "consumer_repo": lk["consumer_repo"],
                    "consumer_file": lk["consumer_file"],
                    "contract_id": lk["contract_id"],
                    "type": lk["contract_type"],
                }
                for lk in provider_links[:5]
            ]
            r["dependents_count"] = r.get("dependents_count", 0) + len(provider_links)
        if consumer_links:
            impact["contract_providers"] = [
                {
                    "provider_repo": lk["provider_repo"],
                    "provider_file": lk["provider_file"],
                    "contract_id": lk["contract_id"],
                    "type": lk["contract_type"],
                }
                for lk in consumer_links[:5]
            ]


def _finalize_dep_summaries(results: list[dict]) -> None:
    """Rebuild risk_summary for any post-enrichment dependents_count change and
    drop the internal ``_base_dep_count`` key. Mutates *results* in place.
    """
    for r in results:
        base = r.pop("_base_dep_count", None)
        if base is not None and r.get("dependents_count", base) != base:
            r["risk_summary"] = r["risk_summary"].replace(
                f"{base} dependents",
                f"{r['dependents_count']} dependents",
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
