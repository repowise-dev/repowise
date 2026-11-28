"""``/api/repos/{repo_id}/plugins``: installed plugins, their sources, and "Sync now".

The read is ``repowise plugins --format json`` run in the repository, so the
page and an agent see the same fields. The sync runs ``repowise sources sync``
as a background task and answers at once; the page reads the list again to
see each source's last sync. Both run the CLI in a child process, as the
Setup routes do, because the server never imports ``repowise.cli``.

A child process costs a few seconds (it imports every seam), so the listing
is kept per repository for :data:`REPORT_TTL_SECONDS`. A sync that ends
reads it again before it reports done, and a settings write drops it
(:func:`forget_report`).

Mutating routes follow the usual auth: ``verify_api_key`` checks the
credential and, through the authorizer, write access to this repository.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
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


_SYNC_STOPPED = "The sync stopped with an error. Run it in a terminal: repowise sources sync"

#: How long a listing is served from memory. A plugin installed or a value set
#: in a terminal shows up after this; one set in the web UI shows up at once.
REPORT_TTL_SECONDS = 60.0

_LIST_FAILED = "Couldn't list plugins here. Run it in a terminal: repowise plugins"


# Kept per process: a second worker or a terminal `repowise sources sync` is
# not seen here. `repowise serve` runs one worker; a deployment with more
# would need the lock in the store, next to the sync ledger.
@dataclass
class _RepoSyncs:
    """The syncs this server started for one repository."""

    #: Source names a running sync covers, or ``ALL_SOURCES``.
    running: set[str] = field(default_factory=set)
    #: Why the last finished sync did not run, until a listing reports it.
    error: str | None = None


def _syncs(request: Request, repo_id: str) -> _RepoSyncs:
    table: dict[str, _RepoSyncs] | None = getattr(request.app.state, "plugin_syncs", None)
    if table is None:
        table = request.app.state.plugin_syncs = {}
    return table.setdefault(repo_id, _RepoSyncs())


def _reports(app: Any) -> dict[str, tuple[float, dict[str, Any]]]:
    """Kept listings by checkout path, with when each was read."""
    table = getattr(app.state, "plugin_reports", None)
    if table is None:
        table = app.state.plugin_reports = {}
    return table


def forget_report(app: Any, repo_path: Path) -> None:
    """Drop the kept listing of the checkout at *repo_path*, after a write that can change it."""
    _reports(app).pop(str(repo_path), None)


async def _read_report(app: Any, repo_path: Path) -> dict[str, Any] | None:
    """Run ``repowise plugins --format json`` and keep what it printed."""
    payload = await asyncio.to_thread(setup_cli.plugins_json, repo_path)
    if payload is not None:
        _reports(app)[str(repo_path)] = (time.monotonic(), payload)
    return payload


async def _report(app: Any, repo: Repository) -> dict[str, Any] | None:
    """The listing for *repo*: the kept one while fresh, else a new read."""
    repo_path = Path(repo.local_path)
    kept = _reports(app).get(str(repo_path))
    if kept is not None and time.monotonic() - kept[0] < REPORT_TTL_SECONDS:
        return kept[1]
    return await _read_report(app, repo_path)


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
    # Raw output can quote a plugin's error text, which is not masked: never send it.
    return None if run.ok else _SYNC_STOPPED


@router.get("/api/repos/{repo_id}/plugins", response_model=PluginsResponse)
async def list_plugins(
    request: Request, repo: Repository = Depends(resolve_local_repo)
) -> dict[str, Any]:
    """``repowise plugins --format json`` for this repository, plus the syncs running here."""
    payload = await _report(request.app, repo)
    if payload is None:
        raise HTTPException(status_code=502, detail=_LIST_FAILED)
    syncs = _syncs(request, repo.id)
    # Reported once, so a later sync run in a terminal never hides behind it.
    error, syncs.error = syncs.error, None
    return {**payload, "syncing": sorted(syncs.running), "sync_error": error}


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
    if source is not None:
        await _check_known(request.app, repo, source)
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

    app = request.app

    async def run() -> None:
        try:
            try:
                result = await asyncio.to_thread(setup_cli.sources_sync, repo_path, source)
                syncs.error = _refusal(result)
            except Exception:  # a task error must never leave the repo marked as syncing
                logger.exception("plugin_sync_failed", extra={"repo_id": repo.id})
                syncs.error = _SYNC_STOPPED
            # Read before the sync reports done, so the next listing shows its last sync.
            forget_report(app, repo_path)
            try:
                await _read_report(app, repo_path)
            except Exception:
                logger.exception("plugin_list_failed", extra={"repo_id": repo.id})
        finally:
            syncs.running = set()

    task = asyncio.create_task(run(), name=f"plugin-sync-{repo.id}")
    bg_tasks: set[asyncio.Task] = request.app.state.background_tasks
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return {"accepted": True, "syncing": sorted(syncs.running)}


async def _check_known(app: Any, repo: Repository, source: str) -> None:
    """Refuse a source name the listing does not have, naming the ones it does.

    A name missing from a kept listing is looked up once more in a new one (a
    plugin installed since). Skipped when the listing cannot be read: the sync
    then reports it.
    """
    known = _source_names(await _report(app, repo))
    if known is not None and source not in known:
        known = _source_names(await _read_report(app, Path(repo.local_path)))
    if known is None or source in known:
        return
    names = ", ".join(known) if known else "none"
    raise HTTPException(
        status_code=404,
        detail=f"No source named {source} is registered. Sources here: {names}.",
    )


def _source_names(payload: dict[str, Any] | None) -> list[str] | None:
    rows = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return sorted({r["source"] for r in rows if isinstance(r, dict) and "source" in r})
