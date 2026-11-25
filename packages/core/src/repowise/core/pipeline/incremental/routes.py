"""Route table refresh on an update, see :mod:`repowise.core.workspace.repo_routes`."""

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

    Deleted paths count: a removed route file must drop its rows. Returns the
    rows written, or None when the read was skipped or failed.
    """
    from repowise.core.workspace.repo_routes import refresh_route_table

    written = await refresh_route_table(
        session, repo_id, repo_path, changed_paths=[fd.path for fd in file_diffs or []]
    )
    if written is not None:
        (log or _noop_log)(f"Routes refreshed: [cyan]{written}[/cyan]")
    return written
