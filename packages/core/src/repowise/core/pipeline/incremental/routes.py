"""Route table and statement-site refresh on an update, see
:mod:`repowise.core.workspace.repo_routes` and :mod:`repowise.core.workspace.statement_sites`."""

from __future__ import annotations

from typing import Any

from ._common import LogFn, _noop_log


async def refresh_routes(
    session: Any,
    repo_id: str,
    repo_path: Any,
    file_diffs: list | None,
    *,
    log: LogFn | None = None,
) -> int | None:
    """Re-read the repo's routes when this update's diff can move one.

    Deleted paths and a rename's old path count: a route file that left must
    drop its rows. Returns the
    rows written, or None when the read was skipped or failed.
    """
    from repowise.core.workspace.repo_routes import refresh_route_table

    paths = [p for fd in file_diffs or [] for p in (fd.path, getattr(fd, "old_path", None)) if p]
    written = await refresh_route_table(session, repo_id, repo_path, changed_paths=paths)
    if written is not None:
        (log or _noop_log)(f"Routes refreshed: [cyan]{written}[/cyan]")
    return written


async def refresh_sites(
    session: Any,
    repo_id: str,
    repo_path: Any,
    file_diffs: list | None,
    *,
    log: LogFn | None = None,
) -> int | None:
    """Re-read the repo's statement sites when this update's diff can move one
    (see :mod:`repowise.core.workspace.statement_sites`)."""
    from repowise.core.workspace.statement_sites import refresh_statement_sites

    paths = [p for fd in file_diffs or [] for p in (fd.path, getattr(fd, "old_path", None)) if p]
    written = await refresh_statement_sites(session, repo_id, repo_path, changed_paths=paths)
    if written is not None:
        (log or _noop_log)(f"Statement sites refreshed: [cyan]{written}[/cyan]")
    return written
