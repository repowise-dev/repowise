"""Keep the per-commit health delta rows current, for both index paths.

Two entry points share one scan: :func:`refresh_commit_health` for the full
index, inside its transaction, and :func:`refresh_commit_health_after_update`
for the incremental update, after its transaction. What is already scanned,
what the budget allows and how the rows are written is the same decision and
lives here once.

The scan is bounded on purpose — see ``change_health.commit_scan`` for the
per-file cost that forces it. A full index seeds the most recent
:data:`DEFAULT_COMMIT_LIMIT` commits and stops at
:data:`DEFAULT_BUDGET_SECONDS`; this repository's 911 commits would otherwise
take about half an hour. Everything older has no row, which the commit sheet
renders as "not analysed" rather than "nothing found".

An update scans after its persist transaction has committed, never inside it:
the scan is minutes of CPU at worst, and holding SQLite's write lock that long
blocks every other writer (the post-commit hook's next update, MCP writes). It
also scans the newest commits in the store rather than only the ones it just
captured, so a commit an earlier update's budget did not reach is picked up by
a later one instead of staying "not analysed" until a full index. It spends
only :data:`UPDATE_BUDGET_SECONDS` inline; the rest of the window drains in a
detached process once the update has returned (:mod:`.commit_health_drain`).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Commits a single run will scan, newest first. Sized so a full index of a
#: repository this size pays minutes, not half an hour.
DEFAULT_COMMIT_LIMIT = 150

#: Wall clock one run may spend. The real guard: commit cost varies by an
#: order of magnitude with diff size, so a count alone bounds nothing.
DEFAULT_BUDGET_SECONDS = 300.0

#: Wall clock one update spends inline (~2-5 s a commit). Whatever it leaves,
#: a backlog after an analyzer bump included, drains in the background
#: (:mod:`.commit_health_drain`) at :data:`DEFAULT_BUDGET_SECONDS`.
UPDATE_BUDGET_SECONDS = 10.0


def _env_number(name: str, default: float) -> float:
    """Read a non-negative numeric override; ignore anything unparseable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def commit_limit() -> int:
    return int(_env_number("REPOWISE_COMMIT_HEALTH_COMMITS", DEFAULT_COMMIT_LIMIT))


def budget_seconds(default: float = DEFAULT_BUDGET_SECONDS) -> float:
    return _env_number("REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS", default)


def _is_fast_index(repo_path: str) -> bool:
    """Whether the index was built with ``--mode fast`` (essential git tier).

    Fast mode exists to skip the expensive per-commit and per-file git signals,
    so an update on such an index must not buy them back one commit at a time.
    """
    try:
        state = json.loads((Path(repo_path) / ".repowise" / "state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get("git_tier") == "essential"


def recent_shas(commit_rows: list[dict] | None) -> list[str]:
    """Shas from persisted commit rows, newest first.

    Sorted here rather than trusted from the walk: the budget is spent in this
    order, so "newest" has to be a property of the data, not of the caller.
    """
    rows = [row for row in (commit_rows or []) if row.get("sha")]
    rows.sort(key=lambda row: row.get("committed_at") or 0, reverse=True)
    return [row["sha"] for row in rows]


async def _pending_shas(
    session: Any, repo_id: str, repo_path: str, shas: list[str], limit: int
) -> list[str]:
    """The shas no current-analyzer row covers yet, newest first, capped."""
    from repowise.core.analysis.change_health.commit_scan import current_fingerprint
    from repowise.core.persistence.crud import get_scanned_commit_shas

    done = await get_scanned_commit_shas(
        session, repo_id, **current_fingerprint(repo_path)
    )
    return [sha for sha in shas if sha not in done][:limit]


async def _scan(repo_path: str, pending: list[str], budget: float) -> Any:
    from repowise.core.analysis.change_health.commit_scan import scan_commits

    scan = await asyncio.to_thread(scan_commits, repo_path, pending, budget_seconds=budget)
    if scan.exhausted_budget:
        logger.info("commit_health_budget_exhausted", scanned=scan.scanned, budget=budget)
    return scan


_STAT_KEYS = ("candidates", "scanned", "skipped", "stored", "findings", "exhausted", "deferred")


def _nothing() -> dict[str, int]:
    return dict.fromkeys(_STAT_KEYS, 0)


def _stats(pending: list[str], scan: Any) -> dict[str, int]:
    return {
        **_nothing(),
        "candidates": len(pending),
        "scanned": scan.scanned,
        "skipped": scan.skipped,
        "stored": len(scan.delta_rows),
        "findings": len(scan.finding_rows),
        "exhausted": int(scan.exhausted_budget),
    }


async def _scan_and_store(
    session: Any, repo_id: str, repo_path: str, pending: list[str], budget: float
) -> dict[str, int]:
    from repowise.core.persistence.crud import upsert_commit_health_bulk

    scan = await _scan(repo_path, pending, budget)
    if scan.delta_rows:
        await upsert_commit_health_bulk(session, repo_id, scan.delta_rows, scan.finding_rows)
    return _stats(pending, scan)


async def refresh_commit_health(
    session: Any,
    repo_id: str,
    repo_path: str,
    shas: list[str],
    *,
    limit: int | None = None,
    budget: float | None = None,
) -> dict[str, int]:
    """Scan the unscanned commits among *shas* and store what they did to health.

    *shas* must be newest first — the budget is spent in that order, so the
    commits a reader is most likely to open get covered first.

    Returns a small stats dict for logging. Never raises: a commit page missing
    one block is a much smaller problem than an index that will not finish.

    The writes run in a savepoint so that staying quiet is actually possible. A
    failed flush marks the whole session for rollback, and the caller keeps
    using it — so without the savepoint, swallowing the error here would hand
    back a poisoned session and take the entire index down several steps later.
    """
    limit = commit_limit() if limit is None else limit
    nothing = _nothing()
    if not shas or limit <= 0:
        return nothing
    try:
        async with session.begin_nested():
            pending = await _pending_shas(session, repo_id, repo_path, shas, limit)
            if not pending:
                return nothing
            return await _scan_and_store(
                session,
                repo_id,
                repo_path,
                pending,
                budget_seconds() if budget is None else budget,
            )
    except Exception as exc:
        logger.debug("commit_health_refresh_failed", error=str(exc))
        return nothing


async def scan_window(
    session_factory: Any, repo_id: str, repo_path: str, budget: float
) -> dict[str, int]:
    """Scan the store's newest unscanned commits for *budget* seconds, then write the rows.

    Reads the window, scans with no session open, then writes the rows in a
    transaction of their own, so the write lock is held for the write and not
    for the scan. The window is the newest :data:`DEFAULT_COMMIT_LIMIT` commits
    in the store, the same window a full index seeds, newest first: the commits
    a reader opens first are covered first, and a backlog older than the window
    is not chased. The caller holds the commit-health lock.
    """
    from repowise.core.persistence import get_session
    from repowise.core.persistence.crud import get_git_commits, upsert_commit_health_bulk

    limit = commit_limit()
    async with get_session(session_factory) as session:
        window = [
            row.sha for row in await get_git_commits(session, repo_id, limit=limit, sort="date")
        ]
        pending = await _pending_shas(session, repo_id, repo_path, window, limit) if window else []
    if not pending:
        return _nothing()
    scan = await _scan(repo_path, pending, budget)
    if scan.delta_rows:
        async with get_session(session_factory) as session:
            await upsert_commit_health_bulk(session, repo_id, scan.delta_rows, scan.finding_rows)
    return _stats(pending, scan)


async def refresh_commit_health_after_update(
    session_factory: Any,
    repo_id: str,
    repo_path: str,
    *,
    budget: float | None = None,
    background: bool = False,
) -> dict[str, int]:
    """The update's scan, run once its persist transaction has committed.

    Spends *budget* (default :data:`UPDATE_BUDGET_SECONDS`) inline. With
    *background*, what the budget leaves is handed to a detached drain that
    starts once this run releases its locks; a caller with no store left
    afterwards (a throwaway database) passes a full budget instead. When a
    drain already holds the scan lock the inline scan is skipped: the drain
    re-reads the window each round, and *background* queues one more behind it.

    Never raises, and touches nothing the update already committed.
    """
    from repowise.core.update_lock import (
        release_commit_health_lock,
        try_acquire_commit_health_lock,
    )

    if commit_limit() <= 0 or _is_fast_index(repo_path):
        return _nothing()
    stats = _nothing()
    if try_acquire_commit_health_lock(Path(repo_path)) is not None:
        stats["deferred"] = 1
    else:
        try:
            stats = await scan_window(
                session_factory,
                repo_id,
                repo_path,
                budget_seconds(UPDATE_BUDGET_SECONDS) if budget is None else budget,
            )
        except Exception as exc:
            logger.debug("commit_health_refresh_failed", error=str(exc))
        finally:
            release_commit_health_lock(Path(repo_path))
    if background and (stats["exhausted"] or stats["deferred"]):
        from .commit_health_drain import start_background_drain

        start_background_drain(repo_path, repo_id)
    return stats
