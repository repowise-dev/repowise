"""Dashboard mode: no query and no targets, the decision health summary."""

from __future__ import annotations

import json

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    cap_collection,
)
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    filter_path_list,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._why_evidence import (
    annotate_response_evidence_async,
)
from repowise.server.mcp_server.tool_why.caps import (
    _MAX_HEALTH_PROPOSED,
    _MAX_HEALTH_RETIRED,
    _MAX_HEALTH_STALE,
    _MAX_HEALTH_UNGOVERNED,
)
from repowise.server.mcp_server.tool_why.loading import _attach_decision_evidence


async def _why_health_dashboard(repo: str | None) -> dict:
    """Mode 1: no query — return the decision health dashboard."""
    from repowise.core.persistence.crud import get_decision_health_summary

    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        health = await get_decision_health_summary(session, repository.id)

        stale = health["stale_decisions"]
        proposed = health["proposed_awaiting_review"]
        ungoverned = health["ungoverned_hotspots"]
        retired = health["retired_decisions"]
        unscoped = health["unscoped_decisions"]

        result_data = {
            "mode": "health",
            "summary": (
                f"{health['summary'].get('active', 0)} active · "
                f"{health['summary'].get('stale', 0)} stale · "
                f"{len(proposed)} proposed · "
                f"{len(ungoverned)} ungoverned hotspots"
            ),
            "counts": health["summary"],
            "stale_decisions": [
                {
                    "id": d.id,
                    "title": d.title,
                    "staleness_score": d.staleness_score,
                    "affected_files": filter_path_list(
                        json.loads(d.affected_files_json), _get_exclude_spec(ctx.path)
                    ),
                }
                for d in stale
            ],
            "proposed_awaiting_review": [
                {
                    "id": d.id,
                    "title": d.title,
                    "source": d.source,
                    "confidence": d.confidence,
                }
                for d in proposed
            ],
            "ungoverned_hotspots": ungoverned,
            "conflicts": list(health.get("conflicts", [])),
            # No other mode enumerates these lanes. ``active`` stays count-only:
            # every other mode exists to serve it.
            "retired_decisions": [
                {"id": d.id, "title": d.title, "lane": lane} for lane, d in retired
            ],
            "unscoped_decisions": [
                {"id": d.id, "title": d.title, "confidence": d.confidence} for d in unscoped
            ],
            "_meta": _build_meta(repository=repository),
        }
        for entry in result_data["stale_decisions"]:
            cap_collection(
                entry,
                "affected_files",
                entry["affected_files"],
                5,
                collector,
                label=f"health stale decision {entry['id']} :: affected_files beyond cap=5",
            )
        await _attach_decision_evidence(session, [*stale, *proposed])
        result_data = await annotate_response_evidence_async(
            result_data,
            ctx.alias,
            [*stale, *proposed],
            repo_root=ctx.path,
        )
        for key, cap in (
            ("stale_decisions", _MAX_HEALTH_STALE),
            ("proposed_awaiting_review", _MAX_HEALTH_PROPOSED),
            ("ungoverned_hotspots", _MAX_HEALTH_UNGOVERNED),
            ("conflicts", 10),
            ("retired_decisions", _MAX_HEALTH_RETIRED),
            ("unscoped_decisions", _MAX_HEALTH_RETIRED),
        ):
            cap_collection(
                result_data,
                key,
                result_data[key],
                cap,
                collector,
                label=f"health {key} beyond cap={cap}",
            )
        collector.attach(result_data)
        return result_data
