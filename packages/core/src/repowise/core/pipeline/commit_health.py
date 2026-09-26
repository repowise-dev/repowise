"""Keep the per-commit health delta rows current, for both index paths.

One entry point, :func:`refresh_commit_health`, used by the full index and by
the incremental update. The difference between them is only which shas they
hand over; everything else — what is already scanned, what the budget allows,
how the rows are written — is the same decision and lives here once.

The scan is bounded on purpose — see ``change_health.commit_scan`` for the
per-file cost that forces it. A full index seeds the most recent
:data:`DEFAULT_COMMIT_LIMIT` commits and stops at
:data:`DEFAULT_BUDGET_SECONDS`; this repository's 911 commits would otherwise
take about half an hour. Everything older has no row, which the commit sheet
renders as "not analysed" rather than "nothing found", and updates extend the
covered window one push at a time.
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


def budget_seconds() -> float:
    return _env_number("REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS)


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


async def _scan_and_store(
    session: Any, repo_id: str, repo_path: str, pending: list[str], budget: float
) -> dict[str, int]:
    from repowise.core.analysis.change_health.commit_scan import scan_commits
    from repowise.core.persistence.crud import upsert_commit_health_bulk

    scan = await asyncio.to_thread(scan_commits, repo_path, pending, budget_seconds=budget)
    if scan.delta_rows:
        await upsert_commit_health_bulk(session, repo_id, scan.delta_rows, scan.finding_rows)
    if scan.exhausted_budget:
        logger.info("commit_health_budget_exhausted", scanned=scan.scanned, budget=budget)
    return {
        "candidates": len(pending),
        "scanned": scan.scanned,
        "skipped": scan.skipped,
        "stored": len(scan.delta_rows),
        "findings": len(scan.finding_rows),
    }


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
