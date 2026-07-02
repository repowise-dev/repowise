"""/api/repos/{repo_id}/git-* — Git intelligence endpoints."""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.change_risk import (
    ChangeFeatures,
    RiskNormalizer,
    baseline_scores,
    extract_range_features,
    score_change,
)
from repowise.core.ingestion.git_indexer._constants import (
    EVOLUTION_CATEGORIES,
    classify_commit_category,
)
from repowise.core.persistence import crud
from repowise.core.persistence.models import GitCommit, GitMetadata, Repository
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.mcp_server.tool_risk import _check_test_gap
from repowise.server.schemas import (
    AgentTrendBucket,
    AgentTrendResponse,
    ChangeFeaturesResponse,
    CommitDetailResponse,
    CommitEvolutionBucket,
    CommitEvolutionResponse,
    CommitResponse,
    CommitStatsResponse,
    GitMetadataResponse,
    GitSummaryResponse,
    HotspotResponse,
    OwnershipEntry,
    Paginated,
    ReviewerSuggestionsResponse,
    RiskDriverResponse,
    RiskRangeResponse,
)
from repowise.server.services.reviewer_suggestions import suggest_reviewers

# Below this many sampled commits a percentile isn't worth showing; mirrors
# the CLI's ``repowise risk`` threshold so the two surfaces agree.
_MIN_BASELINE = 8

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
    # churn_percentile is stored on a 0–1 scale (rank / total) but every UI
    # consumer treats it as a percentile rank in 0–100. Normalize here so
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
        original_path=r.original_path,
    )


def _commit_risk(r: GitCommit):
    """Re-score the persisted Kamei features; deterministic, reproduces the
    stored ``change_risk_score`` exactly. Returns None for unscored rows."""
    if r.change_risk_score is None:
        return None
    feats = ChangeFeatures(
        la=r.lines_added or 0,
        ld=r.lines_deleted or 0,
        nf=r.files_changed or 0,
        nd=r.dirs_changed or 0,
        ns=r.subsystems_changed or 0,
        entropy=r.entropy or 0.0,
        exp=r.author_experience,
        is_fix=bool(r.is_fix),
        author=r.author_name or "",
        subject=r.subject or "",
        ref=r.sha,
    )
    return score_change(feats)


def _commit_fields(r: GitCommit, normalizer: RiskNormalizer) -> dict:
    """Shared CommitResponse field map (raw row + repo-relative normalization)."""
    risk = _commit_risk(r)
    top_driver = risk.top_drivers[0].label if risk and risk.top_drivers else None
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
        "agent_name": r.agent_name,
        "agent_autonomy_tier": r.agent_autonomy_tier,
        "agent_confidence": r.agent_confidence,
    }


def _commit_from_row(r: GitCommit, normalizer: RiskNormalizer) -> CommitResponse:
    return CommitResponse(**_commit_fields(r, normalizer))


def _commit_detail_from_row(r: GitCommit, normalizer: RiskNormalizer) -> CommitDetailResponse:
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
            value=None if d.value != d.value else d.value,  # drop NaN (unknown feature)
            contribution=d.contribution,
            label=d.label,
        )
        for d in (risk.top_drivers if risk else [])
    ]
    return CommitDetailResponse(
        **_commit_fields(r, normalizer),
        drivers=drivers,
        agent_channel=r.agent_channel,
    )


@router.get("/{repo_id}/commits", response_model=Paginated[CommitResponse])
async def get_commits(
    repo_id: str,
    sort: str = Query("risk", pattern="^(risk|date)$"),
    authorship: str = Query("all", pattern="^(all|agent|human)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[CommitResponse]:
    """Per-commit change-risk feed — the review-priority queue.

    ``sort=risk`` (default) orders by raw change-risk score descending (the
    review-priority order); ``sort=date`` orders by recency. ``authorship``
    narrows the feed to agent-attributed or human commits. Each commit also
    carries a **repo-relative** ``risk_percentile`` + ``review_priority`` so the
    ranking is portable across repos (the absolute calibration band is not).
    """
    total = await crud.count_git_commits(session, repo_id, authorship=authorship)
    rows = await crud.get_git_commits(
        session, repo_id, limit=limit, offset=offset, sort=sort, authorship=authorship
    )
    normalizer = RiskNormalizer.from_scores(await crud.get_commit_risk_scores(session, repo_id))
    items = [_commit_from_row(r, normalizer) for r in rows]
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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AgentTrendResponse:
    """Monthly agent-vs-human commit volume across the indexed window.

    Buckets the bounded ``git_commits`` table in Python (portable across
    SQLite/Postgres date functions). Months with zero commits are omitted.
    """
    result = await session.execute(
        select(
            GitCommit.committed_at,
            GitCommit.agent_name,
            GitCommit.agent_autonomy_tier,
        ).where(GitCommit.repository_id == repo_id)
    )
    buckets: dict[str, dict] = {}
    total = 0
    agent_total = 0
    names: dict[str, int] = {}
    for committed_at, agent_name, tier in result.all():
        if committed_at is None:
            continue
        month = committed_at.strftime("%Y-%m")
        b = buckets.setdefault(month, {"total": 0, "agent": 0, "tiers": {}})
        b["total"] += 1
        total += 1
        if agent_name:
            b["agent"] += 1
            agent_total += 1
            names[agent_name] = names.get(agent_name, 0) + 1
            if tier is not None:
                key = str(tier)
                b["tiers"][key] = b["tiers"].get(key, 0) + 1
    return AgentTrendResponse(
        buckets=[
            AgentTrendBucket(
                month=m,
                total_commits=b["total"],
                agent_commits=b["agent"],
                agent_pct=(b["agent"] / b["total"] * 100.0) if b["total"] else 0.0,
                tier_counts=b["tiers"],
            )
            for m, b in sorted(buckets.items())
        ],
        total_commits=total,
        agent_commits=agent_total,
        agent_pct=(agent_total / total * 100.0) if total else 0.0,
        agent_names=sorted(
            [{"name": k, "count": v} for k, v in names.items()],
            key=lambda x: x["count"],
            reverse=True,
        ),
    )


@router.get("/{repo_id}/commits/stats", response_model=CommitStatsResponse)
async def get_commit_stats(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
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
            ).where(GitCommit.repository_id == repo_id)
        )
    ).one()
    fix_count, avg_entropy, agent_count = agg

    scores = await crud.get_commit_risk_scores(session, repo_id)
    normalizer = RiskNormalizer.from_scores(scores)
    high_priority = sum(1 for s in scores if normalizer.priority(s) == "high")

    return CommitStatsResponse(
        total_commits=total,
        high_priority_count=high_priority,
        fix_commit_count=int(fix_count or 0),
        agent_commit_count=int(agent_count or 0),
        avg_entropy=float(avg_entropy or 0.0),
    )


@router.get("/{repo_id}/commits/evolution", response_model=CommitEvolutionResponse)
async def get_commit_evolution(
    repo_id: str,
    granularity: str = Query("auto", pattern="^(auto|month|week)$"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CommitDetailResponse:
    """A single commit (by full sha or unique prefix) with its risk breakdown."""
    row = await crud.get_git_commit(session, repo_id, sha)
    if row is None:
        raise HTTPException(status_code=404, detail="Commit not found")
    normalizer = RiskNormalizer.from_scores(await crud.get_commit_risk_scores(session, repo_id))
    return _commit_detail_from_row(row, normalizer)


@router.get("/{repo_id}/git-metadata", response_model=GitMetadataResponse)
async def get_git_metadata(
    repo_id: str,
    file_path: str = Query(..., description="Relative file path"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GitMetadataResponse:
    """Get git metadata for a specific file."""
    meta = await crud.get_git_metadata(session, repo_id, file_path)
    if meta is None:
        raise HTTPException(status_code=404, detail="Git metadata not found")
    response = GitMetadataResponse.from_orm(meta)
    response.test_gap = await _check_test_gap(session, repo_id, file_path)
    return response


@router.get("/{repo_id}/hotspots", response_model=Paginated[HotspotResponse])
async def get_hotspots(
    repo_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[HotspotResponse]:
    """Return the highest-churn files (hotspots), paginated.

    The previous response shape was a bare list capped at 100 rows — large
    repos with thousands of hotspots silently lost the long tail. Callers
    should treat the envelope's ``total`` as authoritative and request
    further pages via ``offset``.
    """

    base = select(GitMetadata).where(
        GitMetadata.repository_id == repo_id,
        GitMetadata.is_hotspot.is_(True),
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

    paged = (
        base.order_by(
            GitMetadata.temporal_hotspot_score.desc().nulls_last(),
            GitMetadata.churn_percentile.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(paged)).scalars().all()
    items = [_hotspot_from_row(r) for r in rows]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[HotspotResponse](
        items=items,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/{repo_id}/ownership", response_model=Paginated[OwnershipEntry])
async def get_ownership(
    repo_id: str,
    granularity: str = Query("module", description="file or module"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[OwnershipEntry]:
    """Ownership breakdown for a repository, paginated.

    ``granularity=module`` groups by top-level directory; ``file`` returns
    one entry per tracked file.
    """

    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
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
            parts = m.file_path.split("/")
            module = parts[0] if len(parts) > 1 else "root"
            modules.setdefault(module, []).append(m)

        entries = []
        for module_path, files in sorted(modules.items()):
            owners: dict[str, int] = {}
            for f in files:
                if f.primary_owner_name:
                    owners[f.primary_owner_name] = owners.get(f.primary_owner_name, 0) + 1
            if owners:
                top_owner = max(owners, key=owners.get)  # type: ignore[arg-type]
                owner_pct = owners[top_owner] / len(files)
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


@router.get("/{repo_id}/co-changes")
async def get_co_changes(
    repo_id: str,
    file_path: str = Query(..., description="Relative file path"),
    min_count: int = Query(3, ge=1),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Get files that frequently change together with the given file."""
    meta = await crud.get_git_metadata(session, repo_id, file_path)
    if meta is None:
        raise HTTPException(status_code=404, detail="Git metadata not found")

    partners = json.loads(meta.co_change_partners_json)
    filtered = [p for p in partners if p.get("co_change_count", 0) >= min_count]

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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ReviewerSuggestionsResponse:
    """Suggest reviewers for a PR touching the given file paths.

    Composes signals already produced by the indexer (top authors,
    co-change partners, 90-day commit counts) — no live git inspection.
    """

    suggestions = await suggest_reviewers(session, repo_id, paths, limit=limit)
    return ReviewerSuggestionsResponse(paths=paths, suggestions=suggestions)


async def _resolve_local_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Repository:
    """Resolve a repository with a usable local checkout, or raise 404."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None or not repo.local_path or not os.path.isdir(repo.local_path):
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def _revision_exists(repo_path: str, rev: str) -> bool:
    # Reject option-shaped input outright; git refuses ref names starting
    # with "-", so this loses no legitimate revision and keeps user input
    # from ever being parsed as a git flag here or downstream.
    if not rev or rev.startswith("-"):
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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
    repo: Repository = Depends(_resolve_local_repo),  # noqa: B008
) -> RiskRangeResponse:
    """Score a ``base..head`` git range's defect risk from its live diff shape.

    Mirrors ``repowise risk <base>..<head> --format json``: same Kamei
    change-risk model, scored on demand against the working tree instead of
    the indexed commit table, so it also covers ranges that haven't been
    indexed yet (an open PR branch). Runs sync so FastAPI dispatches it to
    the threadpool, since it shells out to git.
    """
    local_path = repo.local_path
    if not _revision_exists(local_path, base) or not _revision_exists(local_path, head):
        raise HTTPException(status_code=400, detail=f"Unknown revision in range {base!r}..{head!r}")

    try:
        features = extract_range_features(local_path, base, head)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not read range {base!r}..{head!r}: {exc}"
        ) from exc

    risk = score_change(features)

    percentile: float | None = None
    priority: str | None = None
    if baseline:
        scores = baseline_scores(local_path, head, baseline, (), "")
        if len(scores) >= _MIN_BASELINE:
            normalizer = RiskNormalizer.from_scores(scores)
            # Rank with experience unknown, matching the baseline (diff-shape
            # percentile within the repo), keeping the comparison like-with-like.
            rank_score = score_change(replace(features, exp=None)).score
            percentile = normalizer.percentile(rank_score)
            priority = normalizer.priority(rank_score)

    return RiskRangeResponse(
        base=base,
        head=head,
        score=risk.score,
        probability=risk.probability,
        level=risk.level,
        risk_percentile=percentile,
        review_priority=priority,
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
                value=None if d.value != d.value else d.value,  # drop NaN (unknown feature)
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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GitSummaryResponse:
    """Aggregate git health signals for a repository.

    ``top_owners_limit`` controls how many contributors are returned in
    descending order of file count. Defaults to 25 (vs. the old hardcoded
    10) so an engineering leader can see the broader contributor surface.
    """

    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    all_meta = list(result.scalars().all())

    hotspot_count = sum(1 for m in all_meta if m.is_hotspot)
    stable_count = sum(1 for m in all_meta if m.is_stable)
    # Normalize to 0–100 to match the rest of the HTTP API contract.
    avg_churn = (
        sum(m.churn_percentile for m in all_meta) / len(all_meta) * 100.0 if all_meta else 0.0
    )

    owners: dict[str, int] = {}
    for m in all_meta:
        if m.primary_owner_name:
            owners[m.primary_owner_name] = owners.get(m.primary_owner_name, 0) + 1
    total = len(all_meta) or 1
    top_owners = sorted(
        [{"name": k, "file_count": v, "pct": v / total} for k, v in owners.items()],
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
