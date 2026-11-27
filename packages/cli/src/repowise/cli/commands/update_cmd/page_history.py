"""Trim every page's version history to ``wiki.page_versions_keep``.

A page write trims only that page's history, so a page no update rewrites
keeps whatever history it had before the cap existed, or before the cap was
lowered. Each update asks the ``page_id`` index whether any page holds more
than the cap (milliseconds) and trims the whole repository only when one does.
No marker is kept: the store itself says whether a trim is owed, so a failed
trim, or a cap raised and lowered again between updates, is caught next time.

The file does not shrink: SQLite keeps the freed pages and reuses them for
later writes before it grows the file. No VACUUM runs here. It rewrites the
whole store (5 s for 1.6 GB on a fast SSD, longer on slower disks), needs that
much free disk again, and holds off every other writer, including open MCP
sessions, for the duration; a post-commit update must not do that unasked.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def trim_page_history(repo_path: Path, *, dry_run: bool) -> int | None:
    """Trim every page's history to the cap when any page is over it.

    Returns the versions deleted, or ``None`` when nothing ran. Best-effort:
    a failure is logged and the next update tries again.
    """
    if dry_run:
        return None
    from repowise.cli.helpers import run_async
    from repowise.core.persistence.crud import page_versions_keep

    keep = page_versions_keep(repo_path)
    if keep is None:  # an unreadable cap trims nothing; doctor names the bad value
        return None
    try:
        deleted = run_async(_trim(repo_path, keep))
    except Exception as exc:  # never block a routine update on housekeeping
        log.debug("page_history_trim_failed", error=str(exc))
        return None
    if deleted:
        log.info("page_history_trimmed", deleted=deleted, keep=keep)
    return deleted


async def _trim(repo_path: Path, keep: int) -> int | None:
    from repowise.cli.helpers import repo_index_session
    from repowise.core.persistence.crud import page_versions_over_cap, prune_page_versions

    async with repo_index_session(repo_path) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        if not await page_versions_over_cap(session, keep):
            return None
        return await prune_page_versions(session, keep, repository_id=repo_id)
