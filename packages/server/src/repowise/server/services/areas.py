"""Area read model: loads the rows the rollup folds, once per index state.

The fold itself lives in :mod:`repowise.core.analysis.areas`; this module reads
the stored analysis, keeps the folded index per (database, repository, index
stamp, area stamp), and shapes the tree and detail answers. The area stamp
covers what can move without an index run: findings triage and a snapshot
written after the scores.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repowise.core.analysis.areas import (
    AreaIndex,
    area_depth,
    area_detail,
    area_score,
    area_scores,
    attach_deltas,
    baseline_index,
    build_area_index,
    file_owners,
    in_area,
    nloc_weights,
)
from repowise.core.analysis.areas.trend import DELTA_WINDOW_DAYS
from repowise.core.persistence import crud
from repowise.core.persistence.crud import code_file_rows
from repowise.core.persistence.database import get_session
from repowise.core.persistence.index_cache import IndexStampCache
from repowise.core.persistence.models import (
    GitCommit,
    GitMetadata,
    HealthFinding,
    HealthSnapshot,
)

__all__ = [
    "MAX_TREE_DEPTH",
    "TREND_POINTS",
    "AreaReadModel",
    "area_detail_payload",
    "area_tree_payload",
    "clear_area_cache",
    "load_area_model",
    "warm_area_model",
]

logger = structlog.get_logger(__name__)

#: Levels one tree request may unfold below its root. Deeper is a second request.
MAX_TREE_DEPTH = 4

#: Snapshots an area trend reads, newest last; the repo trend's default window.
TREND_POINTS = 20

#: Folded indexes, one per repository (older states are dropped): a few MB
#: each on a 6k-file repository.
_models = IndexStampCache(4)
#: Area cards, the ones a reader opens.
_details = IndexStampCache(128)
#: One build per repository at a time; a concurrent caller waits and reads the
#: cache instead of folding the same rows again.
_locks: dict[str, asyncio.Lock] = {}


def clear_area_cache() -> None:
    _models.clear()
    _details.clear()
    # A lock is bound to the loop that first waited on it.
    _locks.clear()


@dataclass
class AreaReadModel:
    index: AreaIndex
    weights: dict[str, int]
    latest_at: datetime | None
    baseline_at: datetime | None


async def _area_stamp(session: AsyncSession, repo_id: str) -> tuple[Any, ...]:
    """What moves the model between index runs: a finding triaged, a snapshot
    recorded after the scores. One statement, beside the index stamp. Git rows
    are rewritten with the health rows (the index stamp sees those), so they
    are not scanned here: on this repository that scan was most of a warm read."""
    columns = []
    for model, moved in (
        (HealthFinding, HealthFinding.updated_at),
        (HealthSnapshot, HealthSnapshot.taken_at),
    ):
        mine = model.repository_id == repo_id
        columns += [
            select(func.max(moved)).where(mine),
            select(func.count()).select_from(model).where(mine),
        ]
    row = (await session.execute(select(*(c.scalar_subquery() for c in columns)))).one()
    return tuple(row)


def _score_map(raw: str | None) -> dict[str, float]:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _snapshot_map(session: AsyncSession, snap: Any) -> dict[str, float]:
    """One snapshot's ``{path: score}``, loaded now because the list deferred it."""
    await session.refresh(snap, ["per_file_scores_json"])
    return _score_map(snap.per_file_scores_json)


async def _git_rows(session: AsyncSession, repo_id: str) -> list[Any]:
    g = GitMetadata
    columns = (
        g.file_path,
        g.is_hotspot,
        g.bus_factor,
        g.primary_owner_name,
        g.primary_owner_email,
        g.top_authors_json,
    )
    return list((await session.execute(select(*columns).where(code_file_rows(repo_id)))).all())


async def _commit_rows(session: AsyncSession, repo_id: str) -> list[Any]:
    """Commit authors, so owner keys resolve as on the contributors pages."""
    q = select(GitCommit.author_name, GitCommit.author_email).where(
        GitCommit.repository_id == repo_id
    )
    return list((await session.execute(q)).all())


async def _build(session: AsyncSession, repo_id: str) -> AreaReadModel:
    metrics = await crud.get_health_metrics(session, repo_id)
    git_rows = await _git_rows(session, repo_id)
    index = build_area_index(
        metrics,
        git_rows=git_rows,
        owners=file_owners(git_rows, await _commit_rows(session, repo_id)),
        findings=await crud.get_health_findings(session, repo_id),
        deductions=await crud.get_deduction_by_path(session, repo_id),
    )
    weights = nloc_weights(metrics)
    history = await crud.list_health_snapshots(session, repo_id, file_maps=False)
    base = baseline_index([s.taken_at for s in history])
    if base is None:
        return AreaReadModel(index, weights, None, None)
    latest, baseline = history[-1], history[base]
    attach_deltas(
        index.nodes,
        area_scores(await _snapshot_map(session, latest), weights),
        area_scores(await _snapshot_map(session, baseline), weights),
    )
    return AreaReadModel(index, weights, latest.taken_at, baseline.taken_at)


async def load_area_model(session: AsyncSession, repo_id: str) -> AreaReadModel:
    """The folded area index for *repo_id*, built once per index and findings state."""
    key = await _models.key(session, repo_id, await _area_stamp(session, repo_id))
    if key in _models:
        return _models.get(key)
    async with _locks.setdefault(repo_id, asyncio.Lock()):
        if key in _models:
            return _models.get(key)
        model = await _build(session, repo_id)
        _models.drop_repo(repo_id)
        return _models.remember(key, model)


async def warm_area_model(
    session_factory: async_sessionmaker[AsyncSession], repo_id: str
) -> None:
    """Fold *repo_id*'s areas now, so the first reader after an index run does
    not pay for it. Best effort: a failure leaves the build to that reader.
    Ceiling: an index written outside this process (``repowise update`` from
    the CLI) is still folded on first read."""
    try:
        async with get_session(session_factory) as session:
            await load_area_model(session, repo_id)
    except Exception:
        logger.warning("areas_warm_failed", repo_id=repo_id, exc_info=True)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _delta_window(model: AreaReadModel) -> dict[str, Any]:
    return {
        "target_days": DELTA_WINDOW_DAYS,
        "latest_at": _iso(model.latest_at),
        "baseline_at": _iso(model.baseline_at),
    }


def area_tree_payload(model: AreaReadModel, root: str, depth: int) -> dict[str, Any] | None:
    """*root*'s rollup and every area up to *depth* levels below it, parents first."""
    nodes = model.index.nodes
    root_node = nodes.get(root)
    if root_node is None:
        return None
    floor = area_depth(root)
    below = [
        node
        for area, node in nodes.items()
        if area != root and in_area(area, root) and node["depth"] - floor <= depth
    ]
    below.sort(key=lambda node: node["path"])
    return {
        "root": root_node,
        "depth": depth,
        "nodes": below,
        "delta_window": _delta_window(model),
    }


async def area_detail_payload(
    session: AsyncSession, repo_id: str, model: AreaReadModel, area: str
) -> dict[str, Any] | None:
    """The area card, its trend read from the newest :data:`TREND_POINTS` snapshots."""
    if area not in model.index.nodes:
        return None
    key = await _details.key(session, repo_id, area, await _area_stamp(session, repo_id))
    if key in _details:
        return _details.get(key)
    trend = []
    for snap in await crud.list_health_snapshots(session, repo_id, limit=TREND_POINTS):
        score = area_score(_score_map(snap.per_file_scores_json), area, model.weights)
        if score is not None:
            trend.append({"taken_at": _iso(snap.taken_at), "health": score})
    detail = area_detail(model.index, area, trend=trend)
    return _details.remember(key, {**detail, "delta_window": _delta_window(model)})
