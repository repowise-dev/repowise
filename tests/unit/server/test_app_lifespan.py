"""Startup and shutdown of the HTTP server, step by step.

Real SQLite files, with only the scheduler and primary vector store stubbed.
Failures come through data or a step's one collaborator, not its location.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import repowise.server.mcp_server as mcp_mod
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GenerationJob, Repository
from repowise.core.persistence.search import FullTextSearch
from repowise.core.workspace.config import WORKSPACE_DATA_DIR
from repowise.core.workspace.system_graph import SYSTEM_GRAPH_FILENAME
from repowise.server.app import create_app, lifespan

_NOW = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)
_PRIMARY_RESET = "Server restarted — job interrupted"
_MEMBER_RESET = "Server restarted; job interrupted"


@pytest.fixture(autouse=True)
def restore_tool_globals():
    """The real lifespan writes process-global MCP tool state; put it back."""
    saved = (
        mcp_mod._registry,
        mcp_mod._workspace_root,
        mcp_mod._cross_repo_enricher,
        mcp_mod._session_factory,
        mcp_mod._fts,
        mcp_mod._vector_store,
    )
    yield
    (
        mcp_mod._registry,
        mcp_mod._workspace_root,
        mcp_mod._cross_repo_enricher,
        mcp_mod._session_factory,
        mcp_mod._fts,
        mcp_mod._vector_store,
    ) = saved


@pytest.fixture(autouse=True)
def live_server_loggers(monkeypatch):
    """Alembic's ``fileConfig`` elsewhere in the suite disables existing loggers."""
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if name.startswith("repowise") and isinstance(logger, logging.Logger):
            monkeypatch.setattr(logger, "disabled", False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """No configured DB, no embedder, a private home, and cwd inside tmp_path."""
    for key in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL", "REPOWISE_EMBEDDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def stubs():
    """The scheduler and the primary vector store, the two heavy collaborators."""
    scheduler = MagicMock()
    vector_store = SimpleNamespace(close=AsyncMock())
    with (
        patch("repowise.server.app.setup_scheduler", return_value=scheduler) as setup,
        patch(
            "repowise.server.search_helpers.build_primary_vector_store",
            new=AsyncMock(return_value=(vector_store, None)),
        ) as build_store,
    ):
        yield SimpleNamespace(
            scheduler=scheduler,
            setup=setup,
            vector_store=vector_store,
            build_store=build_store,
        )


async def _make_db(
    db: Path, repo_id: str, local_path: Path, job_statuses: tuple[str, ...] = ()
) -> None:
    """A wiki.db holding one repository row and the given generation jobs."""
    db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add(
            Repository(
                id=repo_id,
                name=local_path.name,
                url="",
                local_path=str(local_path.resolve()),
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        for i, status in enumerate(job_statuses):
            session.add(
                GenerationJob(id=f"{repo_id}-job{i}", repository_id=repo_id, status=status)
            )
        await session.commit()
    await engine.dispose()


def _jobs(db: Path) -> list[tuple[str, str | None]]:
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(
            "SELECT status, error_message FROM generation_jobs ORDER BY id"
        ).fetchall()


def _refuse_job_updates(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TRIGGER no_job_updates BEFORE UPDATE ON generation_jobs "
            "BEGIN SELECT RAISE(ABORT, 'read only'); END;"
        )


def _write_workspace(root: Path, members: list[tuple[str, bool]]) -> None:
    lines = ["version: 1", f"default_repo: {members[0][0]}", "repos:"]
    for alias, primary in members:
        lines.append(f"- path: {alias}")
        lines.append(f"  alias: {alias}")
        if primary:
            lines.append("  is_primary: true")
    (root / ".repowise-workspace.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Single-repo mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_repo_startup_publishes_state_and_resets_jobs(env, stubs, monkeypatch):
    db = env / "primary.db"
    await _make_db(db, "p-id", env / "p", ("completed", "pending", "running"))
    db_url = f"sqlite+aiosqlite:///{db.as_posix()}"
    monkeypatch.setenv("REPOWISE_DB_URL", db_url)

    app = FastAPI()
    async with lifespan(app):
        state = app.state
        assert state.db_url == db_url
        assert state.session_factory is not None
        assert isinstance(state.fts, FullTextSearch)
        assert state.vector_store is stubs.vector_store
        assert state.primary_vector_repo_id is None
        assert state.background_tasks == set()
        assert state.job_tasks == {}
        assert state.job_cancel_tokens == {}
        assert state.job_events == {}
        assert state.scheduler is stubs.scheduler
        stubs.setup.assert_called_once_with(state.session_factory, app_state=state)
        stubs.scheduler.start.assert_called_once_with()
        # Workspace slots exist and are empty outside a workspace.
        assert state.workspace_config is None
        assert state.workspace_root is None
        assert state.cross_repo_enricher is None
        assert state.repo_registry is None
        assert state.workspace_sessions == {}
        assert state.workspace_path_to_repo_id == {}
        assert state.workspace_engines == []
        assert state.workspace_fts == {}
        assert state.workspace_vector_stores == {}
        # The chat tools see the same stores.
        assert mcp_mod._session_factory is state.session_factory
        assert mcp_mod._fts is state.fts
        assert mcp_mod._vector_store is stubs.vector_store
        # Interrupted jobs are failed before the first request.
        assert _jobs(db) == [
            ("completed", None),
            ("failed", _PRIMARY_RESET),
            ("failed", _PRIMARY_RESET),
        ]
        stubs.scheduler.shutdown.assert_not_called()

    stubs.scheduler.shutdown.assert_called_once_with(wait=False)
    stubs.vector_store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_primary_vector_store_is_cached_under_its_repo(env, stubs, monkeypatch):
    db = env / "primary.db"
    await _make_db(db, "p-id", env / "p")
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    stubs.build_store.return_value = (stubs.vector_store, "p-id")

    app = FastAPI()
    async with lifespan(app):
        assert app.state.primary_vector_repo_id == "p-id"
        assert app.state.workspace_vector_stores == {"p-id": stubs.vector_store}


@pytest.mark.asyncio
async def test_optional_steps_fail_without_stopping_startup(env, stubs, monkeypatch, caplog):
    db = env / "primary.db"
    await _make_db(db, "p-id", env / "p", ("running",))
    _refuse_job_updates(db)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    stubs.vector_store.close.side_effect = RuntimeError("already closed")

    app = FastAPI()
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(
            FullTextSearch, "ensure_index", new=AsyncMock(side_effect=RuntimeError("fts5"))
        ),
        patch(
            "repowise.server.search_helpers.close_workspace_vector_stores",
            new=AsyncMock(side_effect=RuntimeError("lance")),
        ),
    ):
        async with lifespan(app):
            assert isinstance(app.state.fts, FullTextSearch)
            assert _jobs(db) == [("running", None)]

    events = {r.getMessage() for r in caplog.records}
    assert {"stale_job_reset_failed", "fts_ensure_index_failed"} <= events
    assert "workspace_vector_store_close_failed" in events
    assert "repowise_server_stopped" in events


@pytest.mark.asyncio
async def test_shutdown_runs_when_the_app_body_raises(env, stubs, monkeypatch):
    db = env / "primary.db"
    await _make_db(db, "p-id", env / "p")
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")

    app = FastAPI()
    with pytest.raises(RuntimeError, match="request crashed"):
        async with lifespan(app):
            raise RuntimeError("request crashed")

    stubs.scheduler.shutdown.assert_called_once_with(wait=False)
    stubs.vector_store.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Workspace mode, repo-local databases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_startup_opens_member_databases(env, stubs, caplog):
    _write_workspace(
        env,
        [("boot", True), ("api", False), ("nodb", False), ("empty", False), ("junk", False)],
    )
    boot_db = env / "boot" / ".repowise" / "wiki.db"
    api_db = env / "api" / ".repowise" / "wiki.db"
    await _make_db(boot_db, "boot-id", env / "boot", ("running",))
    await _make_db(api_db, "api-id", env / "api", ("pending", "completed"))
    (env / "nodb").mkdir()
    # A member whose wiki.db has no repository row, and one that is not SQLite.
    empty_db = env / "empty" / ".repowise" / "wiki.db"
    empty_db.parent.mkdir(parents=True)
    with sqlite3.connect(str(empty_db)) as conn:
        conn.execute("CREATE TABLE repositories (id TEXT)")
    junk_db = env / "junk" / ".repowise" / "wiki.db"
    junk_db.parent.mkdir(parents=True)
    junk_db.write_bytes(b"not a database at all")
    (env / WORKSPACE_DATA_DIR).mkdir()
    (env / WORKSPACE_DATA_DIR / SYSTEM_GRAPH_FILENAME).write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )

    app = FastAPI()
    with caplog.at_level(logging.INFO):
        async with lifespan(app):
            state = app.state
            # The primary member's wiki.db becomes the server database.
            assert state.db_url == f"sqlite+aiosqlite:///{boot_db.resolve().as_posix()}"
            assert state.workspace_config is not None
            assert state.workspace_root == str(env.resolve())
            # The primary is served by the main engine: FTS registered, no session.
            assert set(state.workspace_sessions) == {"api-id"}
            assert state.workspace_fts["boot-id"] is state.fts
            assert isinstance(state.workspace_fts["api-id"], FullTextSearch)
            assert state.workspace_fts["api-id"] is not state.fts
            assert len(state.workspace_engines) == 1
            assert state.workspace_path_to_repo_id == {
                str((env / "boot").resolve()): "boot-id",
                str((env / "api").resolve()): "api-id",
            }
            assert state.repo_registry is not None
            assert mcp_mod._registry is state.repo_registry
            assert mcp_mod._workspace_root == str(env.resolve())
            # A system graph alone is enough to publish the enricher.
            assert state.cross_repo_enricher is not None
            assert mcp_mod._cross_repo_enricher is state.cross_repo_enricher
            assert _jobs(boot_db) == [("failed", _PRIMARY_RESET)]
            assert _jobs(api_db) == [("failed", _MEMBER_RESET), ("completed", None)]

    events = [r.getMessage() for r in caplog.records]
    assert "workspace_primary_db" in events
    assert "workspace_repo_dbs_loaded" in events
    assert "repowise_workspace_detected" in events
    # Shutdown unpublishes the workspace from the tool layer.
    assert mcp_mod._registry is None
    assert mcp_mod._workspace_root is None
    assert mcp_mod._cross_repo_enricher is None


@pytest.mark.asyncio
async def test_workspace_without_cross_repo_artifacts_publishes_no_enricher(env, stubs):
    _write_workspace(env, [("boot", True)])
    await _make_db(env / "boot" / ".repowise" / "wiki.db", "boot-id", env / "boot")

    app = FastAPI()
    async with lifespan(app):
        assert app.state.workspace_config is not None
        assert app.state.cross_repo_enricher is None
        assert app.state.workspace_sessions == {}


@pytest.mark.asyncio
async def test_member_job_reset_and_fts_failures_are_contained(env, stubs, caplog):
    _write_workspace(env, [("boot", True), ("api", False)])
    await _make_db(env / "boot" / ".repowise" / "wiki.db", "boot-id", env / "boot")
    api_db = env / "api" / ".repowise" / "wiki.db"
    await _make_db(api_db, "api-id", env / "api", ("running",))
    _refuse_job_updates(api_db)

    real_ensure = FullTextSearch.ensure_index
    primary_fts: list[FullTextSearch] = []

    async def member_fts_fails(self):
        if not primary_fts:
            primary_fts.append(self)
            return await real_ensure(self)
        raise RuntimeError("member fts")

    app = FastAPI()
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(FullTextSearch, "ensure_index", new=member_fts_fails),
    ):
        async with lifespan(app):
            # The member's engine still routes; only its keyword index is absent.
            assert "api-id" in app.state.workspace_sessions
            assert "api-id" not in app.state.workspace_fts
            assert _jobs(api_db) == [("running", None)]

    events = {r.getMessage() for r in caplog.records}
    assert {"workspace_fts_init_failed", "workspace_stale_job_reset_failed"} <= events


@pytest.mark.asyncio
async def test_malformed_workspace_file_falls_back_to_single_repo(env, stubs, caplog, monkeypatch):
    (env / ".repowise-workspace.yaml").write_text("repos: [unclosed\n", encoding="utf-8")
    db = env / "primary.db"
    await _make_db(db, "p-id", env / "p")
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")

    app = FastAPI()
    with caplog.at_level(logging.DEBUG):
        async with lifespan(app):
            assert app.state.workspace_config is None
            assert app.state.repo_registry is None
            assert app.state.workspace_sessions == {}

    assert "Workspace detection skipped" in {r.getMessage() for r in caplog.records}


@pytest.mark.asyncio
async def test_malformed_workspace_file_keeps_the_default_database(env, stubs):
    (env / ".repowise-workspace.yaml").write_text("repos: [unclosed\n", encoding="utf-8")

    app = FastAPI()
    async with lifespan(app):
        expected = (env / "home" / ".repowise" / "wiki.db").as_posix()
        assert app.state.db_url == f"sqlite+aiosqlite:///{expected}"


# ---------------------------------------------------------------------------
# Workspace mode, one shared database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_database_registers_members_and_skips_failed_lookups(
    env, stubs, monkeypatch, caplog
):
    _write_workspace(env, [("a", True), ("b", False), ("c", False)])
    for alias in ("a", "b", "c"):
        (env / alias).mkdir()
    db = env / "shared.db"
    await _make_db(db, "a-id", env / "a")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add(
            Repository(
                id="c-id",
                name="c",
                url="",
                local_path=str((env / "c").resolve()),
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.commit()
    await engine.dispose()
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")

    from repowise.core.persistence import crud

    real_lookup = crud.get_repository_by_path

    async def flaky_lookup(session, path):
        if path.endswith("c"):
            raise RuntimeError("lookup failed")
        return await real_lookup(session, path)

    app = FastAPI()
    with (
        caplog.at_level(logging.DEBUG),
        patch("repowise.core.persistence.crud.get_repository_by_path", new=flaky_lookup),
    ):
        async with lifespan(app):
            state = app.state
            assert state.workspace_sessions == {"a-id": state.session_factory}
            assert state.workspace_fts == {"a-id": state.fts}
            assert state.workspace_path_to_repo_id == {str((env / "a").resolve()): "a-id"}
            assert state.workspace_engines == []

    assert "workspace_shared_db_repo_lookup_failed" in {r.getMessage() for r in caplog.records}


# ---------------------------------------------------------------------------
# API-registered repositories outside any workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_repo_databases_are_rediscovered_and_reset(env, stubs, caplog):
    _write_workspace(env, [("boot", True)])
    boot_db = env / "boot" / ".repowise" / "wiki.db"
    await _make_db(boot_db, "boot-id", env / "boot")
    added = env / "added"
    added_db = added / ".repowise" / "wiki.db"
    await _make_db(added_db, "added-id", added, ("pending",))
    # The primary database's registry row for the repo added over the API.
    engine = create_async_engine(f"sqlite+aiosqlite:///{boot_db.as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add(
            Repository(
                id="added-id",
                name="added",
                url="",
                local_path=str(added.resolve()),
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.commit()
    await engine.dispose()

    app = FastAPI()
    with caplog.at_level(logging.INFO):
        async with lifespan(app):
            assert "added-id" in app.state.workspace_sessions
            assert _jobs(added_db) == [("failed", _MEMBER_RESET)]

    events = [r.getMessage() for r in caplog.records]
    assert "repo_dbs_rediscovered" in events
    assert "reset_stale_jobs" in events


@pytest.mark.asyncio
async def test_rediscovery_failure_is_contained(env, stubs, monkeypatch, caplog):
    db = env / "primary.db"
    await _make_db(db, "p-id", env / "p")
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")

    app = FastAPI()
    with (
        caplog.at_level(logging.DEBUG),
        patch(
            "repowise.server.repo_db.rediscover_repo_dbs",
            new=AsyncMock(side_effect=RuntimeError("registry unreadable")),
        ),
    ):
        async with lifespan(app):
            pass

    assert "repo_db_rediscovery_skipped" in {r.getMessage() for r in caplog.records}


@pytest.mark.asyncio
async def test_shutdown_closes_the_registry_and_member_engines(env, stubs):
    _write_workspace(env, [("boot", True), ("api", False)])
    await _make_db(env / "boot" / ".repowise" / "wiki.db", "boot-id", env / "boot")
    await _make_db(env / "api" / ".repowise" / "wiki.db", "api-id", env / "api")

    from sqlalchemy.ext.asyncio import AsyncEngine

    disposed: list[AsyncEngine] = []
    real_dispose = AsyncEngine.dispose

    async def recording_dispose(self, close: bool = True) -> None:
        disposed.append(self)
        await real_dispose(self, close)

    app = FastAPI()
    with (
        patch(
            "repowise.server.mcp_server._test_impact.close_test_impact_indexes", new=AsyncMock()
        ) as close_impact,
        patch.object(AsyncEngine, "dispose", new=recording_dispose),
    ):
        async with lifespan(app):
            registry = app.state.repo_registry
            registry.close = AsyncMock(side_effect=RuntimeError("already closed"))
            member_engine = app.state.workspace_engines[0]
            primary_engine = app.state.engine
            disposed.clear()

    registry.close.assert_awaited_once()
    close_impact.assert_awaited_once()
    # Member engines go first; the primary engine is the last thing released.
    assert disposed[-2:] == [member_engine, primary_engine]


# ---------------------------------------------------------------------------
# _build_embedder
# ---------------------------------------------------------------------------


@pytest.fixture
def embedder_env(monkeypatch):
    for key in ("REPOWISE_EMBEDDER", "REPOWISE_EMBEDDING_MODEL", "REPOWISE_EMBEDDING_DIMS"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    ("backend", "target", "model_env", "expected_kwargs"),
    [
        ("ollama", "ollama.OllamaEmbedder", None, {}),
        ("OpenAI", "openai.OpenAIEmbedder", None, {"model": "text-embedding-3-small"}),
        ("openai", "openai.OpenAIEmbedder", "text-embedding-3-large", {"model": "text-embedding-3-large"}),
        (
            "openrouter",
            "openrouter.OpenRouterEmbedder",
            None,
            {"model": "google/gemini-embedding-001"},
        ),
        (
            "edenai",
            "edenai.EdenAIEmbedder",
            None,
            {"model": "amazon/amazon.titan-embed-text-v2:0"},
        ),
        ("gemini", "gemini.GeminiEmbedder", None, {"output_dimensionality": 768}),
        (
            "gemini",
            "gemini.GeminiEmbedder",
            "text-embedding-004",
            {"model": "text-embedding-004", "output_dimensionality": 768},
        ),
    ],
)
def test_build_embedder_backends(embedder_env, backend, target, model_env, expected_kwargs):
    from repowise.server.app import _build_embedder

    embedder_env.setenv("REPOWISE_EMBEDDER", backend)
    if model_env:
        embedder_env.setenv("REPOWISE_EMBEDDING_MODEL", model_env)
    with patch(f"repowise.core.providers.embedding.{target}") as cls:
        assert _build_embedder() is cls.return_value
    cls.assert_called_once_with(**expected_kwargs)


@pytest.mark.parametrize(
    ("raw", "dims", "warned"),
    [("1536", 1536, False), ("0", 768, True), ("-3", 768, True), ("wide", 768, True), ("", 768, False)],
)
def test_build_embedder_gemini_dims(embedder_env, capsys, raw, dims, warned):
    from repowise.server.app import _build_embedder

    embedder_env.setenv("REPOWISE_EMBEDDER", "gemini")
    embedder_env.setenv("REPOWISE_EMBEDDING_DIMS", raw)
    with patch("repowise.core.providers.embedding.gemini.GeminiEmbedder") as cls:
        _build_embedder()
    cls.assert_called_once_with(output_dimensionality=dims)
    err = capsys.readouterr().err
    if warned:
        assert err == f"REPOWISE_EMBEDDING_DIMS={raw!r} is not a positive integer; using 768.\n"
    else:
        assert err == ""


def test_build_embedder_defaults_to_keyless_with_a_warning(embedder_env, caplog):
    from repowise.core.providers.embedding.base import KeylessEmbedder
    from repowise.server.app import _build_embedder

    with caplog.at_level(logging.WARNING):
        assert isinstance(_build_embedder(), KeylessEmbedder)
    assert any(r.getMessage().startswith("embedder.mock_active") for r in caplog.records)


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def _cors(app: FastAPI) -> dict:
    (mw,) = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    return mw.kwargs


def test_create_app_cors_defaults_to_any_origin_without_credentials(monkeypatch, caplog):
    monkeypatch.delenv("REPOWISE_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("REPOWISE_CORS_ALLOW_CREDENTIALS", "true")
    with caplog.at_level(logging.WARNING):
        app = create_app()
    assert _cors(app) == {
        "allow_origins": ["*"],
        "allow_credentials": False,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    assert any("cors.wildcard_with_credentials_rejected" in r.getMessage() for r in caplog.records)


def test_create_app_cors_explicit_origins_allow_credentials(monkeypatch, caplog):
    monkeypatch.setenv("REPOWISE_CORS_ORIGINS", " https://a.example , ,https://b.example")
    with caplog.at_level(logging.WARNING):
        app = create_app()
    assert _cors(app)["allow_origins"] == ["https://a.example", "https://b.example"]
    assert _cors(app)["allow_credentials"] is True
    assert not any("cors." in r.getMessage() for r in caplog.records)


def test_create_app_metadata_and_routes(monkeypatch):
    from repowise.server import __version__

    monkeypatch.delenv("REPOWISE_CORS_ORIGINS", raising=False)
    app = create_app()
    assert app.title == "repowise API"
    assert app.version == __version__
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/repos", "/api/repos/summary", "/health"} <= paths


@pytest.mark.asyncio
async def test_create_app_maps_lookup_and_value_errors(monkeypatch):
    monkeypatch.delenv("REPOWISE_CORS_ORIGINS", raising=False)
    app = create_app()

    @app.get("/_raise/{kind}")
    async def _raise(kind: str):
        raise (LookupError if kind == "lookup" else ValueError)(f"{kind} failed")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/_raise/lookup")
        invalid = await client.get("/_raise/value")

    assert (missing.status_code, missing.json()) == (404, {"detail": "lookup failed"})
    assert (invalid.status_code, invalid.json()) == (400, {"detail": "value failed"})
