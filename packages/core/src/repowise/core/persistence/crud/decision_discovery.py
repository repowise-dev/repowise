"""The records an agent's discovery pass checks, and the agreements it can move.

The selections return plain dicts, which ``repowise.core.agent_prompts.decisions``
renders; :func:`decision_prompt_for` is that pair over this store, the one call
``repowise decision discover`` and the server route both make.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.agent_prompts.decisions import (
    AGREEMENTS_LIMIT,
    DISCOVERY_LIMIT,
    DecisionPrompt,
    decision_prompt,
)
from repowise.core.analysis.decisions.lifecycle import AGREEMENT_KIND, RETIRED_STATUSES
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.analysis.finding_registry import stored_origin
from repowise.core.code_origin import path_origin

from ..models import DecisionCandidateMeta, DecisionRecord, GitMetadata, HealthFileMetric
from .authority import accepted_predicate, candidate_predicate

#: Paths per ``IN`` clause, under SQLite's bound-parameter ceiling.
_PATH_CHUNK = 500

#: An agent's own record waits for its commit to merge; it is not background.
_PENDING_SOURCES = ("agent",)


def _named_files(record: DecisionRecord) -> list[str]:
    try:
        files = json.loads(record.affected_files_json or "[]")
    except (TypeError, ValueError):
        return []
    return [f for f in files if isinstance(f, str) and f] if isinstance(files, list) else []


async def _production_heat(
    session: AsyncSession, repository_id: str, paths: list[str]
) -> dict[str, tuple[bool, float, float]]:
    """``(is_hotspot, score, churn)`` for each of *paths* that is production code with history.

    Origin is the stored ``code_origin``, and the path decides for a file with
    no health row. A file git holds no history for is absent: nothing says it
    is busy.
    """
    heat: dict[str, tuple[bool, float, float]] = {}
    for start in range(0, len(paths), _PATH_CHUNK):
        chunk = paths[start : start + _PATH_CHUNK]
        rows = await session.execute(
            select(
                GitMetadata.file_path,
                GitMetadata.is_hotspot,
                GitMetadata.temporal_hotspot_score,
                GitMetadata.churn_percentile,
                HealthFileMetric.file_path,
                HealthFileMetric.code_origin,
                HealthFileMetric.is_test,
            )
            .outerjoin(
                HealthFileMetric,
                and_(
                    HealthFileMetric.repository_id == GitMetadata.repository_id,
                    HealthFileMetric.file_path == GitMetadata.file_path,
                ),
            )
            .where(GitMetadata.repository_id == repository_id, GitMetadata.file_path.in_(chunk))
        )
        for path, hot, score, churn, health_path, origin, is_test in rows.all():
            found = stored_origin(origin, is_test) if health_path else path_origin(path)
            if found == "production":
                heat[path] = (bool(hot), float(score or 0.0), float(churn or 0.0))
    return heat


async def discovery_records(
    session: AsyncSession, repository_id: str, *, limit: int = DISCOVERY_LIMIT
) -> list[dict[str, Any]]:
    """Background records on the busiest production files, busiest first.

    Background: never put in force, not retired, nobody has dismissed, merged
    or split it, not an agreement, and not an agent's record waiting for its
    commit to merge. A record must name a production file git has history
    for; its place is its busiest such file (hotspot first, then hotspot
    score, then churn). Each dict names those files, busiest first.
    """
    q = (
        select(DecisionRecord)
        .outerjoin(DecisionCandidateMeta, DecisionCandidateMeta.decision_id == DecisionRecord.id)
        .where(
            DecisionRecord.repository_id == repository_id,
            candidate_predicate(),
            or_(
                DecisionCandidateMeta.decision_id.is_(None),
                DecisionCandidateMeta.review_state == "open",
            ),
            DecisionRecord.source.notin_(_PENDING_SOURCES),
            or_(DecisionRecord.kind.is_(None), DecisionRecord.kind != AGREEMENT_KIND),
        )
    )
    records = [
        (rec, files)
        for rec in (await session.execute(q)).scalars().all()
        if binds_to_paths(rec.scope_basis) and (files := _named_files(rec))
    ]
    heat = await _production_heat(
        session, repository_id, sorted({f for _, files in records for f in files})
    )

    def file_key(path: str) -> tuple[bool, float, float, str]:
        hot, score, churn = heat[path]
        return (not hot, -score, -churn, path)

    ranked = []
    for rec, files in records:
        busy = sorted((f for f in dict.fromkeys(files) if f in heat), key=file_key)
        if busy:
            ranked.append((file_key(busy[0]), -(rec.confidence or 0.0), rec.id, rec, busy))
    ranked.sort(key=lambda item: item[:3])
    return [
        {"id": rec.id, "title": rec.title, "source": rec.source, "files": busy}
        for *_, rec, busy in ranked[:limit]
    ]


async def agreement_records(
    session: AsyncSession, repository_id: str, *, limit: int = AGREEMENTS_LIMIT
) -> list[dict[str, Any]]:
    """Working agreements still stored and not retired, in force ones first, then newest."""
    q = (
        select(DecisionRecord)
        .where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.kind == AGREEMENT_KIND,
            DecisionRecord.status.notin_(sorted(RETIRED_STATUSES)),
        )
        .order_by(accepted_predicate().desc(), DecisionRecord.created_at.desc())
        .limit(limit)
    )
    return [
        {"id": rec.id, "title": rec.title, "decision": rec.decision}
        for rec in (await session.execute(q)).scalars().all()
    ]


async def decision_prompt_for(session: AsyncSession, repository_id: str, kind: str) -> DecisionPrompt:
    """The decision prompt *kind* (``discover``, ``add``, ``agreements``) over this store."""
    if kind == "agreements":
        return decision_prompt(kind, await agreement_records(session, repository_id))
    if kind == "add":
        return decision_prompt(kind, [])
    return decision_prompt(kind, await discovery_records(session, repository_id))
