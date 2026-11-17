"""The estate report document for a workspace, built once for every surface.

``repowise report estate`` and ``GET /api/workspace/estate`` both call
:func:`build_estate_document`, so the CLI and the web page read one document.
It opens each member's index the way the estate sections do
(:meth:`WorkspaceReportContext.each_index`), runs any repo-scope section, then
the workspace-scope sections once over the stored workspace artifacts.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from repowise.core.report.build import assemble, available_sections, collect_repo, collect_workspace
from repowise.core.report.model import ReportContext, Section, WorkspaceReportContext

#: A member whose index exists but was not read: ``behind`` when the store
#: lacks a table or column this version reads even after the reconcile
#: ``init_db`` runs, ``unreadable`` when opening it raised a storage error or
#: it holds no repository row. ``indexed: False`` is kept for no index at all.
INDEX_STATES = ("behind", "unreadable")


async def _member(
    alias: str, root: Path, sections: Sequence[tuple[str, Section]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(repos entry, repo-scope sections)`` for one member.

    Storage errors become the member's ``index_state``; any other error
    propagates, so a defect is never reported as a missing index.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.database import schema_behind
    from repowise.core.workspace.registry import open_repo_db, repo_db_path
    from repowise.core.workspace.repo_index import checkout_repository

    entry: dict[str, Any] = {"alias": alias, "name": root.name}
    if not repo_db_path(root).is_file():
        return {**entry, "indexed": False}, []
    unreadable = ({**entry, "index_state": "unreadable"}, [])
    try:
        engine, sessions = await open_repo_db(root)
    except (SQLAlchemyError, OSError):
        return unreadable
    try:
        if await schema_behind(engine):
            return {**entry, "index_state": "behind"}, []
        async with sessions() as session:
            repo = await checkout_repository(session, root)
            if repo is None:
                return unreadable
            ctx = ReportContext(session, repo.id, root, alias=alias, head_commit=repo.head_commit)
            return await collect_repo(ctx, list(sections))
    except (SQLAlchemyError, OSError):
        return unreadable
    finally:
        await engine.dispose()


async def build_estate_document(
    ws_root: Path, sections: Sequence[tuple[str, Section]] | None = None
) -> dict[str, Any]:
    """The ``estate`` report over the workspace at *ws_root*; *sections* defaults
    to every registered estate section."""
    picked = list(sections) if sections is not None else available_sections("estate")
    ctx = WorkspaceReportContext(ws_root)
    parts = [
        await _member(entry.alias, (ws_root / entry.path).resolve(), picked)
        for entry in ctx.config().repos
    ]
    ctx.repos = [entry for entry, _ in parts]
    workspace = await collect_workspace(ctx, picked)
    document = assemble({"kind": "workspace", "name": ws_root.name}, parts, workspace=workspace)
    document["kind"] = "estate"
    return document


__all__ = ["INDEX_STATES", "build_estate_document"]
