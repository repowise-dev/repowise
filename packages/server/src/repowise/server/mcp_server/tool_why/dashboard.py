"""Dashboard mode: no query and no targets, the decision health summary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from repowise.core.persistence.database import get_session
from repowise.core.precedent.currency import commits_since
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


def _stale_row_currency(root: Path, decision: object, files: list[str]) -> dict:
    """What git can say about one stale row: commits since, and files gone.

    Only keys git can answer are returned; a failed query omits them rather
    than guessing. ``commits_since`` counts from the evidence commit when
    there is one, else from the record's creation date.
    """
    evidence = json.loads(getattr(decision, "evidence_commits_json", None) or "[]")
    since_commit = evidence[-1] if evidence else None
    count = commits_since(
        root,
        since_commit=since_commit,
        since_date=None if since_commit else getattr(decision, "created_at", None),
        nodes=files,
    )
    extra: dict = {}
    if count is not None:
        extra["commits_since"] = count
    moved = [f for f in files if not (Path(root) / f).exists()]
    if moved:
        extra["moved_files"] = moved
    return extra


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
        # After the cap, so the git cost is bounded by the rows actually served.
        by_id = {d.id: d for d in stale}
        for entry in result_data["stale_decisions"]:
            decision = by_id.get(entry["id"])
            if decision is None:
                continue
            entry.update(
                await asyncio.to_thread(
                    _stale_row_currency,
                    Path(ctx.path),
                    decision,
                    entry["affected_files"],
                )
            )
        collector.attach(result_data)
        return result_data
