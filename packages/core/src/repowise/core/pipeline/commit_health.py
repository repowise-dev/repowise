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
renders as "not analysed" rather than "nothing found", and updates extend the
covered window one push at a time.

An update scans after its persist transaction has committed, never inside it:
the scan is minutes of CPU at worst, and holding SQLite's write lock that long
blocks every other writer (the post-commit hook's next update, MCP writes). It
also scans the newest commits in the store rather than only the ones it just
captured, so a commit an earlier update's budget did not reach is picked up by
a later one instead of staying "not analysed" until a full index.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Commits a single run will scan, newest first. Sized so a full index of a
#: repository this size pays minutes, not half an hour.
DEFAULT_COMMIT_LIMIT = 150

#: Wall clock one run may spend. The real guard: commit cost varies by an
#: order of magnitude with diff size, so a count alone bounds nothing.
DEFAULT_BUDGET_SECONDS = 300.0

#: Wall clock one update may spend. An update runs on every commit through the
#: post-commit hook, so it gets a fifth of the full index's budget. A commit
#: costs ~5 s on this repository under load (~2 s idle), so this covers about a
#: dozen commits per run, a normal push in one update, and a longer gap drains
#: over the next few updates instead of stalling one for five minutes.
UPDATE_BUDGET_SECONDS = 60.0


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


def _stats(pending: list[str], scan: Any) -> dict[str, int]:
    return {
        "candidates": len(pending),
        "scanned": scan.scanned,
        "skipped": scan.skipped,
        "stored": len(scan.delta_rows),
        "findings": len(scan.finding_rows),
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
    nothing = dict.fromkeys(("candidates", "scanned", "skipped", "stored", "findings"), 0)
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


async def refresh_commit_health_after_update(
    session_factory: Any,
    repo_id: str,
    repo_path: str,
    *,
    budget: float | None = None,
) -> dict[str, int]:
    """The update's scan, run once its persist transaction has committed.

    Reads the window, scans with no session open, then writes the rows in a
    transaction of their own, so the write lock is held for the write and not
    for the scan. The window is the newest :data:`DEFAULT_COMMIT_LIMIT` commits
    in the store, the same window a full index seeds, newest first: the commits
    a reader opens first are covered first, and a backlog older than the window
    is not chased.

    Never raises, and touches nothing the update already committed.
    """
    from repowise.core.persistence import get_session
    from repowise.core.persistence.crud import get_git_commits, upsert_commit_health_bulk

    limit = commit_limit()
    nothing = dict.fromkeys(("candidates", "scanned", "skipped", "stored", "findings"), 0)
    if limit <= 0:
        return nothing
    try:
        async with get_session(session_factory) as session:
            window = [
                row.sha
                for row in await get_git_commits(session, repo_id, limit=limit, sort="date")
            ]
            if not window:
                return nothing
            pending = await _pending_shas(session, repo_id, repo_path, window, limit)
        if not pending:
            return nothing
        scan = await _scan(
            repo_path,
            pending,
            budget_seconds(UPDATE_BUDGET_SECONDS) if budget is None else budget,
        )
        if scan.delta_rows:
            async with get_session(session_factory) as session:
                await upsert_commit_health_bulk(
                    session, repo_id, scan.delta_rows, scan.finding_rows
                )
        return _stats(pending, scan)
    except Exception as exc:
        logger.debug("commit_health_refresh_failed", error=str(exc))
        return nothing
