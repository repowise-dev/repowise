"""Run an ``issue-source`` against a local index: ask about the defect window, store, re-walk.

The host side of the seam for a checkout repowise indexed. A host without a
checkout (a server that keeps only the index) fetches on its own and calls
:func:`~repowise.core.persistence.crud.issue_links.store_issue_links` itself.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

from repowise.core.evidence.sources import FetchOutcome

from .sources import IssueCommit, IssueRequest, IssueSource, StoredLink, run_issue_fetch

logger = structlog.get_logger(__name__)

__all__ = ["issue_request", "stored_tracker_fixes", "sync_issues"]


def issue_request(
    repo_path: str | Path,
    repository_id: str,
    *,
    repository_name: str = "",
    settings: Mapping[str, Any] | None = None,
    credentials: Any = None,
) -> IssueRequest:
    """The request for *repo_path*: every commit the fix walk reads, with its change number."""
    from repowise.core.forges.changes import change_number
    from repowise.core.forges.detect import read_remote_ref
    from repowise.core.ingestion.git_indexer import GitIndexer

    indexer = GitIndexer(repo_path, read_only=True)
    commits = [
        IssueCommit(sha, subject, body, change_number(subject, body, indexer.forge))
        for sha, subject, body in indexer.fix_window_commits()
    ]
    return IssueRequest(
        repository_id=repository_id,
        repository_name=repository_name,
        commits=commits,
        remote=read_remote_ref(repo_path),
        settings=dict(settings or {}),
        credentials=credentials,
    )


async def sync_issues(
    session: Any,
    repository_id: str,
    repo_path: str | Path,
    source: IssueSource,
    *,
    repository_name: str = "",
    settings: Mapping[str, Any] | None = None,
    credentials: Any = None,
) -> FetchOutcome:
    """Fetch *source*'s links for the defect window and store them in *session*.

    When a bug or incident link came or went on a commit the index already
    holds, the next ``repowise update`` re-reads git history once, so its fix
    counts match a fresh index; links on newer commits ride the update itself.
    The caller commits *session*.
    """
    from repowise.core.persistence.crud.issue_links import store_issue_links

    request = await asyncio.to_thread(
        issue_request,
        repo_path,
        repository_id,
        repository_name=repository_name,
        settings=settings,
        credentials=credentials,
    )
    covered = [c.sha for c in request.commits]

    async def _store(links: list[StoredLink], complete: bool) -> dict[str, Any]:
        return await store_issue_links(
            session,
            repository_id,
            links,
            source=source.name,
            covered_shas=covered if complete else None,
        )

    outcome = await run_issue_fetch(source, request, _store)
    changed = [sha for stored in outcome.stored for sha in stored.get("defect_changed", ())]
    if changed and await _any_indexed(session, repository_id, repo_path, changed):
        from repowise.core.ingestion.git_indexer import (
            GIT_HISTORY_ISSUE_LINKS_REWALK,
            request_history_rewalk,
        )

        request_history_rewalk(repo_path, GIT_HISTORY_ISSUE_LINKS_REWALK)
    return outcome


async def _any_indexed(
    session: Any, repository_id: str, repo_path: str | Path, shas: list[str]
) -> bool:
    """Whether any of *shas* is in the indexed commit's history; True when that is unknown."""
    from repowise.core.persistence.models import Repository

    repo_row = await session.get(Repository, repository_id)
    head = getattr(repo_row, "head_commit", None)
    if not head:
        return True
    return await asyncio.to_thread(_reachable_any, repo_path, head, shas)


def _reachable_any(repo_path: str | Path, head: str, shas: list[str]) -> bool:
    from repowise.core.git_repo import Repo

    try:
        repo = Repo(repo_path, search_parent_directories=True)
    except Exception:
        return True
    try:
        for sha in shas:
            try:
                repo.git.merge_base("--is-ancestor", sha, head)
                return True
            except Exception:
                continue
        return False
    finally:
        with contextlib.suppress(Exception):
            repo.close()


async def stored_tracker_fixes(repo_path: str | Path) -> frozenset[str]:
    """The tracker fixes stored in *repo_path*'s index, read before a git walk.

    Empty when there is no index, no repository row or no ``issue_links``
    table yet: the walk then counts exactly what the git rule finds.
    """
    from repowise.core.persistence.database import (
        create_engine,
        create_session_factory,
        get_session,
        has_db_store,
        resolve_db_url,
    )

    root = Path(repo_path)
    if not has_db_store(root):
        return frozenset()
    url = resolve_db_url(root)
    # A configured SQLite file that does not exist yet stays that way: opening
    # it would create the store a dry run or a first index has not written.
    if url.startswith("sqlite") and not Path(url.split(":///", 1)[-1]).is_file():
        return frozenset()
    engine = create_engine(url)
    try:
        from repowise.core.persistence.crud import get_repository_by_path
        from repowise.core.persistence.crud.issue_links import load_tracker_fixes

        async with get_session(create_session_factory(engine)) as session:
            # The lookup every reader shares, so a moved checkout still finds its row.
            repo = await get_repository_by_path(session, str(root))
            if repo is None:
                return frozenset()
            return await load_tracker_fixes(session, repo.id)
    except Exception as exc:
        # An index older than the table, or a locked store: count by git alone.
        logger.warning("stored_tracker_fixes_failed", error=type(exc).__name__)
        return frozenset()
    finally:
        await engine.dispose()
