"""Read the stores into :class:`RepoFacts`, and keep the per-person action state.

Every store is read in its own savepoint and may fail on its own: an index
built before a detector shipped, or an older schema, costs that store's rules
(reported as unavailable, with the reason) rather than the whole list.

The reads here only narrow; the row-to-fact rule is the pure builder in
``repowise.core.analysis.actions.build``.

Index and update store the rules' output as a read snapshot
(:func:`write_actions_snapshot`), keyed by the newest commit and the newest
write to every store the facts come from; a read that finds the key unchanged
ranks the stored actions and skips the facts and the rules.
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
    find_action,
    rank_actions,
    rule_actions,
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
    reach_paths,
    with_leads,
    with_test_reach,
)
from repowise.core.analysis.actions.facts import FileFacts
from repowise.core.analysis.actions.rules.code import FIX_FIRST_ACTIONS
from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS
from repowise.core.analysis.dead_code.risk_factors import REVIEW_ONLY_KINDS
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.test_reachability import any_tests_reaching

from ...models import (
    ActionState,
    CoverageFile,
    CoverageIngest,
    DeadCodeFinding,
    DecisionAcceptance,
    DecisionRecord,
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
from .fix_first import load_fix_first, write_fix_first_snapshot
from .fix_first import stores as fix_first_stores
from .read_snapshots import StorePart, read_snapshot, refresh_snapshot, snapshot_key, store_stamp

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


async def _history(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    count = select(func.count()).select_from(GitCommit).where(GitCommit.repository_id == repo_id)
    commits = (await session.execute(count)).scalar_one()
    if not commits:
        # No commit rows and no per-file history: git was never indexed, which
        # is unknown, not a short history.
        indexed = select(GitMetadata.file_path).where(GitMetadata.repository_id == repo_id)
        if (await session.execute(indexed.limit(1))).first() is None:
            return {"history_commits": None}
    return {"history_commits": commits}


async def _test_map(session: AsyncSession, repo_id: str, files: dict[str, FileFacts]) -> dict:
    """Only a file no test reaches may be told to add tests, so only those few are walked."""
    paths = reach_paths(files)
    if not paths:
        return {}
    return {"files": with_test_reach(files, await any_tests_reaching(session, repo_id, paths))}


async def _fix_first(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    # An action names the item and links to its full read; it carries no tests.
    queue = await load_fix_first(session, repo_id, limit=FIX_FIRST_ACTIONS, verify=False)
    return {"fix_first": queue.items}


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

    await read("history", lambda: _history(session, repo_id))
    await read("files", lambda: _files(session, repo_id, since))
    if "files" in values:
        await read("test_map", lambda: _test_map(session, repo_id, values["files"]))
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


#: The stored rules' output's ``read_snapshots.kind``.
SNAPSHOT_KIND = "actions"


def _stores(repo_id: str) -> list[StorePart]:
    """Every store :func:`load_repo_facts` reads, as :func:`store_stamp` parts.

    The person's answers are not among them: they are applied on every read.
    Commit files and renamed paths move with the commit rows; acceptances are
    append-only, so their count moves.
    """
    timed = (
        (GitCommit.updated_at, GitCommit.repository_id),
        (GitMetadata.updated_at, GitMetadata.repository_id),
        (GraphMetric.created_at, GraphMetric.repository_id),
        (FixEvent.updated_at, FixEvent.repository_id),
        (GitCommitHealthFinding.updated_at, GitCommitHealthFinding.repository_id),
        (SecurityFinding.detected_at, SecurityFinding.repository_id),
        (DocDriftFinding.detected_at, DocDriftFinding.repository_id),
        (DeadCodeFinding.analyzed_at, DeadCodeFinding.repository_id),
        (DecisionRecord.updated_at, DecisionRecord.repository_id),
        (None, DecisionAcceptance.repository_id),
        (CoverageFile.ingested_at, CoverageFile.repository_id),
        (CoverageIngest.ingested_at, CoverageIngest.repository_id),
    )
    return [*fix_first_stores(repo_id), *((col, owner == repo_id) for col, owner in timed)]


async def _snapshot_key(session: AsyncSession, repo_id: str) -> str:
    _, head_sha = await _anchor(session, repo_id)
    return snapshot_key(head_sha, *await store_stamp(session, _stores(repo_id)))


async def _ruled(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """The rules' output from the facts as stored now, and what could not be read."""
    facts = await load_repo_facts(session, repo_id)
    return {**rule_actions(facts), "unavailable": dict(facts.unavailable)}


async def write_actions_snapshot(session: AsyncSession, repo_id: str) -> bool:
    """Store the rules' output for the next reader; ``False`` when the stored
    one was already built from these stores."""
    key = await _snapshot_key(session, repo_id)
    return await refresh_snapshot(
        session, repo_id, SNAPSHOT_KIND, key, lambda: _ruled(session, repo_id)
    )


async def write_read_snapshots(session: AsyncSession, repo_id: str) -> None:
    """Store the Fix first queue, then the actions view that quotes its head.

    For a writer whose stores are final for this run. Best-effort, each in its
    own savepoint: a view that fails to build is built live on read instead.
    """
    for kind, write in (
        ("fix_first", write_fix_first_snapshot),
        (SNAPSHOT_KIND, write_actions_snapshot),
    ):
        try:
            async with session.begin_nested():
                await write(session, repo_id)
        except Exception as exc:  # a reader builds live instead
            logger.warning("read snapshot %s not written: %s", kind, exc)


async def load_actions_view(
    session: AsyncSession, repo_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """The one entry point: the rules' output, the person's answers, and the ranked view.

    The rules' output is the stored snapshot when it was built from the stores
    as they are now, else built from the facts here. Never writes.
    """
    ruled = await read_snapshot(
        session, repo_id, SNAPSHOT_KIND, lambda: _snapshot_key(session, repo_id)
    )
    if ruled is None:
        ruled = await _ruled(session, repo_id)
    try:
        async with session.begin_nested():
            states = await get_action_states(session, repo_id)
    except Exception:
        states = {}
    view = rank_actions(ruled, states, now=now or datetime.now(UTC))
    view["unavailable"] = dict(ruled["unavailable"])
    return view


async def load_action(session: AsyncSession, repo_id: str, action_id: str) -> Action | None:
    """One action by id: the same facts the list reads, without ranking the view."""
    return find_action(await load_repo_facts(session, repo_id), action_id)
