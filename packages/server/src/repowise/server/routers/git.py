"""/api/repos/{repo_id}/git-* — Git intelligence endpoints."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.change_risk import (
    SCORE_MEASURES,
    SCORE_UNIT,
    FixHistoryUnavailableError,
    RiskNormalizer,
    assess_change,
    baseline_samples,
    change_features_from_stored,
    densities_excluding,
    extract_range_features,
    fix_pressure,
    range_anchor,
    review_priority_classification,
    score_change,
    scores_excluding,
)
from repowise.core.analysis.history import DeltaStamp, delta_currency
from repowise.core.analysis.owners import people_resolver
from repowise.core.analysis.risk_semantics import change_risk_authority
from repowise.core.attribution import (
    AGENT,
    COVERAGE_FLOOR,
    HUMAN_ATTESTED,
    attribution_share,
    commit_label,
)
from repowise.core.co_change import MIN_CO_CHANGE_SUPPORT, parse_partners
from repowise.core.git_refs import branch_change_range
from repowise.core.ingestion.git_indexer._constants import (
    EVOLUTION_CATEGORIES,
    classify_commit_category,
)
from repowise.core.ingestion.git_indexer.identity import author_identity_key
from repowise.core.persistence import crud
from repowise.core.persistence.models import GitCommit, GitMetadata, Repository
from repowise.core.persistence.sql import is_missing_table
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.mcp_server.tool_risk import _check_test_gap
from repowise.server.routers._history_filters import HistoryParams, history_params
from repowise.server.routers._local_git import (
    repo_attribution_on,
    resolve_local_repo,
    revision_exists,
)
from repowise.server.routers.held_origins import HOLD_QUERY, INCLUDE_QUERY
from repowise.server.schemas import (
    AgentTrendBucket,
    AgentTrendResponse,
    ChangeFeaturesResponse,
    CoChangeResponse,
    CommitDetailResponse,
    CommitEvolutionBucket,
    CommitEvolutionResponse,
    CommitFileResponse,
    CommitHealthFindingResponse,
    CommitHealthResponse,
    CommitResponse,
    CommitStatsResponse,
    FixHistoryFileResponse,
    FixHistoryResponse,
    GitMetadataResponse,
    GitSummaryResponse,
    HotspotResponse,
    OwnershipEntry,
    Paginated,
    ReviewerSuggestionsResponse,
    RiskDriverResponse,
    RiskHistogramBucket,
    RiskRangeResponse,
)
from repowise.server.schemas.git import HotspotFacets, HotspotPage
from repowise.server.schemas.history import PeriodHealthSummaryResponse
from repowise.server.services.areas import held_origins
from repowise.server.services.history.commits import (
    CommitHistory,
    commit_history,
    commit_period_summary,
)
from repowise.server.services.history.health import (
    current_stamp,
    finding_fields,
    period_response,
)
from repowise.server.services.hotspots import FILTERS as HOTSPOT_FILTERS
from repowise.server.services.hotspots import SORTS as HOTSPOT_SORTS
from repowise.server.services.hotspots import HotspotQuery, is_accelerating, list_hotspots
from repowise.server.services.module_health import top_level_module
from repowise.server.services.reviewer_suggestions import suggest_reviewers

router = APIRouter(
    prefix="/api/repos",
    tags=["git"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Row mappers — kept out of the route bodies so they're trivial to test and
# can be reused by future endpoints (owner profile, module health, etc.).
# ---------------------------------------------------------------------------


def _hotspot_from_row(r: GitMetadata) -> HotspotResponse:
    # churn_percentile is stored on a 0-1 scale (rank / total) but every UI
    # consumer treats it as a percentile rank in 0-100. Normalize here so
    # the API contract is unambiguous and the dashboard ChurnBar / scatter
    # / hotspots-mini render correctly without per-component hacks.
    churn_pct = (r.churn_percentile or 0.0) * 100.0
    return HotspotResponse(
        file_path=r.file_path,
        commit_count_total=r.commit_count_total or 0,
        commit_count_90d=r.commit_count_90d,
        commit_count_30d=r.commit_count_30d,
        churn_percentile=churn_pct,
        temporal_hotspot_score=r.temporal_hotspot_score,
        primary_owner=r.primary_owner_name,
        primary_owner_commit_pct=r.primary_owner_commit_pct,
        recent_owner_name=r.recent_owner_name,
        recent_owner_commit_pct=r.recent_owner_commit_pct,
        is_hotspot=r.is_hotspot,
        is_stable=r.is_stable,
        bus_factor=r.bus_factor or 0,
        contributor_count=r.contributor_count or 0,
        lines_added_90d=r.lines_added_90d or 0,
        lines_deleted_90d=r.lines_deleted_90d or 0,
        avg_commit_size=r.avg_commit_size or 0.0,
        commit_categories=json.loads(r.commit_categories_json) if r.commit_categories_json else {},
        merge_commit_count_90d=r.merge_commit_count_90d or 0,
        commit_count_capped=bool(r.commit_count_capped),
        age_days=r.age_days or 0,
        last_commit_at=r.last_commit_at,
        change_entropy=r.change_entropy or 0.0,
        # Normalize 0-1 -> 0-100 to match churn_percentile.
        change_entropy_pct=(r.change_entropy_pct or 0.0) * 100.0,
        prior_defect_count=r.prior_defect_count or 0,
        bug_magnet=bool(r.bug_magnet),
        last_fix_at=r.last_fix_at,
        original_path=r.original_path,
        accelerating=is_accelerating(r.commit_count_30d, r.commit_count_90d, r.age_days),
    )


def _commit_risk(r: GitCommit):
    """Re-score the persisted Kamei features; deterministic, reproduces the
    stored ``change_risk_score`` exactly. Returns None for unscored rows."""
    if r.change_risk_score is None:
        return None
    feats = change_features_from_stored(
        la=r.lines_added,
        ld=r.lines_deleted,
        nf=r.files_changed,
        nd=r.dirs_changed,
        ns=r.subsystems_changed,
        entropy=r.entropy,
        exp=r.author_experience,
        is_fix=r.is_fix,
        author=r.author_name,
        subject=r.subject,
        ref=r.sha,
    )
    return score_change(feats)


async def _author_commit_counts(session: AsyncSession, repo_id: str) -> dict[str, int]:
    """Commits per author in the index, keyed by folded identity.

    One grouped query per request rather than a per-row lookup, and folded in
    Python so it matches the tally that produced ``author_experience`` exactly.
    """
    counts: dict[str, int] = {}
    for name, email, count in await crud.get_author_commit_counts(session, repo_id):
        key = author_identity_key(name, email)
        counts[key] = counts.get(key, 0) + int(count or 0)
    return counts


def _attribution_fields(r: GitCommit) -> dict:
    return {
        "agent_name": r.agent_name,
        "agent_autonomy_tier": r.agent_autonomy_tier,
        "agent_confidence": r.agent_confidence,
        "attribution": commit_label(r.attribution, r.agent_name),
    }


def _commit_fields(
    r: GitCommit,
    normalizer: RiskNormalizer,
    author_counts: dict[str, int] | None = None,
    *,
    attribution_on: bool,
    history: CommitHistory | None = None,
) -> dict:
    """Shared CommitResponse field map (raw row + repo-relative normalization).

    Attribution fields stay None when attribution is off for the repository;
    the health delta and hotspot touches come from *history* when given.
    """
    risk = _commit_risk(r)
    top_driver = risk.top_driver.label if risk and risk.top_driver else None
    return {
        "sha": r.sha,
        "short_sha": r.sha[:8],
        "author_name": r.author_name or "",
        "author_email": r.author_email or "",
        "committed_at": r.committed_at,
        "subject": r.subject or "",
        "lines_added": r.lines_added or 0,
        "lines_deleted": r.lines_deleted or 0,
        "files_changed": r.files_changed or 0,
        "dirs_changed": r.dirs_changed or 0,
        "subsystems_changed": r.subsystems_changed or 0,
        "entropy": r.entropy or 0.0,
        "is_fix": bool(r.is_fix),
        "change_risk_score": r.change_risk_score,
        "change_risk_level": r.change_risk_level,
        "risk_percentile": normalizer.percentile(r.change_risk_score),
        "review_priority": normalizer.priority(r.change_risk_score),
        "top_driver": top_driver,
        "author_experience": r.author_experience,
        "author_commit_count": (
            author_counts.get(author_identity_key(r.author_name, r.author_email))
            if author_counts is not None
            else None
        ),
        **(_attribution_fields(r) if attribution_on else {}),
        **(history.fields(r.sha) if history is not None else {}),
    }


def _commit_from_row(
    r: GitCommit,
    normalizer: RiskNormalizer,
    author_counts: dict[str, int] | None = None,
    *,
    attribution_on: bool,
    history: CommitHistory | None = None,
) -> CommitResponse:
    fields = _commit_fields(
        r, normalizer, author_counts, attribution_on=attribution_on, history=history
    )
    return CommitResponse(**fields)


async def _commit_files(session: AsyncSession, repo_id: str, sha: str) -> list[CommitFileResponse]:
    """The files a commit touched. Stored, so it answers without a checkout."""
    try:
        rows = await crud.get_commit_files(session, repo_id, sha)
    except (OperationalError, ProgrammingError) as exc:
        # An index older than the table: serve the commit without its files
        # rather than failing the whole detail view.
        if not is_missing_table(exc):
            raise
        return []
    if not rows:
        return []
    meta = await crud.get_git_metadata_bulk(session, repo_id, [r.file_path for r in rows])
    return [
        CommitFileResponse(
            path=r.file_path,
            lines_added=r.lines_added,
            lines_deleted=r.lines_deleted,
            prior_fixes=getattr(meta.get(r.file_path), "prior_defect_count", None),
        )
        for r in rows
    ]


async def _commit_health(
    session: AsyncSession, repo_id: str, sha: str, stamp: DeltaStamp
) -> CommitHealthResponse | None:
    """What the commit did to health. None when it was never scanned, or only
    by an analyzer too old to read; ``stale`` when an older one wrote it."""
    try:
        delta = await crud.get_commit_health(session, repo_id, sha)
        rows = await crud.get_commit_health_findings(session, repo_id, sha) if delta else []
    except (OperationalError, ProgrammingError) as exc:
        # An index older than the tables: serve the commit without its health
        # block rather than failing the whole detail view.
        if not is_missing_table(exc):
            raise
        return None
    currency = delta_currency(delta, stamp) if delta is not None else "absent"
    if currency == "absent":
        return None
    return CommitHealthResponse(
        status=delta.status,
        stale=currency == "stale",
        introduced_count=delta.introduced_count,
        worsened_count=delta.worsened_count,
        resolved_count=delta.resolved_count,
        improved_count=delta.improved_count,
        moved_count=delta.moved_count,
        files_analyzed=delta.files_analyzed,
        files_skipped=delta.files_skipped,
        findings=[CommitHealthFindingResponse(**finding_fields(f)) for f in rows],
    )


def _commit_detail_from_row(
    r: GitCommit,
    normalizer: RiskNormalizer,
    author_counts: dict[str, int] | None = None,
    files: list[CommitFileResponse] | None = None,
    health: CommitHealthResponse | None = None,
    *,
    attribution_on: bool,
    history: CommitHistory | None = None,
) -> CommitDetailResponse:
    """Map a commit row to its detail view, recomputing the risk-driver
    breakdown from the persisted Kamei features + author experience.

    The model is deterministic and ships its constants, so re-scoring the
    stored features reproduces the persisted ``change_risk_score`` exactly —
    no need to persist the per-driver breakdown.
    """
    risk = _commit_risk(r)
    drivers = [
        RiskDriverResponse(
            feature=d.feature,
            value=d.value,  # already None for an unknown feature
            contribution=d.contribution,
            label=d.label,
        )
        for d in (risk.top_drivers if risk else [])
    ]
    return CommitDetailResponse(
        **_commit_fields(
            r, normalizer, author_counts, attribution_on=attribution_on, history=history
        ),
        drivers=drivers,
        agent_channel=r.agent_channel if attribution_on else None,
        files=files or [],
        health=health,
    )


@router.get("/{repo_id}/commits", response_model=Paginated[CommitResponse])
async def get_commits(
    repo_id: str,
    sort: str = Query("date", pattern="^(risk|date)$"),
    authorship: str = Query("all", pattern="^(all|agent|human|unknown)$"),
    kind: str = Query("all", pattern="^(all|high|fixes)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    params: HistoryParams = Depends(history_params),
    session: AsyncSession = Depends(get_db_session),
) -> Paginated[CommitResponse]:
    """Per-commit change-risk feed — the review-priority queue.

    ``sort=date`` (default) orders by recency; ``sort=risk`` orders by the
    supporting diff-shape score descending. ``authorship`` narrows to
    agent-attributed, human-attested or unknown commits (ignored when
    attribution is off), and ``kind`` to the ``high``
    review-priority band or to ``fixes``. Each commit carries a
    **repo-relative** ``risk_percentile`` + ``review_priority`` so the ranking
    is portable across repos (the absolute calibration band is not), and leads
    with ``health_delta`` (what it made better or worse) and ``touches_hotspot``.
    ``author``, ``area``, ``since``/``until``, ``delta`` and ``touches_hotspot``
    narrow the repository, like the other filters.
    """
    attribution_on = await repo_attribution_on(session, repo_id)
    if not attribution_on:
        authorship = "all"
    normalizer = RiskNormalizer.from_scores(await crud.get_commit_risk_scores(session, repo_id))
    # The high band is a tercile of the repo's own scores, so the boundary has
    # to be resolved before the page is cut rather than derived per row.
    high_cut = normalizer.high_cut
    stamp = await current_stamp(session, repo_id)
    where = params.commit_filter(authorship=authorship, kind=kind, high_cut=high_cut, stamp=stamp)
    total = await crud.count_git_commits(session, repo_id, where=where)
    rows = await crud.get_git_commits(
        session,
        repo_id,
        limit=limit,
        offset=offset,
        sort=sort,
        where=where,
    )
    author_counts = await _author_commit_counts(session, repo_id)
    history = await commit_history(session, repo_id, [r.sha for r in rows], stamp)
    items = [
        _commit_from_row(
            r, normalizer, author_counts, attribution_on=attribution_on, history=history
        )
        for r in rows
    ]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[CommitResponse](
        items=items,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/{repo_id}/commits/agent-trend", response_model=AgentTrendResponse)
async def get_agent_trend(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> AgentTrendResponse:
    """Monthly commits by attribution label across the indexed window.

    Each month and the window carry agent, human-attested and unknown counts,
    the coverage (known / all) and the agent share of covered commits, withheld
    below the coverage floor. Buckets the bounded ``git_commits`` table in
    Python (portable across SQLite/Postgres date functions). Months with zero
    commits are omitted.
    """
    floor_pct = COVERAGE_FLOOR * 100.0
    if not await repo_attribution_on(session, repo_id):
        return AgentTrendResponse(enabled=False, coverage_floor_pct=floor_pct)
    result = await session.execute(
        select(
            GitCommit.committed_at,
            GitCommit.agent_name,
            GitCommit.agent_autonomy_tier,
            GitCommit.attribution,
        ).where(GitCommit.repository_id == repo_id)
    )
    buckets: dict[str, Counter[str]] = {}
    tiers: dict[str, Counter[str]] = {}
    window: Counter[str] = Counter()
    names: dict[str, int] = {}
    for committed_at, agent_name, tier, attribution in result.all():
        if committed_at is None:
            continue
        month = committed_at.strftime("%Y-%m")
        label = commit_label(attribution, agent_name)
        for counts in (buckets.setdefault(month, Counter()), window):
            counts["total"] += 1
            counts[label] += 1
        if label == AGENT and agent_name:
            names[agent_name] = names.get(agent_name, 0) + 1
            if tier is not None:
                tiers.setdefault(month, Counter())[str(tier)] += 1
    return AgentTrendResponse(
        coverage_floor_pct=floor_pct,
        buckets=[
            AgentTrendBucket(month=m, tier_counts=dict(tiers.get(m, {})), **_share_fields(c))
            for m, c in sorted(buckets.items())
        ],
        **_share_fields(window),
        agent_names=sorted(
            [{"name": k, "count": v} for k, v in names.items()],
            key=lambda x: x["count"],
            reverse=True,
        ),
    )


def _share_fields(counts: Counter[str]) -> dict:
    """``AttributionShareFields`` from per-label commit counts, in percent."""
    share = attribution_share(counts[AGENT], counts[HUMAN_ATTESTED], counts["total"])
    return {
        "total_commits": share.total,
        "agent_commits": share.agent,
        "human_attested_commits": share.human_attested,
        "unknown_commits": share.unknown,
        "coverage_pct": share.coverage * 100.0 if share.coverage is not None else None,
        "agent_pct_of_covered": (
            share.agent_share * 100.0 if share.agent_share is not None else None
        ),
        "suppressed_reason": share.suppressed_reason,
    }


@router.get("/{repo_id}/commits/stats", response_model=CommitStatsResponse)
async def get_commit_stats(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> CommitStatsResponse:
    """Repo-wide commit aggregates for the headline stat cards.

    Computed over **every** indexed commit, not the loaded page — the feed is
    paginated, so reducing the client's window mis-counts (a risk-sorted first
    page is entirely top-tercile, and its fix/entropy figures are a slice, not
    the whole). High-priority uses the same repo-relative tercile as the feed.
    """
    total = await crud.count_git_commits(session, repo_id)

    # is_fix / entropy / agent counts in a single pass. CASE-SUM rather than
    # COUNT(...) FILTER keeps it portable across SQLite and Postgres.
    agg = (
        await session.execute(
            select(
                func.sum(case((GitCommit.is_fix.is_(True), 1), else_=0)),
                func.avg(GitCommit.entropy),
                func.sum(case((GitCommit.agent_name.is_not(None), 1), else_=0)),
                func.sum(case((GitCommit.attribution == HUMAN_ATTESTED, 1), else_=0)),
            ).where(GitCommit.repository_id == repo_id)
        )
    ).one()
    fix_count, avg_entropy, agent_count, human_count = agg
    attribution: dict = {}
    if await repo_attribution_on(session, repo_id):
        counts = Counter({AGENT: int(agent_count or 0), HUMAN_ATTESTED: int(human_count or 0)})
        counts["total"] = total
        fields = _share_fields(counts)
        attribution = {
            "agent_commit_count": fields["agent_commits"],
            "agent_pct_of_covered": fields["agent_pct_of_covered"],
            "attribution_coverage_pct": fields["coverage_pct"],
        }

    scores = await crud.get_commit_risk_scores(session, repo_id)
    normalizer = RiskNormalizer.from_scores(scores)
    high_priority = sum(1 for s in scores if normalizer.priority(s) == "high")

    return CommitStatsResponse(
        total_commits=total,
        high_priority_count=high_priority,
        fix_commit_count=int(fix_count or 0),
        **attribution,
        avg_entropy=float(avg_entropy or 0.0),
        risk_histogram=_risk_histogram(normalizer.scores),
        moderate_cut=normalizer.moderate_cut,
        high_cut=normalizer.high_cut,
    )


@router.get("/{repo_id}/commits/health-summary", response_model=PeriodHealthSummaryResponse)
async def get_commit_health_summary(
    repo_id: str,
    params: HistoryParams = Depends(history_params),
    session: AsyncSession = Depends(get_db_session),
) -> PeriodHealthSummaryResponse:
    """How many commits under the filters made health better, worse, both or
    neither, and the findings behind it; improvements first."""
    where = params.commit_filter(stamp=await current_stamp(session, repo_id))
    summary = await commit_period_summary(session, repo_id, where)
    return period_response(summary, params.since, params.until)


_HISTOGRAM_BINS = 20  # 0.5-wide bins across the 0-10 supporting diff-shape score


def _risk_histogram(sorted_scores: list[float]) -> list[RiskHistogramBucket]:
    """Bin the repo's supporting diff-shape scores for the distribution chart.

    Reuses the score list already fetched for the normalizer, so this costs no
    extra query. The top bin is closed on the right so a perfect 10.0 lands
    somewhere instead of falling off the end.
    """
    if not sorted_scores:
        return []
    width = 10.0 / _HISTOGRAM_BINS
    counts = [0] * _HISTOGRAM_BINS
    for s in sorted_scores:
        idx = min(_HISTOGRAM_BINS - 1, max(0, int(s / width)))
        counts[idx] += 1
    return [
        RiskHistogramBucket(start=i * width, end=(i + 1) * width, count=c)
        for i, c in enumerate(counts)
    ]


@router.get("/{repo_id}/commits/evolution", response_model=CommitEvolutionResponse)
async def get_commit_evolution(
    repo_id: str,
    granularity: str = Query("auto", pattern="^(auto|month|week)$"),
    session: AsyncSession = Depends(get_db_session),
) -> CommitEvolutionResponse:
    """The repo's development "story arc" — commit-category mix over time.

    Each commit is classified into exactly one category (feature / fix /
    refactor / docs / test / deps / chore / other) from its subject and bucketed
    by month (or week on short-history repos). Classification is pure and runs
    off the already-stored subject, so this needs no reindex. The UI stacks the
    buckets to show how the development emphasis shifts as the repo matures.
    """
    result = await session.execute(
        select(GitCommit.committed_at, GitCommit.subject).where(GitCommit.repository_id == repo_id)
    )
    rows = [(ts, subj) for ts, subj in result.all() if ts is not None]

    if not rows:
        return CommitEvolutionResponse(
            buckets=[],
            categories=[],
            totals={},
            total_commits=0,
            granularity="month",
        )

    rows.sort(key=lambda r: r[0])
    first, last = rows[0][0], rows[-1][0]

    # Auto: weekly resolution keeps short-lived repos from collapsing into one
    # or two fat monthly columns; anything spanning more than ~26 weeks reads
    # better monthly.
    if granularity == "auto":
        span_days = (last - first).days
        granularity = "week" if span_days <= 26 * 7 else "month"

    def _bucket_key(ts: datetime) -> tuple[str, str]:
        if granularity == "week":
            iso_year, iso_week, _ = ts.isocalendar()
            monday = (ts - timedelta(days=ts.isoweekday() - 1)).date()
            return f"{iso_year}-W{iso_week:02d}", monday.isoformat()
        return ts.strftime("%Y-%m"), ts.replace(day=1).date().isoformat()

    buckets: dict[str, dict] = {}
    totals: Counter[str] = Counter()
    for ts, subject in rows:
        period, start = _bucket_key(ts)
        cat = classify_commit_category(subject or "")
        b = buckets.setdefault(period, {"start": start, "total": 0, "counts": Counter()})
        b["total"] += 1
        b["counts"][cat] += 1
        totals[cat] += 1

    # Canonical category order, restricted to those that actually appear.
    present = [c for c in EVOLUTION_CATEGORIES if totals.get(c)]

    return CommitEvolutionResponse(
        buckets=[
            CommitEvolutionBucket(
                period=period,
                start=b["start"],
                total=b["total"],
                counts=dict(b["counts"]),
            )
            for period, b in sorted(buckets.items(), key=lambda kv: kv[1]["start"])
        ],
        categories=present,
        totals=dict(totals),
        total_commits=len(rows),
        granularity=granularity,
        first_commit_at=first.isoformat(),
        last_commit_at=last.isoformat(),
    )


@router.get("/{repo_id}/commits/{sha}", response_model=CommitDetailResponse)
async def get_commit(
    repo_id: str,
    sha: str,
    session: AsyncSession = Depends(get_db_session),
) -> CommitDetailResponse:
    """A single commit (by full sha or unique prefix) with its risk breakdown."""
    row = await crud.get_git_commit(session, repo_id, sha)
    if row is None:
        raise HTTPException(status_code=404, detail="Commit not found")
    normalizer = RiskNormalizer.from_scores(await crud.get_commit_risk_scores(session, repo_id))
    author_counts = await _author_commit_counts(session, repo_id)
    files = await _commit_files(session, repo_id, row.sha)
    stamp = await current_stamp(session, repo_id)
    health = await _commit_health(session, repo_id, row.sha, stamp)
    return _commit_detail_from_row(
        row,
        normalizer,
        author_counts,
        files,
        health,
        attribution_on=await repo_attribution_on(session, repo_id),
        history=await commit_history(session, repo_id, [row.sha], stamp),
    )


@router.get("/{repo_id}/git-metadata", response_model=GitMetadataResponse)
async def get_git_metadata(
    repo_id: str,
    file_path: str = Query(..., description="Relative file path"),
    session: AsyncSession = Depends(get_db_session),
) -> GitMetadataResponse:
    """Get git metadata for a specific file."""
    meta = await crud.get_git_metadata(session, repo_id, file_path)
    if meta is None:
        raise HTTPException(status_code=404, detail="Git metadata not found")
    response = GitMetadataResponse.from_orm(
        meta, attribution_on=await repo_attribution_on(session, repo_id)
    )
    response.test_gap = await _check_test_gap(session, repo_id, file_path)
    return response


def _hotspot_query(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200, description="Path or owner substring, case-insensitive."),
    filter: str = Query("all", pattern=f"^({'|'.join(HOTSPOT_FILTERS)})$"),
    sort: str = Query("score", pattern=f"^({'|'.join(HOTSPOT_SORTS)})$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    hold: str = HOLD_QUERY,
    include: str = INCLUDE_QUERY,
) -> HotspotQuery:
    return HotspotQuery(
        limit=limit,
        offset=offset,
        q=q,
        filter=filter,
        sort=sort,
        descending=order == "desc",
        held=held_origins(hold, include),
    )


@router.get("/{repo_id}/hotspots", response_model=HotspotPage)
async def get_hotspots(
    repo_id: str,
    query: HotspotQuery = Depends(_hotspot_query),
    session: AsyncSession = Depends(get_db_session),
) -> HotspotPage:
    """Hotspots ranked by temporal hotspot score, one page at a time.

    ``total`` counts the rows this filter and search match; ``facets`` count
    every filter over the searched set, so a chip can say "N of total". Each
    row's ``rank`` is its place by score in the scope, whatever the sort.
    """
    listing = await list_hotspots(session, repo_id, query)
    items = [_hotspot_from_row(r).model_copy(update={"rank": rank}) for r, rank in listing.rows]
    end = query.offset + query.limit
    next_offset = end if end < listing.total else None
    return HotspotPage(
        items=items,
        total=listing.total,
        has_more=next_offset is not None,
        next_offset=next_offset,
        facets=HotspotFacets(**listing.facets),
    )


@router.get("/{repo_id}/ownership", response_model=Paginated[OwnershipEntry])
async def get_ownership(
    repo_id: str,
    granularity: str = Query("module", description="file or module"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> Paginated[OwnershipEntry]:
    """Ownership breakdown for a repository, paginated.

    ``granularity=module`` groups by top-level directory; ``file`` returns
    one entry per tracked file.
    """

    result = await session.execute(select(GitMetadata).where(crud.code_file_rows(repo_id)))
    all_meta = result.scalars().all()

    if granularity == "file":
        entries = [
            OwnershipEntry(
                module_path=m.file_path,
                primary_owner=m.primary_owner_name,
                owner_pct=m.primary_owner_commit_pct,
                file_count=1,
                is_silo=(m.primary_owner_commit_pct or 0) > 0.8,
            )
            for m in all_meta
        ]
    else:
        modules: dict[str, list] = {}
        for m in all_meta:
            modules.setdefault(top_level_module(m.file_path), []).append(m)

        resolve = people_resolver(all_meta)
        entries = []
        for module_path, files in sorted(modules.items()):
            owners: Counter[str] = Counter(
                resolve(f.primary_owner_name, f.primary_owner_email)
                for f in files
                if f.primary_owner_name
            )
            if owners:
                top_key = max(owners, key=owners.__getitem__)
                top_owner = resolve.display_name(top_key)
                owner_pct = owners[top_key] / len(files)
            else:
                top_owner = None
                owner_pct = 0.0

            entries.append(
                OwnershipEntry(
                    module_path=module_path,
                    primary_owner=top_owner,
                    owner_pct=owner_pct,
                    file_count=len(files),
                    is_silo=owner_pct > 0.8,
                )
            )

    total = len(entries)
    page = entries[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[OwnershipEntry](
        items=page,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/{repo_id}/co-changes", response_model=CoChangeResponse)
async def get_co_changes(
    repo_id: str,
    file_path: str = Query(..., description="Relative file path"),
    min_count: int = Query(MIN_CO_CHANGE_SUPPORT, ge=1),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get files that frequently change together with the given file.

    ``min_count`` is a number of shared commits, not the decayed weight.
    """
    meta = await crud.get_git_metadata(session, repo_id, file_path)
    if meta is None:
        raise HTTPException(status_code=404, detail="Git metadata not found")

    filtered = [
        p.record for p in parse_partners(meta.co_change_partners_json) if p.support >= min_count
    ]

    return {
        "file_path": file_path,
        "co_change_partners": filtered,
        "total": len(filtered),
    }


@router.get(
    "/{repo_id}/reviewer-suggestions",
    response_model=ReviewerSuggestionsResponse,
)
async def get_reviewer_suggestions(
    repo_id: str,
    paths: list[str] = Query(..., description="Repeat ?paths= for each changed file"),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
) -> ReviewerSuggestionsResponse:
    """Suggest reviewers for a PR touching the given file paths.

    Composes signals already produced by the indexer (top authors,
    co-change partners, 90-day commit counts) — no live git inspection.
    """

    suggestions = await suggest_reviewers(session, repo_id, paths, limit=limit)
    return ReviewerSuggestionsResponse(paths=paths, suggestions=suggestions)


@router.get("/{repo_id}/risk/range", response_model=RiskRangeResponse)
def get_risk_range(
    repo_id: str,
    base: str = Query(..., description="Base revision of the range"),
    head: str = Query("HEAD", description="Head revision of the range"),
    baseline: int = Query(
        200,
        ge=0,
        description="Recent commits to sample for the repo-relative percentile (0 skips it)",
    ),
    repo: Repository = Depends(resolve_local_repo),
) -> RiskRangeResponse:
    """Assess a ``base..head`` range from its live diff shape and history.

    Mirrors ``repowise risk <base>..<head> --format json``: same Kamei
    change-risk model, scored on demand against the working tree instead of
    the indexed commit table, so it also covers ranges that haven't been
    indexed yet (an open PR branch). Runs sync so FastAPI dispatches it to
    the threadpool, since it shells out to git.
    """
    local_path = repo.local_path
    if not revision_exists(local_path, base) or not revision_exists(local_path, head):
        raise HTTPException(status_code=400, detail=f"Unknown revision in range {base!r}..{head!r}")

    # A base that moved past the fork point would diff the two tips; read the branch's change.
    _, range_note = branch_change_range(local_path, f"{base}..{head}")
    try:
        features = extract_range_features(
            local_path, base, head, sep="..." if range_note else ".."
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not read range {base!r}..{head!r}: {exc}"
        ) from exc

    # The fork point anchors both the baseline cohort and the fix record, so a
    # range is ranked against the history it forked from and is never credited
    # with fixes it brought in itself.
    anchor = range_anchor(local_path, base, head)
    samples = baseline_samples(local_path, anchor, baseline, ()) if baseline else []
    try:
        pressure: dict[str, float] | None = fix_pressure(local_path, anchor)
    except FixHistoryUnavailableError:
        pressure = None

    assessed = assess_change(
        features,
        fix_pressure=pressure,
        baseline_scores=scores_excluding(samples, ""),
        baseline_fix_densities=densities_excluding(samples, "", pressure or {}),
    )
    risk, percentile, priority = assessed.risk, assessed.percentile, assessed.priority

    return RiskRangeResponse(
        base=base,
        head=head,
        fix_history=FixHistoryResponse(
            available=assessed.fix_history_available,
            density=assessed.fix_density,
            percentile=assessed.fix_percentile,
            files=[
                FixHistoryFileResponse(path=path, churn=churn, fix_pressure=p)
                for path, churn, p in assessed.hot_files
            ],
        ),
        risk_authority=change_risk_authority(),
        score=risk.score,
        score_measures=SCORE_MEASURES,
        score_unit=SCORE_UNIT,
        risk_percentile=percentile,
        review_priority=priority,
        classification=review_priority_classification(priority),
        fallback_band=risk.level if priority is None else None,
        is_fix=features.is_fix,
        features=ChangeFeaturesResponse(
            la=features.la,
            ld=features.ld,
            nf=features.nf,
            nd=features.nd,
            ns=features.ns,
            entropy=features.entropy,
            exp=features.exp,
        ),
        drivers=[
            RiskDriverResponse(
                feature=d.feature,
                value=d.value,  # already None for an unknown feature
                contribution=d.contribution,
                label=d.label,
            )
            for d in risk.top_drivers
        ],
    )


@router.get("/{repo_id}/git-summary", response_model=GitSummaryResponse)
async def get_git_summary(
    repo_id: str,
    top_owners_limit: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> GitSummaryResponse:
    """Aggregate git health signals for a repository.

    ``top_owners_limit`` controls how many contributors are returned in
    descending order of file count. Defaults to 25 (vs. the old hardcoded
    10) so an engineering leader can see the broader contributor surface.
    """

    result = await session.execute(select(GitMetadata).where(crud.code_file_rows(repo_id)))
    all_meta = list(result.scalars().all())

    hotspot_count = sum(1 for m in all_meta if m.is_hotspot)
    stable_count = sum(1 for m in all_meta if m.is_stable)
    # Normalize to 0-100 to match the rest of the HTTP API contract.
    avg_churn = (
        sum(m.churn_percentile for m in all_meta) / len(all_meta) * 100.0 if all_meta else 0.0
    )

    # One person is one owner, however many names and emails they commit under.
    resolve = people_resolver(all_meta)
    owners = Counter(
        resolve(m.primary_owner_name, m.primary_owner_email)
        for m in all_meta
        if m.primary_owner_name
    )
    total = len(all_meta) or 1
    top_owners = sorted(
        [
            {"name": resolve.display_name(k), "file_count": v, "pct": v / total}
            for k, v in owners.items()
        ],
        key=lambda x: x["file_count"],
        reverse=True,
    )[:top_owners_limit]

    return GitSummaryResponse(
        total_files=len(all_meta),
        hotspot_count=hotspot_count,
        stable_count=stable_count,
        average_churn_percentile=avg_churn,
        top_owners=top_owners,
    )
