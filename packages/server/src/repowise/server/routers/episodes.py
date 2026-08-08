"""/api/repos/{repo_id}/episodes — the dated things that happened to the code.

Reads the sidecar episode store (``.repowise/episodes/episodes.db``), not the
SQLAlchemy wiki database, so every handler here resolves the repository's
checkout first and degrades to ``available: false`` when there is nothing on
disk. That is the convention the distill-savings endpoint set for a sidecar
store, and it is what lets a dashboard render a real cold-start state instead
of an error boundary.

Three constraints are inherited rather than re-decided, and each one has cost
somebody a defect already:

**Shareable tiers only.** ``SERVED_TIERS`` excludes the transcript tier, which
is per-machine — two people opening the same repository's dashboard would
otherwise see different pages, and one of them would see sessions the other
never ran.

**The store is never opened to answer a question.** ``EpisodeStore.__init__``
creates the database and runs the schema, so constructing it on a repository
that has never derived episodes would grow a database as a side effect of a
page load. Every handler checks ``is_file()`` first.

**Currency is asked once, on the detail route only.** ``episode_currency``
shells out to ``git rev-list`` at roughly 60 ms; a fifty-row page asking per
row would be three seconds of subprocess. The list routes carry
``free_currency`` instead, which answers from stamps already in the row.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.precedent.currency import episode_currency, free_currency
from repowise.core.precedent.store import EpisodeStore, default_store_path
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.mcp_server._episodes import SERVED_TIERS
from repowise.server.schemas import (
    EpisodeCountsResponse,
    EpisodeDetail,
    EpisodeListResponse,
    EpisodeSummary,
)

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/repos",
    tags=["episodes"],
    dependencies=[Depends(verify_api_key)],
)

#: Rows one page may hold. The store's own per-tier ceiling is 5,000 and git
#: bodies run to hundreds of characters, so an unbounded page is a multi-
#: megabyte response; summaries carry no body, which is what makes even the
#: maximum affordable.
_MAX_PAGE = 200

#: Episodes returned for one file. Small because this answers "what happened
#: here" beside a file, where a reader takes the recent ones — not an archive.
_MAX_BY_FILE = 20


async def _repo_root(session: AsyncSession, repo_id: str) -> Path | None:
    """The repository's checkout, or None when there is nothing to read.

    404s only for a repository that does not exist. A repository that exists
    but has no usable local path is not an error — it is a repository this
    server cannot read a sidecar store for, which the caller renders as an
    unavailable feature rather than a failure.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if not repo.local_path:
        return None
    return Path(repo.local_path)


@contextmanager
def _open_store(root: Path | None) -> Iterator[EpisodeStore | None]:
    """The store, or None when there is none to open.

    **This does not open read-only, and the distinction matters.** The
    existence check keeps a read from *creating* a store, which is the
    invariant worth having: a repository that never derived episodes must not
    grow a database because somebody loaded a page. But once the file exists,
    ``EpisodeStore.__init__`` runs the schema script and will rebuild a
    missing FTS or node index, so a GET here can perform DDL and a backfill.
    That is bounded (it happens once, then the objects exist) and it is the
    same constructor every other reader uses, but it is not "read-only" and
    calling it that would be the kind of comment this layer has been burned
    by. A genuinely read-only open belongs with the store, not here.
    """
    if root is None:
        yield None
        return
    store_path = default_store_path(root)
    if not store_path.is_file():
        yield None
        return
    try:
        store = EpisodeStore(store_path)
    except Exception:
        _log.warning("episode store open failed for %s", store_path, exc_info=True)
        yield None
        return
    try:
        yield store
    finally:
        store.close()


def _read(root: Path | None, fn, default):
    """Run one store read, degrading to *default* on any failure.

    The open guard above catches a store that cannot be opened; this catches
    one that opens and then fails on a query. The realistic case is not
    corruption but ``SQLITE_BUSY`` past the 5 s timeout — a dashboard polling
    while an index writes episodes — and a 500 on the page for that would be
    a worse outcome than the feature saying it has nothing.
    """
    with _open_store(root) as store:
        if store is None:
            return default
        try:
            return fn(store)
        except Exception:
            _log.warning("episode store read failed", exc_info=True)
            return default


@router.get("/{repo_id}/episodes", response_model=EpisodeListResponse)
async def list_episodes(
    repo_id: str,
    tier: str | None = Query(None, description="Filter to one tier: structural | git"),
    kind: str | None = Query(None, description="Filter to one kind, e.g. code_fix"),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> EpisodeListResponse:
    """A page of episodes, newest first, without their bodies.

    Zero git calls by construction: the only currency shown is the one derived
    from stamps already in the row. Runs inline rather than on a worker thread
    because the work is two bounded SQLite reads, matching the other sidecar
    readers in this package; the detail route below is the one that threads,
    because it shells out. Bounded rather than indexed — there is no index on
    ``birth_at``, so each page sorts the filtered set in a temp b-tree, which
    the store's own row cap is what makes affordable.
    """
    root = await _repo_root(session, repo_id)
    tiers = _tier_allowlist(tier)

    def read(store: EpisodeStore) -> tuple[int, list[dict]]:
        return (
            store.count_episodes(tiers=tiers, kind=kind),
            store.list_episodes(
                tiers=tiers, kind=kind, limit=limit, offset=offset, with_body=False
            ),
        )

    got = _read(root, read, None)
    if got is None:
        return EpisodeListResponse(available=False)
    total, rows = got
    return EpisodeListResponse(
        total=total,
        episodes=[EpisodeSummary.from_row(r, still_true=free_currency(r)) for r in rows],
    )


@router.get("/{repo_id}/episodes/counts", response_model=EpisodeCountsResponse)
async def episode_counts(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> EpisodeCountsResponse:
    """Totals by tier and by kind.

    Declared above ``/{episode_id}`` deliberately: FastAPI matches in
    declaration order, so below it "counts" would be looked up as an episode
    id — the same trap the decisions router documents.
    """
    root = await _repo_root(session, repo_id)

    def read(store: EpisodeStore) -> tuple[dict[str, int], dict[str, int]]:
        return (
            store.group_counts("tier", tiers=SERVED_TIERS),
            store.group_counts("kind", tiers=SERVED_TIERS),
        )

    got = _read(root, read, None)
    if got is None:
        return EpisodeCountsResponse(available=False)
    by_tier, by_kind = got
    return EpisodeCountsResponse(
        total=sum(by_tier.values()), by_tier=by_tier, by_kind=by_kind
    )


@router.get("/{repo_id}/episodes/by-file", response_model=EpisodeListResponse)
async def episodes_by_file(
    repo_id: str,
    path: str = Query(..., description="Repo-relative file or directory path"),
    limit: int = Query(_MAX_BY_FILE, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_db_session),
) -> EpisodeListResponse:
    """Episodes bound at, above or below *path*, newest first.

    Normally served through the store's node index — 0.22 ms against 22 ms for
    a scan over the JSON scope column at the store's row cap. ``total`` is the
    count for this path, which is what makes the number on a file page a
    measured one rather than the size of the window.

    **Threaded, unlike the other list routes, and the index is why.** When
    :meth:`~EpisodeStore._ensure_node_index` cannot build the index — a
    read-only database, a disk error — both reads silently fall back to a full
    scan. That path is the one thing here that can take tens of milliseconds,
    so it does not get to sit on the event loop; the store, not this route,
    decides which one runs.
    """
    root = await _repo_root(session, repo_id)

    def read(store: EpisodeStore) -> tuple[int, list[dict]]:
        return (
            store.count_by_node([path], tiers=SERVED_TIERS).get(path, 0),
            store.list_by_node([path], tiers=SERVED_TIERS, limit=limit),
        )

    got = await asyncio.to_thread(_read, root, read, None)
    if got is None:
        return EpisodeListResponse(available=False)
    total, rows = got
    return EpisodeListResponse(
        total=total,
        episodes=[EpisodeSummary.from_row(r, still_true=free_currency(r)) for r in rows],
    )


@router.get("/{repo_id}/episodes/{episode_id}", response_model=EpisodeDetail)
async def get_episode(
    repo_id: str,
    episode_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> EpisodeDetail:
    """One episode, whole, with the currency question actually asked of git.

    The only route here that spends a subprocess, and it spends exactly one.
    It runs on a worker thread for that reason — the convention this package
    already applies to every episode read that shells out.

    Two 404s with different wording, because this is the one route where the
    difference decides what a client does next: a repository with no episode
    store at all is a feature that has nothing here, while an unknown id on a
    populated store is a bad request worth not retrying. The list routes make
    the same distinction with ``available``, which a single-object response
    has no room for.
    """
    root = await _repo_root(session, repo_id)
    if not _has_store(root):
        raise HTTPException(status_code=404, detail="No episodes recorded for this repository")
    row = await asyncio.to_thread(_load_episode, root, episode_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    verdict, current = row.pop("_verdict")
    return EpisodeDetail.from_row(row, sentence=verdict, current=current)


def _has_store(root: Path | None) -> bool:
    """Whether this repository has an episode store at all. Never creates one."""
    return root is not None and default_store_path(root).is_file()


def _load_episode(root: Path | None, episode_id: str) -> dict | None:
    """The row plus its git verdict, or None. Blocking; call on a thread."""
    if root is None:
        return None
    row = _read(root, lambda s: s.get_episode(episode_id, tiers=SERVED_TIERS), None)
    if row is None:
        return None
    # Asked outside the store context: the git query needs the checkout, not
    # the database, and holding a SQLite handle open across a subprocess buys
    # nothing.
    verdict = episode_currency(row, root=root)
    row["_verdict"] = (verdict.sentence, verdict.current)
    return row


def _tier_allowlist(tier: str | None) -> tuple[str, ...]:
    """The tiers to serve, narrowed by *tier* but never widened past the set.

    An unknown or non-shareable tier selects nothing rather than everything,
    which is the difference between a filter that returns an empty page and
    one that quietly serves the transcript tier to a stranger.
    """
    if tier is None:
        return tuple(SERVED_TIERS)
    return (tier,) if tier in SERVED_TIERS else ()
