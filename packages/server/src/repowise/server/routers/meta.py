"""``/api/meta`` endpoints: repowise version freshness + release changelog.

Powers the web UI's upgrade banner, "what's new" view, and version footer. All
logic is shared with the CLI through :mod:`repowise.core.upgrade` so the server
never depends on ``repowise.cli``. Best-effort: PyPI / changelog / store-read
failures degrade to partial responses, never errors.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.upgrade import UpgradeTier, assess
from repowise.core.upgrade.changelog import ChangelogEntry, ChangelogHighlight
from repowise.core.upgrade.release import (
    ReleaseCheck,
    check_latest_version_cached,
    latest_highlights_cached,
    load_bundled_changelog,
    load_release_stats,
)
from repowise.server import __version__
from repowise.server.deps import resolve_session_factory, verify_api_key
from repowise.server.schemas import (
    ChangelogEntryModel,
    ChangelogHighlightModel,
    ChangelogResponse,
    ChangelogSectionModel,
    ReleaseStatsModel,
    VersionResponse,
)

router = APIRouter(prefix="/api/meta", tags=["meta"], dependencies=[Depends(verify_api_key)])

#: Generic, install-method-agnostic upgrade hint for the web (the server can't
#: know how the user installed the CLI). The CLI itself shows a tailored command.
_UPGRADE_COMMAND = "pip install -U repowise"


@router.get("/version", response_model=VersionResponse)
async def get_version(request: Request, repo_id: str | None = None) -> VersionResponse:
    """Server version + PyPI freshness, plus store status when ``repo_id`` given.

    The global version/freshness path needs no database; a session is opened
    lazily only when a ``repo_id`` is supplied (for the store-format fields).
    """
    # Off the event loop: a cache miss makes up to two 2 s network calls.
    check, latest_titles = await asyncio.to_thread(_freshness)
    current = next((e for e in load_bundled_changelog() if e.version == __version__), None)
    resp = VersionResponse(
        server_version=__version__,
        latest_version=check.latest_version,
        update_available=check.update_available,
        upgrade_command=_UPGRADE_COMMAND,
        highlights=[_highlight(h) for h in current.highlights] if current else [],
        latest_highlights=latest_titles,
    )

    if repo_id:
        await _attach_store_status(resp, repo_id, request)
    return resp


def _freshness() -> tuple[ReleaseCheck, list[str]]:
    """PyPI check, plus the newer release's highlight titles when there is one."""
    check = check_latest_version_cached(__version__)
    if check.update_available and check.latest_version:
        return check, latest_highlights_cached(check.latest_version)
    return check, []


async def _attach_store_status(resp: VersionResponse, repo_id: str, request: Request) -> None:
    """Fill the store-format fields for *repo_id*. Best-effort, never raises."""
    try:
        factory = resolve_session_factory(request.app.state, repo_id)
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, repo_id)
        if repo is None or not repo.local_path:
            return
        state_path = Path(repo.local_path) / ".repowise" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        verdict = assess(state)
        resp.store_format_version = verdict.from_store_version
        resp.store_compatible = verdict.tier != UpgradeTier.REINDEX_RECOMMENDED
        resp.reindex_recommended = verdict.reindex_recommended
        resp.reindex_command = verdict.reindex_command
    except Exception:
        return


@router.get("/changelog", response_model=ChangelogResponse)
async def get_changelog(limit: int = 20) -> ChangelogResponse:
    """Return repowise's bundled changelog, newest first, capped at *limit*."""
    entries = load_bundled_changelog()[: max(0, limit)]
    stats = load_release_stats()
    return ChangelogResponse(entries=[_entry(e, stats.get(e.version)) for e in entries])


def _highlight(h: ChangelogHighlight) -> ChangelogHighlightModel:
    return ChangelogHighlightModel(id=h.id, title=h.title, blurb=h.blurb, href=h.href)


def _entry(e: ChangelogEntry, stats: dict | None) -> ChangelogEntryModel:
    return ChangelogEntryModel(
        version=e.version,
        label=e.label,
        summary=e.summary,
        highlights=[_highlight(h) for h in e.highlights],
        stats=_stats(stats),
        sections=[ChangelogSectionModel(name=s.name, items=list(s.items)) for s in e.sections],
    )


def _stats(raw: dict | None) -> ReleaseStatsModel | None:
    """Validated figures, or ``None`` for a missing or malformed record."""
    try:
        return ReleaseStatsModel.model_validate(raw) if raw else None
    except ValidationError:
        return None
