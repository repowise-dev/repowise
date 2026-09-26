"""Server-startup wiring for a detected workspace.

When ``repowise serve`` runs inside a workspace, the HTTP lifespan uses the
primary member's database, opens (or maps) every member's store so the REST
API can route to it, publishes a ``RepoRegistry`` and the cross-repo enricher
to the chat tools, and fails jobs a restart interrupted in member databases.
Each step here is one of those; ``app.lifespan`` sequences them.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import update as sa_update

from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.models import GenerationJob
from repowise.core.persistence.search import FullTextSearch

# Startup events keep the app logger's name, so log filters written against
# ``repowise.server.app`` still see them.
logger = logging.getLogger("repowise.server.app")


async def fail_interrupted_jobs(session_factory: Any, message: str) -> int:
    """Mark every pending/running job in one database failed; return how many."""
    async with get_session(session_factory) as session:
        stale_result = await session.execute(
            sa_update(GenerationJob)
            .where(GenerationJob.status.in_(["running", "pending"]))
            .values(
                status="failed",
                error_message=message,
                finished_at=datetime.now(UTC),
            )
        )
        return stale_result.rowcount or 0


async def reset_workspace_stale_jobs(app_state) -> int:
    """Mark interrupted pending/running jobs failed across workspace repo DBs."""
    reset_count = 0
    for ws_factory in getattr(app_state, "workspace_sessions", {}).values():
        reset_count += await fail_interrupted_jobs(
            ws_factory, "Server restarted; job interrupted"
        )
    return reset_count


def workspace_primary_db_url() -> str | None:
    """The primary workspace member's ``wiki.db`` URL, when there is one on disk."""
    try:
        from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root

        _ws_root = find_workspace_root()
        if _ws_root is not None:
            _ws_cfg = WorkspaceConfig.load(_ws_root)
            _primary = _ws_cfg.get_primary()
            _primary_path = _ws_root / (_primary.path if _primary else ".")
            _primary_db = (_primary_path / ".repowise" / "wiki.db").resolve()
            if _primary_db.exists():
                logger.info("workspace_primary_db", extra={"db": str(_primary_db)})
                return f"sqlite+aiosqlite:///{_primary_db.as_posix()}"
    except Exception:
        pass  # Fall back to default
    return None


def init_workspace_state(app_state, vector_store, primary_vector_repo_id: str | None) -> None:
    """Workspace slots on app state, empty until a workspace is detected."""
    # Workspace detection — mirrors MCP _server.py:_detect_workspace()
    app_state.workspace_config = None
    app_state.workspace_root = None
    app_state.cross_repo_enricher = None
    app_state.repo_registry = None  # RepoRegistry, workspace mode only
    app_state.workspace_sessions = {}  # repo_id → session_factory
    app_state.workspace_path_to_repo_id = {}  # local_path → repo_id
    app_state.workspace_engines = []  # engines to dispose on shutdown
    # Per-repo FTS instances keyed by repo_id, used by the search router
    # to fan out across every workspace repo (single-repo FTS lives on
    # app.state.fts and stays as the primary).
    app_state.workspace_fts = {}  # repo_id → FullTextSearch
    # repo_id → vector store (LanceDB-backed) for per-repo semantic search.
    # Populated lazily by the search router on first use, then cached.
    app_state.workspace_vector_stores = {}  # repo_id → VectorStore
    if primary_vector_repo_id is not None:
        app_state.workspace_vector_stores[primary_vector_repo_id] = vector_store


async def _map_shared_db_members(app_state, ws_config, ws_root: Path, session_factory, fts) -> None:
    # Shared database mode (e.g. PostgreSQL, REPOWISE_DB_URL).
    # Member repos are registered in the shared database and do not
    # have per-repo .repowise/wiki.db files.
    from repowise.core.persistence.crud import get_repository_by_path

    async with get_session(session_factory) as session:
        for repo_entry in ws_config.repos:
            repo_path = (ws_root / repo_entry.path).resolve()
            try:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is not None:
                    app_state.workspace_sessions[repo.id] = session_factory
                    app_state.workspace_fts[repo.id] = fts
                    app_state.workspace_path_to_repo_id[str(repo_path)] = repo.id
            except Exception:
                logger.debug(
                    "workspace_shared_db_repo_lookup_failed",
                    extra={"path": str(repo_path)},
                    exc_info=True,
                )


def _read_repo_row(repo_db: Path) -> tuple | None:
    """The ``(id,)`` row a member's ``wiki.db`` holds, or None when there is none."""
    # Read repo_id from this DB
    try:
        conn = sqlite3.connect(str(repo_db))
        row = conn.execute("SELECT id FROM repositories LIMIT 1").fetchone()
        conn.close()
    except Exception:
        return None
    return row or None


async def _open_member_db(app_state, repo_id: str, repo_db: Path) -> None:
    repo_engine = create_engine(f"sqlite+aiosqlite:///{repo_db.as_posix()}")
    await init_db(repo_engine)
    repo_sf = create_session_factory(repo_engine)
    app_state.workspace_sessions[repo_id] = repo_sf
    app_state.workspace_engines.append(repo_engine)

    # Build a per-repo FTS instance so the search router can
    # fan out queries across every workspace repo. Without
    # this, full-text search only ever sees the primary DB.
    try:
        repo_fts = FullTextSearch(repo_engine)
        await repo_fts.ensure_index()
        app_state.workspace_fts[repo_id] = repo_fts
    except Exception:
        logger.debug(
            "workspace_fts_init_failed",
            extra={"repo_id": repo_id},
            exc_info=True,
        )


async def _open_member_dbs(app_state, ws_config, ws_root: Path, fts, db_url: str) -> None:
    # Create per-repo DB engines so all workspace repos are accessible
    # via the same REST API (sidebar, repo-specific pages, etc.)
    for repo_entry in ws_config.repos:
        repo_path = (ws_root / repo_entry.path).resolve()
        repo_db = repo_path / ".repowise" / "wiki.db"
        if not repo_db.exists():
            continue
        row = _read_repo_row(repo_db)
        if row is None:
            continue
        repo_id = row[0]
        app_state.workspace_path_to_repo_id[str(repo_path)] = repo_id

        # Skip if this is the primary DB we already connected to
        # (the main engine already serves this repo) — but still
        # register the primary's FTS under its repo_id so the
        # search fan-out can include it.
        if db_url and repo_db.as_posix() in db_url.replace("\\", "/"):
            app_state.workspace_fts[repo_id] = fts
            continue

        await _open_member_db(app_state, repo_id, repo_db)


def _publish_registry(
    app_state, ws_config, ws_root: Path, embedder_factory: Callable[[], Any]
) -> None:
    # Give the MCP tool functions a RepoRegistry, the same one the
    # stdio MCP lifespan builds (_server.py). Without it every chat
    # tool call falls into the single-repo branch of
    # _resolve_repo_context() and dies with "Repository not found:
    # <alias>", because the alias is looked up in the primary repo's
    # wiki.db only (issue #970). Contexts load lazily, so the repo
    # the first chat call names pays the engine/FTS open cost inline.
    # The registry holds its own handles on each repo's wiki.db,
    # separate from app.state.workspace_sessions above; collapsing
    # the two onto one set of connections is worth doing but is a
    # bigger change than this fix.
    from repowise.core.workspace.registry import RepoRegistry
    from repowise.server.chat_tools import set_tool_workspace

    repo_registry = RepoRegistry(
        workspace_root=ws_root,
        ws_config=ws_config,
        embedder_factory=embedder_factory,
    )
    app_state.repo_registry = repo_registry
    set_tool_workspace(registry=repo_registry, workspace_root=str(ws_root))


async def _reset_member_stale_jobs(app_state) -> None:
    # Reset stale jobs in non-primary workspace DBs too. The primary
    # DB was handled before workspace detection, but each secondary
    # repo has its own generation_jobs table and stale running rows
    # there would keep the UI showing an in-progress sync forever.
    try:
        reset_count = await reset_workspace_stale_jobs(app_state)
        if reset_count:
            logger.warning(
                "reset_workspace_stale_jobs",
                extra={"count": reset_count},
            )
    except Exception as exc:
        logger.warning("workspace_stale_job_reset_failed", extra={"error": str(exc)})


def _publish_cross_repo_enricher(app_state, ws_config, ws_root: Path) -> None:
    from repowise.core.workspace.breaking_change import BREAKING_CHANGES_FILENAME
    from repowise.core.workspace.config import WORKSPACE_DATA_DIR
    from repowise.core.workspace.conformance import CONFORMANCE_FILENAME
    from repowise.core.workspace.contracts import CONTRACTS_FILENAME
    from repowise.core.workspace.system_graph import SYSTEM_GRAPH_FILENAME
    from repowise.server.chat_tools import set_tool_workspace
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    data_dir = ws_root / WORKSPACE_DATA_DIR
    enricher = CrossRepoEnricher(
        data_dir / "cross_repo_edges.json",
        contracts_path=data_dir / CONTRACTS_FILENAME,
        system_graph_path=data_dir / SYSTEM_GRAPH_FILENAME,
        breaking_changes_path=data_dir / BREAKING_CHANGES_FILENAME,
        conformance_path=data_dir / CONFORMANCE_FILENAME,
    )
    if enricher.has_data or enricher.has_contract_data or enricher.has_system_graph:
        app_state.cross_repo_enricher = enricher
        set_tool_workspace(cross_repo_enricher=enricher)
        logger.info(
            "repowise_workspace_detected",
            extra={
                "repos": len(ws_config.repos),
                "co_changes": len(getattr(enricher, "_co_changes", [])),
                "contract_links": len(getattr(enricher, "_contract_links", [])),
            },
        )
    else:
        logger.info("repowise_workspace_detected", extra={"repos": len(ws_config.repos)})


async def attach_workspace(
    app_state,
    *,
    session_factory,
    fts,
    db_url: str,
    embedder_factory: Callable[[], Any],
) -> None:
    """Detect a workspace around the cwd and wire its members into app state.

    Outside a workspace this does nothing. Any failure leaves the server in
    single-repo mode rather than failing startup.
    """
    try:
        from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root

        ws_root = find_workspace_root()
        if ws_root is None:
            return
        ws_config = WorkspaceConfig.load(ws_root)
        app_state.workspace_config = ws_config
        app_state.workspace_root = str(ws_root)
        ws_root_path = Path(ws_root)

        from repowise.core.persistence.database import get_configured_db_url

        if get_configured_db_url() is not None:
            await _map_shared_db_members(app_state, ws_config, ws_root_path, session_factory, fts)
        else:
            await _open_member_dbs(app_state, ws_config, ws_root_path, fts, db_url)

        if app_state.workspace_sessions:
            logger.info(
                "workspace_repo_dbs_loaded",
                extra={"count": len(app_state.workspace_sessions)},
            )

        _publish_registry(app_state, ws_config, ws_root_path, embedder_factory)
        await _reset_member_stale_jobs(app_state)
        _publish_cross_repo_enricher(app_state, ws_config, ws_root_path)
    except Exception:
        logger.debug("Workspace detection skipped", exc_info=True)
