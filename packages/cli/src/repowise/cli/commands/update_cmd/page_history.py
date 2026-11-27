"""Trim every page's version history to ``wiki.page_versions_keep``.

A page write trims only that page's history, so a page no update rewrites
keeps whatever history it had before the cap existed, or before the cap
changed. This pass trims all of them once, and again whenever the cap changes.
``state.json`` records the cap the store was last trimmed to. (A raised cap
trims nothing, but recording it means lowering it again later trims the
history pages grew under the raised one.)

The file does not shrink: SQLite keeps the freed pages and reuses them for
later writes before it grows the file. No VACUUM runs here. It rewrites the
whole store (5 s for 1.6 GB on a fast SSD, longer on slower disks), needs that
much free disk again, and holds off every other writer, including open MCP
sessions, for the duration; a post-commit update must not do that unasked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: state.json key: the ``wiki.page_versions_keep`` the whole store was last trimmed to.
PAGE_VERSIONS_KEPT_KEY = "page_versions_kept"


def trim_page_history(repo_path: Path, state: dict[str, Any], *, dry_run: bool) -> int | None:
    """Trim every page's history when the store was not trimmed to the current cap.

    Returns the versions deleted, or ``None`` when nothing ran. Best-effort:
    a failure is logged and the next update tries again.
    """
    if dry_run:
        return None
    from repowise.cli.helpers import run_async, save_state
    from repowise.core.persistence.crud import page_versions_keep

    keep = page_versions_keep(repo_path)
    if keep is None:  # an unreadable cap trims nothing; doctor names the bad value
        return None
    if state.get(PAGE_VERSIONS_KEPT_KEY) == keep:
        return None
    try:
        deleted = run_async(_trim(repo_path, keep))
    except Exception as exc:  # never block a routine update on housekeeping
        log.debug("page_history_trim_failed", error=str(exc))
        return None
    if deleted is None:
        return None
    try:
        save_state(repo_path, {**state, PAGE_VERSIONS_KEPT_KEY: keep})
    except Exception as exc:  # unrecorded: the next update trims again, finding little
        log.debug("page_history_mark_failed", error=str(exc))
        return deleted
    state[PAGE_VERSIONS_KEPT_KEY] = keep
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
