"""``/api/repos/{repo_id}/plugins``: installed plugins, their sources, and "Sync now".

The read is ``repowise plugins --format json`` run in the repository, so the
page and an agent see the same fields. The sync runs ``repowise sources sync``
as a background task and answers at once; the page reads the list again to
see each source's last sync. Both run the CLI in a child process, as the
Setup routes do, because the server never imports ``repowise.cli``.

Mutating routes follow the usual auth: ``verify_api_key`` checks the
credential and, through the authorizer, write access to this repository.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from repowise.core.persistence.models import Repository
from repowise.server.deps import verify_api_key
from repowise.server.routers._local_git import resolve_local_repo
from repowise.server.schemas.plugins import (
    PluginsResponse,
    SourcesSyncAccepted,
    SourcesSyncRequest,
)
from repowise.server.services import setup_cli

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plugins"], dependencies=[Depends(verify_api_key)])

#: In ``syncing``: a sync of every configured source is running.
ALL_SOURCES = "*"

#: A source name as plugins register them; never option-shaped.
_SOURCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


@dataclass
class _RepoSyncs:
    """The syncs this server started for one repository."""

    #: Source names a running sync covers, or ``ALL_SOURCES``.
    running: set[str] = field(default_factory=set)
    #: Why the last finished sync did not run, if it did not.
    error: str | None = None


def _syncs(request: Request, repo_id: str) -> _RepoSyncs:
    table: dict[str, _RepoSyncs] | None = getattr(request.app.state, "plugin_syncs", None)
    if table is None:
        table = request.app.state.plugin_syncs = {}
    return table.setdefault(repo_id, _RepoSyncs())


def _refusal(run: setup_cli.CliRun) -> str | None:
    """The CLI's refusal message when the sync did not run at all, else ``None``.

    A source that ran and failed is recorded in its own last sync; this is
    for a sync that never reached one (no index, unknown source, timeout).
    """
    if run.exit_code is None:
        return "The sync could not start or ran past its time limit."
    payload = run.json()
    if isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    if run.ok:
        return None
    lines = run.lines(limit=1)
    return lines[0] if lines else "The sync did not run."


@router.get("/api/repos/{repo_id}/plugins", response_model=PluginsResponse)
async def list_plugins(
    request: Request, repo: Repository = Depends(resolve_local_repo)
) -> dict[str, Any]:
    """``repowise plugins --format json`` for this repository, plus the syncs running here."""
    payload = await asyncio.to_thread(setup_cli.plugins_json, Path(repo.local_path))
    if payload is None:
        raise HTTPException(
            status_code=502,
            detail="Couldn't list plugins here. Run it in a terminal: repowise plugins",
        )
    syncs = _syncs(request, repo.id)
    return {**payload, "syncing": sorted(syncs.running), "sync_error": syncs.error}


@router.post(
    "/api/repos/{repo_id}/plugins/sync",
    response_model=SourcesSyncAccepted,
    status_code=202,
)
async def sync_sources(
    body: SourcesSyncRequest,
    request: Request,
    repo: Repository = Depends(resolve_local_repo),
) -> dict[str, Any]:
    """Start ``repowise sources sync`` (one source, or every configured one) in the background."""
    source = body.source
    if source is not None and not _SOURCE_RE.fullmatch(source):
        raise HTTPException(status_code=400, detail=f"Not a source name: {source}")
    syncs = _syncs(request, repo.id)
    if syncs.running:
        raise HTTPException(
            status_code=409, detail="A sync is already running for this repository."
        )
    repo_path = Path(repo.local_path)
    # ``*`` stands for every configured source: listing them first would cost
    # a second child process before this answers.
    syncs.running = {source or ALL_SOURCES}
    syncs.error = None

    async def run() -> None:
        try:
            result = await asyncio.to_thread(setup_cli.sources_sync, repo_path, source)
            syncs.error = _refusal(result)
        except Exception:  # a task error must never leave the repo marked as syncing
            logger.exception("plugin_sync_failed", extra={"repo_id": repo.id})
            syncs.error = "The sync stopped with an error. Run it in a terminal: repowise sources sync"
        finally:
            syncs.running = set()

    task = asyncio.create_task(run(), name=f"plugin-sync-{repo.id}")
    bg_tasks: set[asyncio.Task] = request.app.state.background_tasks
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return {"accepted": True, "syncing": sorted(syncs.running)}
