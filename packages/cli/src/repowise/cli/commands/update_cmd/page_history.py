"""Trim every page's version history to ``wiki.page_versions_keep``.

A page write trims only that page's history, so a page no update rewrites
keeps whatever history it had before the cap existed, or before the cap was
lowered. This pass trims all of them once, and again only when the cap goes
down. ``state.json`` records the cap the store was last trimmed to.

The file does not shrink: SQLite keeps the freed pages and reuses them for
later writes, so the store stops growing instead. No VACUUM runs here. On a
store of a gigabyte or more it takes tens of seconds, needs as much free disk
again, and holds every other reader and writer off for that time, which a
post-commit update must not do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: state.json key: the ``wiki.page_versions_keep`` the whole store was last trimmed to.
PAGE_VERSIONS_KEPT_KEY = "page_versions_kept"


def trim_page_history(repo_path: Path, state: dict[str, Any], *, dry_run: bool) -> int | None:
    """Trim every page's history when the store has not been trimmed to the current cap.

    Returns the versions deleted, or ``None`` when nothing ran. Best-effort:
    a failure is logged and the next update tries again.
    """
    if dry_run:
        return None
    from repowise.cli.helpers import run_async, save_state
    from repowise.core.persistence.crud import page_versions_keep

    keep = page_versions_keep(repo_path)
    recorded = state.get(PAGE_VERSIONS_KEPT_KEY)
    if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded <= keep:
        return None
    try:
        deleted = run_async(_trim(repo_path, keep))
    except Exception as exc:  # never block a routine update on housekeeping
        log.debug("page_history_trim_failed", error=str(exc))
        return None
    if deleted is None:
        return None
    state[PAGE_VERSIONS_KEPT_KEY] = keep
    save_state(repo_path, state)
    if deleted:
        log.info("page_history_trimmed", deleted=deleted, keep=keep)
    return deleted


async def _trim(repo_path: Path, keep: int) -> int | None:
    from repowise.cli.helpers import repo_index_session
    from repowise.core.persistence.crud import prune_page_versions

    async with repo_index_session(repo_path) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        return await prune_page_versions(session, keep, repository_id=repo_id)
