"""Per-repo database registration for API-managed repositories.

A repository's canonical store is ``<repo>/.repowise/wiki.db`` — the same
file the CLI creates and a later ``repowise serve`` run from inside that repo
expects to find. Before this module, a repo registered via ``POST /api/repos``
while the server was pointed at some other database got all of its pipeline
output (pages, graph, jobs) written into that ambient database instead:
invisible to the CLI and polluting whatever DB the server happened to open.

The fix mirrors what workspace mode already does at startup: open a dedicated
engine/session factory on the repo's own ``wiki.db`` and register it in
``app_state.workspace_sessions`` so :func:`repowise.server.deps.resolve_session_factory`
routes every ``repo_id``-scoped request (and the job executor) to the right
file. The map predates non-workspace registration, hence its name; it is
simply "repo_id to its own database" and works identically in both modes.

The server's primary database keeps a lightweight registry row (same repo id)
so repos can be listed and re-registered across restarts.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.models import Repository
from repowise.core.persistence.search import FullTextSearch

logger = logging.getLogger(__name__)


def repo_db_file(local_path: str | Path) -> Path:
    """Return the repo-local database file path (may not exist yet)."""
    return Path(local_path).resolve() / ".repowise" / "wiki.db"


def is_primary_db_for(app_state, local_path: str | Path) -> bool:
    """True when the server's primary database *is* this repo's wiki.db.

    In that case no per-repo engine is needed — the ambient session factory
    already reads and writes the canonical file.
    """
    db_url: str = getattr(app_state, "db_url", "") or ""
    return repo_db_file(local_path).as_posix() in db_url.replace("\\", "/")


def _state_dict(app_state, name: str) -> dict:
    """Fetch-or-create a dict attribute on app.state (test apps skip lifespan)."""
    existing = getattr(app_state, name, None)
    if existing is None:
        existing = {}
        setattr(app_state, name, existing)
    return existing


def _state_list(app_state, name: str) -> list:
    existing = getattr(app_state, name, None)
    if existing is None:
        existing = []
        setattr(app_state, name, existing)
    return existing


async def ensure_repo_registration(
    app_state,
    *,
    local_path: str,
    name: str,
    url: str = "",
    default_branch: str = "main",
    settings: dict | None = None,
    repo_id: str | None = None,
):
    """Open (creating if needed) a repo's own wiki.db and register it for routing.

    Ensures the ``Repository`` row exists in the repo-local database — created
    with ``repo_id`` when given, so the primary-DB registry row and the
    canonical row share one id — and registers the session factory plus a
    per-repo FTS instance on ``app_state``. Idempotent: an already-registered
    repo returns its existing factory.

    Returns ``(session_factory, repo_id)`` for the canonical row.
    """
    resolved = str(Path(local_path).resolve())

    if is_primary_db_for(app_state, resolved):
        # Server is already serving this repo's own DB as its primary.
        async with get_session(app_state.session_factory) as session:
            rid = await _ensure_repo_row(
                session,
                name=name,
                local_path=resolved,
                url=url,
                default_branch=default_branch,
                settings=settings,
                repo_id=repo_id,
            )
        return app_state.session_factory, rid

    sessions = _state_dict(app_state, "workspace_sessions")
    fts_map = _state_dict(app_state, "workspace_fts")
    engines = _state_list(app_state, "workspace_engines")

    db_file = repo_db_file(resolved)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await init_db(engine)
    factory = create_session_factory(engine)

    async with get_session(factory) as session:
        rid = await _ensure_repo_row(
            session,
            name=name,
            local_path=resolved,
            url=url,
            default_branch=default_branch,
            settings=settings,
            repo_id=repo_id,
        )

    if rid in sessions:
        # Registered earlier in this process — keep the original engine and
        # drop the redundant one we just opened.
        await engine.dispose()
        return sessions[rid], rid

    sessions[rid] = factory
    engines.append(engine)
    try:
        repo_fts = FullTextSearch(engine)
        await repo_fts.ensure_index()
        fts_map[rid] = repo_fts
    except Exception:
        logger.debug("repo_fts_init_failed", extra={"repo_id": rid}, exc_info=True)

    logger.info("repo_db_registered", extra={"repo_id": rid, "db": str(db_file)})
    return factory, rid


async def _ensure_repo_row(
    session,
    *,
    name: str,
    local_path: str,
    url: str,
    default_branch: str,
    settings: dict | None,
    repo_id: str | None,
) -> str:
    """Create the canonical repo row if missing; never clobber an existing one.

    Startup rediscovery and job launches route through here, so an existing
    row's name/url/branch must survive untouched — only ``POST /api/repos``
    (which updates fields explicitly) may overwrite them.
    """
    from repowise.core.persistence.crud import get_repository_by_path

    repo = await get_repository_by_path(session, local_path)
    if repo is None:
        repo = await upsert_repository(
            session,
            name=name,
            local_path=local_path,
            url=url,
            default_branch=default_branch,
            settings=settings,
            repo_id=repo_id,
        )
    return repo.id


async def upsert_registry_row(
    session,
    *,
    repo_id: str,
    name: str,
    local_path: str,
    url: str = "",
    default_branch: str = "main",
    settings: dict | None = None,
) -> None:
    """Mirror the canonical repo row into the server's primary database.

    The registry row is what makes an externally-stored repo listable and,
    on the next server start, re-registerable. If a row for the same path
    exists under a different id (e.g. registered before the repo was ever
    indexed by the CLI), it is replaced so routing stays keyed by one id.
    """
    import json as _json

    result = await session.execute(select(Repository).where(Repository.local_path == local_path))
    existing = result.scalar_one_or_none()
    if existing is not None and existing.id != repo_id:
        await session.delete(existing)
        await session.flush()
        existing = None

    if existing is None:
        await upsert_repository(
            session,
            name=name,
            local_path=local_path,
            url=url,
            default_branch=default_branch,
            settings=settings,
            repo_id=repo_id,
        )
    else:
        existing.name = name
        existing.url = url
        existing.default_branch = default_branch
        if settings is not None:
            existing.settings_json = _json.dumps(settings)
        await session.flush()


async def rediscover_repo_dbs(app_state) -> int:
    """Re-register per-repo databases recorded in the primary DB's registry.

    Called at startup after workspace detection. For every repository row in
    the primary database whose ``local_path`` carries its own ``wiki.db``
    (and is not the primary DB itself, nor already registered by the
    workspace loop), open an engine and register it for routing.
    """
    import sqlite3

    count = 0
    sessions = _state_dict(app_state, "workspace_sessions")

    async with get_session(app_state.session_factory) as session:
        result = await session.execute(select(Repository.id, Repository.local_path))
        rows = result.all()

    for registry_id, local_path in rows:
        if not local_path or registry_id in sessions:
            continue
        db_file = repo_db_file(local_path)
        if not db_file.is_file() or is_primary_db_for(app_state, local_path):
            continue
        # Read the canonical id without opening a full async engine first.
        try:
            with sqlite3.connect(str(db_file)) as conn:
                row = conn.execute(
                    "SELECT id, name FROM repositories WHERE local_path = ? LIMIT 1",
                    (str(Path(local_path).resolve()),),
                ).fetchone()
                if row is None:
                    row = conn.execute("SELECT id, name FROM repositories LIMIT 1").fetchone()
        except Exception:
            continue
        canonical_id = row[0] if row else None
        if canonical_id and canonical_id != registry_id:
            logger.warning(
                "repo_db_id_mismatch",
                extra={"registry_id": registry_id, "canonical_id": canonical_id},
            )
        try:
            await ensure_repo_registration(
                app_state,
                local_path=local_path,
                name=(row[1] if row else None) or Path(local_path).name,
                repo_id=registry_id,
            )
            count += 1
        except Exception:
            logger.debug("repo_db_rediscovery_failed", extra={"path": local_path}, exc_info=True)
    return count
