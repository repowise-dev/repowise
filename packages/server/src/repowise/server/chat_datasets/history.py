"""The ``changes`` and ``health_trend`` datasets: what happened over time.

``changes`` is one row per merged change in the indexed history, the records
get_overview's change block counts; ``health_trend`` is one row per health
snapshot, the scalars get_health's trend reads.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import select

from repowise.server.chat_datasets import (
    AVERAGES,
    SUMS,
    DatasetField,
    LoadContext,
    register_dataset,
)


@register_dataset(
    "changes",
    description="One row per merged change in the indexed history (get_overview's change records).",
    fields={
        "date": DatasetField("time", "the day it merged"),
        "week": DatasetField("time", "the Monday of the week it merged", step="week"),
        "month": DatasetField("time", "the month it merged"),
        "category": DatasetField(
            "text", "feature, fix, refactor, docs, test, deps, chore or other"
        ),
        "shape": DatasetField("text", "merge, squash or commit"),
        "files_changed": DatasetField(
            "number", "files the change touched (to count changes, omit y)", SUMS, unit="files"
        ),
        "lines_added": DatasetField("number", "lines added", SUMS, unit="lines"),
        "lines_deleted": DatasetField("number", "lines deleted", SUMS, unit="lines"),
    },
    basis=("measured", "git history"),
    example={"x": "week", "series": "category", "aggregate": "count"},
    noun="merged changes",
)
async def load_changes(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.core.persistence.models import Change
    from repowise.server.mcp_server._changes import _week

    q = select(
        Change.merged_at,
        Change.category,
        Change.shape,
        Change.files_changed,
        Change.lines_added,
        Change.lines_deleted,
    ).where(Change.repository_id == ctx.repository.id)
    rows = []
    for merged_at, category, shape, files, added, deleted in await ctx.session.execute(q):
        if merged_at is None:
            continue
        moment = merged_at if merged_at.tzinfo else merged_at.replace(tzinfo=UTC)
        rows.append(
            {
                "date": moment.date().isoformat(),
                "week": _week(moment),
                "month": moment.strftime("%Y-%m"),
                "category": category,
                "shape": shape,
                "files_changed": files,
                "lines_added": added,
                "lines_deleted": deleted,
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


@register_dataset(
    "health_trend",
    description="One row per health snapshot, oldest first (get_health's trend).",
    fields={
        "taken_at": DatasetField("time", "when the snapshot was taken"),
        "average_health": DatasetField("number", "average file health, 0 to 10", AVERAGES, unit="score"),
        "hotspot_health": DatasetField(
            "number", "average health of hotspot files, 0 to 10", AVERAGES, unit="score"
        ),
    },
    basis=("inferred", "health snapshots"),
    example={"x": "taken_at", "y": ["average_health", "hotspot_health"], "kind": "line"},
    noun="health snapshots",
)
async def load_health_trend(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.core.persistence.crud.analysis.health import get_health_snapshot_headline

    # Scalars only: whole snapshot rows carry every file's score.
    headline = await get_health_snapshot_headline(ctx.session, ctx.repository.id, recent=50)
    return [
        {
            "taken_at": s.taken_at.isoformat(timespec="seconds"),
            "average_health": round(s.average_health, 2),
            "hotspot_health": round(s.hotspot_health, 2) if s.hotspot_health is not None else None,
        }
        for s in headline.recent
    ]
