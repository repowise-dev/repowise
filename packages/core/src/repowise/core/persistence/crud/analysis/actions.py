"""Read the stores into :class:`RepoFacts`, and keep the per-person action state.

Every store is read in its own savepoint and may fail on its own: an index
built before a detector shipped, or an older schema, costs that store's rules
(reported as unavailable, with the reason) rather than the whole list.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.actions import ActionStateRecord, RepoFacts, compose_actions
from repowise.core.analysis.actions.facts import (
    CoverageState,
    DeadFacts,
    DecisionFacts,
    DriftFacts,
    FileFacts,
    LeadFinding,
    PerfFacts,
    RecentFinding,
    SecretFacts,
)
from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.scoring import HISTORY_CATEGORY, biomarker_category
from repowise.core.author_identity import author_identity_key

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
    PerformanceOpportunity,
    SecurityFinding,
)

logger = logging.getLogger(__name__)

QUARTER = timedelta(days=90)
WEEK = timedelta(days=7)

#: Attribution bases that tie a finding to lines the commit wrote. A
#: ``file_change`` basis only says the commit touched the file, which is not
#: enough to tell someone they made it worse.
AUTHORED_BASES = ("added_lines", "new_file", "changed_symbol")

#: Files whose lead finding is looked up: busy bug magnets only, so the lookup
#: stays a keyed read whatever the repository's size.
LEAD_LOOKUP_MIN_COMMITS = 5


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
    fix_shas: dict[str, set[str]] = defaultdict(set)
    all_fix: set[str] = set()
    if since is not None:
        for path, sha in (
            await session.execute(
                select(FixEvent.file_path, FixEvent.fix_sha).where(
                    FixEvent.repository_id == repo_id,
                    FixEvent.committed_at >= since,
                    FixEvent.shape_kind == "code_fix",
                    FixEvent.attribution != "none",
                )
            )
        ).all():
            fix_shas[path].add(sha)
            all_fix.add(sha)

    files: dict[str, FileFacts] = {}
    for r in rows:
        files[r.file_path] = FileFacts(
            path=r.file_path,
            is_test=bool(r.is_test),
            score=r.score,
            max_ccn=r.max_ccn,
            nloc=r.nloc,
            line_coverage_pct=r.line_coverage_pct,
            commits_90d=r.commit_count_90d or 0,
            last_commit_at=r.last_commit_at,
            bug_magnet=bool(r.bug_magnet),
            fix_commits_90d=len(fix_shas.get(r.file_path, ())),
            bus_factor=r.bus_factor,
            owner_key=(
                author_identity_key(r.primary_owner_name, r.primary_owner_email)
                if (r.primary_owner_name or r.primary_owner_email)
                else None
            ),
            owner_name=r.primary_owner_name,
            owner_pct=r.primary_owner_commit_pct,
            dependents=dependents.get(r.file_path),
        )

    # Lead finding for the busy bug magnets: the code-shape finding an edit can
    # fix. History markers are context, never the thing to do.
    lead_paths = [
        p
        for p, f in files.items()
        if f.bug_magnet
        and not f.is_test
        and f.commits_90d >= LEAD_LOOKUP_MIN_COMMITS
        and f.fix_commits_90d >= 3
    ]
    if lead_paths:
        findings = (
            await session.execute(
                select(
                    HealthFinding.file_path,
                    HealthFinding.biomarker_type,
                    HealthFinding.severity,
                    HealthFinding.function_name,
                    HealthFinding.line_start,
                    HealthFinding.reason,
                    HealthFinding.health_impact,
                ).where(
                    HealthFinding.repository_id == repo_id,
                    HealthFinding.status == "open",
                    HealthFinding.file_path.in_(lead_paths),
                    HealthFinding.biomarker_type.not_in(excluded_types()),
                )
            )
        ).all()
        by_path: dict[str, list] = defaultdict(list)
        for f in findings:
            by_path[f.file_path].append(f)
        for path, found in by_path.items():
            shape, _history = split_by_origin(found)
            lead = primary_finding(shape)
            if lead is not None:
                files[path] = replace(
                    files[path],
                    lead=LeadFinding(
                        biomarker=lead.biomarker_type,
                        severity=lead.severity,
                        function=lead.function_name,
                        line=lead.line_start,
                        reason=lead.reason or "",
                    ),
                )
    return {
        "files": files,
        "fix_shas_by_file": {p: frozenset(s) for p, s in fix_shas.items()},
        "fix_commits_90d": len(all_fix),
    }


async def _authors(session: AsyncSession, repo_id: str, since: datetime | None) -> dict[str, Any]:
    last: dict[str, datetime] = {}
    active: set[str] = set()
    for name, email, latest in (
        await session.execute(
            select(
                GitCommit.author_name,
                GitCommit.author_email,
                func.max(GitCommit.committed_at),
            )
            .where(GitCommit.repository_id == repo_id)
            .group_by(GitCommit.author_name, GitCommit.author_email)
        )
    ).all():
        if latest is None:
            continue
        key = author_identity_key(name, email)
        if key not in last or latest > last[key]:
            last[key] = latest
        if since is not None and latest >= since:
            active.add(key)
    return {"author_last_commit": last, "active_authors_90d": len(active)}


async def _recent(
    session: AsyncSession, repo_id: str, since: datetime | None, files: dict[str, FileFacts]
) -> dict[str, Any]:
    if since is None:
        return {}
    rows = (
        await session.execute(
            select(GitCommitHealthFinding, GitCommit.subject, GitCommit.committed_at)
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
    candidates = [
        (f, subject, at)
        for f, subject, at in rows
        if not (files.get(f.file_path) and files[f.file_path].is_test)
        and biomarker_category(f.biomarker_type) != HISTORY_CATEGORY
        and f.biomarker_type not in excluded_types()
    ]
    if not candidates:
        return {"recent_findings": ()}
    paths = {f.file_path for f, _, _ in candidates}
    open_keys = {
        (p, b, fn)
        for p, b, fn in (
            await session.execute(
                select(
                    HealthFinding.file_path,
                    HealthFinding.biomarker_type,
                    HealthFinding.function_name,
                ).where(
                    HealthFinding.repository_id == repo_id,
                    HealthFinding.status == "open",
                    HealthFinding.file_path.in_(paths),
                )
            )
        ).all()
    }
    recent = []
    seen: set[tuple[str, str, str | None]] = set()
    for f, subject, at in sorted(candidates, key=lambda r: r[2] or datetime.min, reverse=True):
        key = (f.file_path, f.biomarker_type, f.symbol)
        # Still open, and counted once however many commits touched it.
        if key not in open_keys or key in seen:
            continue
        seen.add(key)
        recent.append(
            RecentFinding(
                sha=f.sha,
                subject=subject or "",
                committed_at=at,
                file_path=f.file_path,
                symbol=f.symbol,
                biomarker=f.biomarker_type,
                severity=f.severity,
                change_kind=f.change_kind,
                line=f.line_start,
                reason=f.reason or "",
            )
        )
    return {"recent_findings": tuple(recent)}


async def _perf(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(PerformanceOpportunity).where(
                    PerformanceOpportunity.repository_id == repo_id,
                    PerformanceOpportunity.status == "open",
                    PerformanceOpportunity.execution_context == "production",
                    PerformanceOpportunity.actionability_state.in_(("plan_ready", "advisory")),
                )
            )
        )
        .scalars()
        .all()
    )
    out = []
    for r in rows:
        try:
            details = json.loads(r.details_json or "{}")
        except ValueError:
            details = {}
        facets = details.get("facets") or {}
        plan = details.get("plan") or {}
        out.append(
            PerfFacts(
                opportunity_id=r.opportunity_id,
                biomarker=r.biomarker_type,
                boundary=r.boundary_kind,
                file_path=r.file_path,
                symbol=r.intervention_symbol,
                call_sites=r.affected_call_sites_total or 0,
                files=r.affected_files_total or 0,
                actionability=r.actionability_state,
                exposure=facets.get("exposure"),
                loop_magnitude=facets.get("loop_magnitude"),
                effort=plan.get("effort_bucket"),
            )
        )
    return {"perf": tuple(out)}


async def _secrets(session: AsyncSession, repo_id: str, files: dict[str, FileFacts]) -> dict:
    rows = (
        await session.execute(
            select(
                SecurityFinding.file_path,
                SecurityFinding.kind,
                SecurityFinding.line_number,
                SecurityFinding.snippet,
            ).where(
                SecurityFinding.repository_id == repo_id,
                SecurityFinding.commit_sha == "",
                SecurityFinding.severity == "high",
                SecurityFinding.kind.in_((*SECRET_KINDS, PUBLIC_ENV_KIND)),
            )
        )
    ).all()
    return {
        "secrets": tuple(
            SecretFacts(r.file_path, r.kind, r.line_number, r.snippet or "")
            for r in rows
            if not (files.get(r.file_path) and files[r.file_path].is_test)
            and not _test_path(r.file_path)
        )
    }


def _test_path(path: str) -> bool:
    lowered = path.lower()
    return any(
        seg in lowered
        for seg in ("/tests/", "/test/", "__tests__", "/fixtures/", ".test.", ".spec.", "_test.")
    ) or lowered.startswith(("tests/", "test/"))


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
    return {
        "drift": tuple(
            DriftFacts(
                document=r.file_path,
                kind=r.kind,
                target=r.target or "",
                raw=r.raw or "",
                line=r.line_number,
                reason=r.reason or "",
                confidence=float(r.confidence or 0.0),
                target_known=(r.target in known),
            )
            for r in rows
        )
    }


async def _dead(session: AsyncSession, repo_id: str, files: dict[str, FileFacts]) -> dict:
    rows = (
        await session.execute(
            select(
                DeadCodeFinding.id,
                DeadCodeFinding.file_path,
                DeadCodeFinding.symbol_name,
                DeadCodeFinding.lines,
            ).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.status == "open",
                DeadCodeFinding.safe_to_delete.is_(True),
                DeadCodeFinding.kind.not_in(excluded_types()),
            )
        )
    ).all()
    return {
        "dead": tuple(
            DeadFacts(r.id, r.file_path, r.symbol_name, r.lines or 0)
            for r in rows
            if not (files.get(r.file_path) and files[r.file_path].is_test)
            and not _test_path(r.file_path)
        )
    }


async def _decisions(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    from ..decision_health import get_decision_health_summary

    summary = await get_decision_health_summary(session, repo_id)
    counts = summary.get("summary") or {}
    return {
        "stale_decisions": tuple(
            DecisionFacts(d.id, d.title) for d in summary.get("stale_decisions") or []
        ),
        "proposed_decisions": int(counts.get("proposed") or 0),
        "accepted_decisions": int(counts.get("active") or 0),
    }


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
    if not count:
        return {"coverage": CoverageState("unknown")}
    # The ingest record is authoritative for when, at which commit, and whether
    # the report's paths all mapped. Indexes that predate it carry the same
    # commit on every row, because one ingest replaces them all.
    partial = False
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
    # Stale only when both commits are known and differ; a report without a
    # commit cannot be called out of date.
    status = "stale" if latest_sha and head_sha and latest_sha != head_sha else "measured"
    return {"coverage": CoverageState(status, latest_at, int(count), partial)}


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
            unavailable[store] = "Not in this index yet; run `repowise update`."

    await read("files", lambda: _files(session, repo_id, since))
    files: dict[str, FileFacts] = values.get("files") or {}
    await read("authors", lambda: _authors(session, repo_id, since))
    await read("commit_health", lambda: _recent(session, repo_id, week, files))
    await read("performance", lambda: _perf(session, repo_id))
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
