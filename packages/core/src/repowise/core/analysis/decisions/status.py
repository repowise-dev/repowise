"""What decision capture did, assembled from what capture actually persisted.

There is no capture-run ledger, so every figure here is derived from a durable
trace capture left behind, and the report omits what it cannot attribute rather
than guessing at it.

Lives in core rather than in the CLI so the API can answer the same question.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud.authority import (
    accepted_decision_ids,
    candidate_predicate,
    count_decisions_by_lane,
    record_blockers,
)
from repowise.core.persistence.models import (
    DecisionCandidateMeta,
    DecisionRecord,
    LlmCost,
    _now_utc,
)
from repowise.core.sessions.staging import default_store_path

from .lifecycle import CANDIDATE_REVIEW_STATES
from .policy import SOURCE_SPECS, SourceRuntime
from .policy_store import load_policy
from .provenance import RETIRED_SOURCES

#: The ``LlmCost.operation`` every decision-extraction call is booked under.
#: One label covers the index sources and the broad session lane alike, so
#: spend cannot be split between them here.
CAPTURE_COST_OPERATION = "decision_extraction"

_SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True, slots=True)
class _Backlog:
    """Queue depth from the session staging store, or why it could not be read."""

    available: bool
    reason: str = ""
    values: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"available": self.available}
        if self.reason:
            payload["reason"] = self.reason
        payload.update(self.values or {})
        return payload


def _age_days(then: datetime, now: datetime) -> float:
    """Age in days. SQLite hands back naive datetimes, and they are UTC."""
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    return round(max(0.0, (now - then).total_seconds()) / _SECONDS_PER_DAY, 2)


async def _per_source(
    session: AsyncSession, repository_id: str, accepted: set[str]
) -> dict[str, dict[str, Any]]:
    """Records, accepted count and newest capture per stored source value.

    Tombstoned candidates are excluded, because ``count_decisions_by_lane``
    excludes them: counting them here would make the Sources column sum to
    more than the total beside it.
    """
    rows = (
        await session.execute(
            select(DecisionRecord.id, DecisionRecord.source, DecisionRecord.created_at).where(
                DecisionRecord.repository_id == repository_id,
                or_(DecisionRecord.status != "dismissed", DecisionRecord.id.in_(accepted)),
            )
        )
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for did, source, created_at in rows:
        bucket = out.setdefault(
            source, {"records": 0, "accepted": 0, "candidates": 0, "last_captured": None}
        )
        bucket["records"] += 1
        bucket["accepted" if did in accepted else "candidates"] += 1
        if created_at is not None and (
            bucket["last_captured"] is None or created_at > bucket["last_captured"]
        ):
            bucket["last_captured"] = created_at
    return out


async def _review(session: AsyncSession, repository_id: str) -> dict[str, Any]:
    """Review-state counts and the age of the unreviewed backlog."""
    states = dict(
        (
            await session.execute(
                select(DecisionCandidateMeta.review_state, func.count())
                .where(DecisionCandidateMeta.repository_id == repository_id)
                .group_by(DecisionCandidateMeta.review_state)
            )
        ).all()
    )
    # Asked of the record rather than read off the stored review priority, so
    # this and `decision candidates` cannot report different backlogs for one
    # store. Having no review row at all is a separate count, not a blocker.
    open_rows = (
        await session.execute(
            select(DecisionRecord, DecisionCandidateMeta.decision_id)
            .outerjoin(
                DecisionCandidateMeta,
                DecisionCandidateMeta.decision_id == DecisionRecord.id,
            )
            .where(
                DecisionRecord.repository_id == repository_id,
                candidate_predicate(),
            )
        )
    ).all()

    now = _now_utc()
    days = sorted(_age_days(record.created_at, now) for record, _ in open_rows)
    acceptable = sum(1 for record, _ in open_rows if not record_blockers(record))
    no_review_row = sum(1 for _, meta_id in open_rows if meta_id is None)
    middle = len(days) // 2
    return {
        "states": {state: int(states.get(state, 0)) for state in CANDIDATE_REVIEW_STATES},
        "unreviewed": len(days),
        "acceptable": acceptable,
        "blocked": len(days) - acceptable,
        "no_review_row": no_review_row,
        "oldest_age_days": days[-1] if days else 0.0,
        "median_age_days": (
            0.0
            if not days
            else days[middle]
            if len(days) % 2
            else round((days[middle - 1] + days[middle]) / 2, 2)
        ),
    }


def _backlog(repo_path: Path) -> _Backlog:
    """Staging-queue depth, read without creating or migrating the store.

    Opened read-only on purpose: ``SessionStagingStore`` reconciles its schema
    on construction, and a report has no business writing a queue table into a
    store that predates it.
    """
    db_path = default_store_path(repo_path)
    if not db_path.exists():
        return _Backlog(False, "No session staging store yet.")

    queries = {
        "discovery_spans_pending": "SELECT COUNT(*) FROM discovery_spans WHERE consumed_at IS NULL",
        "discovery_spans_retrying": (
            "SELECT COUNT(*) FROM discovery_spans WHERE consumed_at IS NULL AND attempts > 0"
        ),
        "raw_candidates_pending": (
            "SELECT COUNT(*) FROM raw_candidates WHERE structured_key IS NULL"
        ),
        "session_decisions_unpromoted": "SELECT COUNT(*) FROM decisions WHERE promoted_at IS NULL",
    }
    values: dict[str, int] = {}
    try:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return _Backlog(False, f"Session staging store unreadable: {exc}")
    try:
        for key, sql in queries.items():
            try:
                values[key] = int(conn.execute(sql).fetchone()[0])
            except sqlite3.Error as exc:
                # A store written before this queue existed has no such table,
                # and absent is not zero, so the key is left out rather than
                # faked. Anything else is this store being unreadable — a WAL
                # database with no companion file cannot be opened read-only —
                # and reporting that as an empty queue would be a lie.
                if "no such table" not in str(exc):
                    return _Backlog(False, f"Session staging store unreadable: {exc}")
                continue
    finally:
        conn.close()
    if not values:
        return _Backlog(False, "Session staging store has no queue tables yet.")
    return _Backlog(True, values=values)


async def _cost(session: AsyncSession, repository_id: str) -> dict[str, Any]:
    """Model spend booked to decision extraction.

    Totals are all-time and ``last_call`` is one row, because nothing records
    which call belonged to which run: a per-run figure would be a guess at a
    boundary the store does not have.
    """
    totals = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LlmCost.input_tokens), 0),
                func.coalesce(func.sum(LlmCost.output_tokens), 0),
                func.coalesce(func.sum(LlmCost.cost_usd), 0.0),
            ).where(
                LlmCost.repository_id == repository_id,
                LlmCost.operation == CAPTURE_COST_OPERATION,
            )
        )
    ).one()
    last = (
        await session.execute(
            select(LlmCost.ts, LlmCost.model, LlmCost.input_tokens, LlmCost.output_tokens)
            .where(
                LlmCost.repository_id == repository_id,
                LlmCost.operation == CAPTURE_COST_OPERATION,
            )
            .order_by(LlmCost.ts.desc(), LlmCost.id.desc())
            .limit(1)
        )
    ).first()
    return {
        "operation": CAPTURE_COST_OPERATION,
        "calls": int(totals[0]),
        "input_tokens": int(totals[1]),
        "output_tokens": int(totals[2]),
        "cost_usd": round(float(totals[3]), 6),
        "last_call": (
            None
            if last is None
            else {
                "at": last[0],
                "model": last[1],
                "input_tokens": int(last[2]),
                "output_tokens": int(last[3]),
            }
        ),
    }


async def capture_status(
    session: AsyncSession,
    repository_id: str,
    repo_path: Path,
    *,
    provider_available: bool,
) -> dict[str, Any]:
    """The one capture report, shared by every surface that asks for it."""
    resolution = load_policy(repo_path)
    policy = resolution.policy

    accepted = await accepted_decision_ids(session, repository_id)
    by_source = await _per_source(session, repository_id, accepted)

    sources: list[dict[str, Any]] = []
    for runtime in policy.runtime(provider_available=provider_available):
        captured = by_source.get(runtime.key, {})
        sources.append(
            {
                **runtime.to_dict(),
                "records": captured.get("records", 0),
                "accepted": captured.get("accepted", 0),
                "candidates": captured.get("candidates", 0),
                "last_captured": captured.get("last_captured"),
            }
        )
    # A retired source keeps its stored rows, and a row whose source has no
    # spec would otherwise vanish from a report that claims to be a census.
    # Built through SourceRuntime so it is the same row shape as the rest:
    # the API's source model requires every field a spec-backed row carries.
    known = {spec.key for spec in SOURCE_SPECS}
    for key in sorted(set(by_source) - known):
        retired = key in RETIRED_SOURCES
        sources.append(
            {
                **SourceRuntime(
                    key=key,
                    label=key,
                    description=(
                        "Retired source; stored rows only."
                        if retired
                        else "Unknown source; stored rows only."
                    ),
                    authority="machine",
                    deterministic=False,
                    supports_llm=False,
                    togglable=False,
                    enabled=False,
                    llm_enabled=False,
                    status="disabled",
                    reason=(
                        "This source no longer runs."
                        if retired
                        else "This source is not in the registry."
                    ),
                ).to_dict(),
                **by_source[key],
            }
        )

    # The policy's own source list is dropped: ``sources`` below is the same
    # runtime rows with the capture counts joined on, and two lists that can
    # disagree is the drift this layer exists to stop having.
    resolved = policy.to_dict(provider_available=provider_available)
    resolved.pop("sources", None)
    return {
        "policy": {
            **resolved,
            "provider_available": provider_available,
            "warnings": list(resolution.warnings),
        },
        "sources": sources,
        "lanes": await count_decisions_by_lane(session, repository_id),
        "review": await _review(session, repository_id),
        "backlog": _backlog(repo_path).to_dict(),
        "cost": await _cost(session, repository_id),
    }
