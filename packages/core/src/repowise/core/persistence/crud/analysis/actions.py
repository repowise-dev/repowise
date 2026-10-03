"""Read the stores into :class:`RepoFacts`, and keep the per-person action state.

Every store is read in its own savepoint and may fail on its own: an index
built before a detector shipped, or an older schema, costs that store's rules
(reported as unavailable, with the reason) rather than the whole list.

The reads here only narrow; the row-to-fact rule is the pure builder in
``repowise.core.analysis.actions.build``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.actions import (
    Action,
    ActionStateRecord,
    RepoFacts,
    compose_actions,
    find_action,
)
from repowise.core.analysis.actions.build import (
    ABSENT,
    AUTHORED_BASES,
    QUARTER,
    WEEK,
    build_authors,
    build_coverage,
    build_dead,
    build_decisions,
    build_drift,
    build_files,
    build_recent,
    build_secrets,
    lead_paths,
    with_leads,
)
from repowise.core.analysis.actions.facts import FileFacts
from repowise.core.analysis.actions.rules.code import FIX_FIRST_ACTIONS
from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS
from repowise.core.analysis.dead_code.risk_factors import REVIEW_ONLY_KINDS
from repowise.core.analysis.finding_registry import excluded_types

from ...models import (
    ActionState,
    CoverageFile,
    CoverageIngest,
    DeadCodeFinding,
    DocDriftFinding,
    FixEvent,
    GitCommit,
    GitCommitFile,
    GitCommitHealthFinding,
    GitMetadata,
    GraphMetric,
    HealthFileMetric,
    HealthFinding,
    SecurityFinding,
)
from .fix_first import load_fix_first

logger = logging.getLogger(__name__)

_HEALTH_FINDING_COLUMNS = (
    HealthFinding.file_path,
    HealthFinding.biomarker_type,
    HealthFinding.severity,
    HealthFinding.function_name,
    HealthFinding.line_start,
    HealthFinding.reason,
    HealthFinding.health_impact,
)


async def _anchor(session: AsyncSession, repo_id: str) -> tuple[datetime | None, str | None]:
    row = (
        await session.execute(
            select(GitCommit.committed_at, GitCommit.sha)
            .where(GitCommit.repository_id == repo_id, GitCommit.committed_at.is_not(None))
            .order_by(GitCommit.committed_at.desc())
            .limit(1)
        )
    ).first()
    return (row.committed_at, row.sha) if row else (None, None)


async def _files(session: AsyncSession, repo_id: str, since: datetime | None) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                GitMetadata.file_path,
                GitMetadata.commit_count_90d,
                GitMetadata.last_commit_at,
                GitMetadata.bug_magnet,
                GitMetadata.bus_factor,
                GitMetadata.primary_owner_name,
                GitMetadata.primary_owner_email,
                GitMetadata.primary_owner_commit_pct,
                HealthFileMetric.is_test,
                HealthFileMetric.score,
                HealthFileMetric.max_ccn,
                HealthFileMetric.nloc,
                HealthFileMetric.line_coverage_pct,
            )
            .outerjoin(
                HealthFileMetric,
                (HealthFileMetric.repository_id == GitMetadata.repository_id)
                & (HealthFileMetric.file_path == GitMetadata.file_path),
            )
            .where(GitMetadata.repository_id == repo_id)
        )
    ).all()
    dependents = dict(
        (
            await session.execute(
                select(GraphMetric.node_id, GraphMetric.in_degree).where(
                    GraphMetric.repository_id == repo_id
                )
            )
        ).all()
    )
    fix_events: list[Any] = []
    if since is not None:
        fix_events = (
            await session.execute(
                select(
                    FixEvent.file_path,
                    FixEvent.fix_sha,
                    FixEvent.committed_at,
                    FixEvent.shape_kind,
                    FixEvent.attribution,
                ).where(
                    FixEvent.repository_id == repo_id,
                    FixEvent.committed_at >= since,
                    FixEvent.shape_kind == "code_fix",
                    FixEvent.attribution != "none",
                )
            )
        ).all()
    out = build_files(rows, since=since, fix_events=fix_events, dependents=dependents)
    # The lead lookup stays keyed to the few files that get one.
    paths = lead_paths(out["files"])
    if paths:
        findings = (
            await session.execute(
                select(*_HEALTH_FINDING_COLUMNS).where(
                    HealthFinding.repository_id == repo_id,
                    HealthFinding.status == "open",
                    HealthFinding.file_path.in_(paths),
                    HealthFinding.biomarker_type.not_in(excluded_types()),
                )
            )
        ).all()
        out["files"] = with_leads(out["files"], findings)
    return out


async def _authors(session: AsyncSession, repo_id: str, since: datetime | None) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                GitCommit.author_name,
                GitCommit.author_email,
                func.max(GitCommit.committed_at).label("committed_at"),
            )
            .where(GitCommit.repository_id == repo_id)
            .group_by(GitCommit.author_name, GitCommit.author_email)
        )
    ).all()
    return build_authors(rows, since=since)


async def _recent(
    session: AsyncSession, repo_id: str, since: datetime | None, files: dict[str, FileFacts]
) -> dict[str, Any]:
    if since is None:
        return {}
    rows = (
        await session.execute(
            select(
                GitCommitHealthFinding.sha,
                GitCommitHealthFinding.file_path,
                GitCommitHealthFinding.symbol,
                GitCommitHealthFinding.biomarker_type,
                GitCommitHealthFinding.severity,
                GitCommitHealthFinding.change_kind,
                GitCommitHealthFinding.line_start,
                GitCommitHealthFinding.reason,
                GitCommitHealthFinding.attribution_basis,
                GitCommit.subject,
                GitCommit.committed_at,
            )
            .join(
                GitCommit,
                (GitCommit.repository_id == GitCommitHealthFinding.repository_id)
                & (GitCommit.sha == GitCommitHealthFinding.sha),
            )
            .where(
                GitCommitHealthFinding.repository_id == repo_id,
                GitCommit.committed_at >= since,
                GitCommitHealthFinding.change_kind.in_(("introduced", "worsened")),
                GitCommitHealthFinding.severity.in_(("critical", "high")),
                GitCommitHealthFinding.attribution_basis.in_(AUTHORED_BASES),
            )
        )
    ).all()
    open_findings: list[Any] = []
    if rows:
        open_findings = (
            await session.execute(
                select(*_HEALTH_FINDING_COLUMNS).where(
                    HealthFinding.repository_id == repo_id,
                    HealthFinding.status == "open",
                    HealthFinding.file_path.in_({r.file_path for r in rows}),
                )
            )
        ).all()
    return build_recent(rows, week=since, open_findings=open_findings, files=files)


async def _fix_first(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    return {"fix_first": (await load_fix_first(session, repo_id, limit=FIX_FIRST_ACTIONS)).items}


async def _secrets(session: AsyncSession, repo_id: str, files: dict[str, FileFacts]) -> dict:
    rows = (
        await session.execute(
            select(
                SecurityFinding.file_path,
                SecurityFinding.kind,
                SecurityFinding.line_number,
                SecurityFinding.snippet,
                SecurityFinding.severity,
                SecurityFinding.commit_sha,
            ).where(
                SecurityFinding.repository_id == repo_id,
                SecurityFinding.commit_sha == "",
                SecurityFinding.severity == "high",
                SecurityFinding.kind.in_((*SECRET_KINDS, PUBLIC_ENV_KIND)),
            )
        )
    ).all()
    return build_secrets(rows, files)


async def _drift(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                DocDriftFinding.file_path,
                DocDriftFinding.kind,
                DocDriftFinding.target,
                DocDriftFinding.raw,
                DocDriftFinding.line_number,
                DocDriftFinding.reason,
                DocDriftFinding.confidence,
            ).where(DocDriftFinding.repository_id == repo_id)
        )
    ).all()
    path_targets = {r.target for r in rows if r.kind == "path" and r.target}
    known: set[str] = set()
    if path_targets:
        known = set(
            (
                await session.execute(
                    select(GitCommitFile.file_path)
                    .where(
                        GitCommitFile.repository_id == repo_id,
                        GitCommitFile.file_path.in_(path_targets),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        known |= set(
            (
                await session.execute(
                    select(GitMetadata.original_path).where(
                        GitMetadata.repository_id == repo_id,
                        GitMetadata.original_path.in_(path_targets),
                    )
                )
            )
            .scalars()
            .all()
        )
    return build_drift(rows, known)


async def _dead(session: AsyncSession, repo_id: str, files: dict[str, FileFacts]) -> dict:
    rows = (
        await session.execute(
            select(
                DeadCodeFinding.id,
                DeadCodeFinding.file_path,
                DeadCodeFinding.symbol_name,
                DeadCodeFinding.lines,
                DeadCodeFinding.kind,
                DeadCodeFinding.safe_to_delete,
                DeadCodeFinding.status,
            ).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.status == "open",
                DeadCodeFinding.safe_to_delete.is_(True),
                DeadCodeFinding.kind.not_in(excluded_types() | REVIEW_ONLY_KINDS),
            )
        )
    ).all()
    return build_dead(rows, files)


async def _decisions(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    from ..decision_health import get_decision_health_summary

    return build_decisions(await get_decision_health_summary(session, repo_id))


async def _coverage(session: AsyncSession, repo_id: str, head_sha: str | None) -> dict:
    count, latest_at, latest_sha = (
        await session.execute(
            select(
                func.count(CoverageFile.id),
                func.max(CoverageFile.ingested_at),
                func.max(CoverageFile.ingested_commit_sha),
            ).where(CoverageFile.repository_id == repo_id)
        )
    ).one()
    partial = False
    if count:
        # The ingest record is authoritative for when, at which commit, and
        # whether the report's paths all mapped. Indexes that predate it carry
        # the same commit on every row, because one ingest replaces them all.
        try:
            async with session.begin_nested():
                ingest = (
                    await session.execute(
                        select(
                            CoverageIngest.ingested_at,
                            CoverageIngest.ingested_commit_sha,
                            CoverageIngest.mapping_partial,
                        )
                        .where(CoverageIngest.repository_id == repo_id)
                        .order_by(CoverageIngest.ingested_at.desc())
                        .limit(1)
                    )
                ).first()
            if ingest is not None:
                latest_at, latest_sha, partial = (
                    ingest.ingested_at,
                    ingest.ingested_commit_sha,
                    bool(ingest.mapping_partial),
                )
        except Exception:  # an index from before ingests were recorded
            pass
    return build_coverage(
        {
            "files_measured": count,
            "ingested_at": latest_at,
            "ingested_commit_sha": latest_sha,
            "partial": partial,
        },
        head_sha,
    )


_Reader = Callable[[], Awaitable[dict[str, Any]]]


async def load_repo_facts(session: AsyncSession, repo_id: str) -> RepoFacts:
    anchor, head_sha = await _anchor(session, repo_id)
    since = (anchor - QUARTER) if anchor else None
    week = (anchor - WEEK) if anchor else None
    values: dict[str, Any] = {"anchor": anchor}
    unavailable: dict[str, str] = {}

    async def read(store: str, reader: _Reader) -> None:
        try:
            async with session.begin_nested():
                values.update(await reader())
        except Exception as exc:  # one store must not cost the list
            logger.warning("actions: %s unavailable: %s", store, exc)
            unavailable[store] = ABSENT

    await read("files", lambda: _files(session, repo_id, since))
    files: dict[str, FileFacts] = values.get("files") or {}
    await read("authors", lambda: _authors(session, repo_id, since))
    await read("commit_health", lambda: _recent(session, repo_id, week, files))
    await read("fix_first", lambda: _fix_first(session, repo_id))
    await read("security", lambda: _secrets(session, repo_id, files))
    await read("doc_drift", lambda: _drift(session, repo_id))
    await read("dead_code", lambda: _dead(session, repo_id, files))
    await read("decisions", lambda: _decisions(session, repo_id))
    await read("coverage", lambda: _coverage(session, repo_id, head_sha))
    values["unavailable"] = unavailable
    return RepoFacts(**values)


async def get_action_states(session: AsyncSession, repo_id: str) -> dict[str, ActionStateRecord]:
    rows = (
        (await session.execute(select(ActionState).where(ActionState.repository_id == repo_id)))
        .scalars()
        .all()
    )
    return {r.action_id: ActionStateRecord(r.state, r.fingerprint, r.until) for r in rows}


async def set_action_state(
    session: AsyncSession,
    repo_id: str,
    action_id: str,
    *,
    state: str | None,
    fingerprint: str = "",
    until: datetime | None = None,
) -> None:
    """Record, replace, or (``state=None``) clear a person's answer to one action."""
    row = (
        await session.execute(
            select(ActionState).where(
                ActionState.repository_id == repo_id, ActionState.action_id == action_id
            )
        )
    ).scalar_one_or_none()
    if state is None:
        if row is not None:
            await session.delete(row)
        return
    if row is None:
        session.add(
            ActionState(
                repository_id=repo_id,
                action_id=action_id,
                state=state,
                fingerprint=fingerprint,
                until=until,
            )
        )
    else:
        row.state, row.fingerprint, row.until = state, fingerprint, until


async def load_actions_view(
    session: AsyncSession, repo_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """The one entry point: facts, the person's answers, and the ranked view."""
    facts = await load_repo_facts(session, repo_id)
    try:
        async with session.begin_nested():
            states = await get_action_states(session, repo_id)
    except Exception:
        states = {}
    view = compose_actions(facts, states, now=now or datetime.now(UTC))
    view["unavailable"] = dict(facts.unavailable)
    return view


async def load_action(session: AsyncSession, repo_id: str, action_id: str) -> Action | None:
    """One action by id: the same facts the list reads, without ranking the view."""
    return find_action(await load_repo_facts(session, repo_id), action_id)
