"""Regression test: chat endpoints honour ``workspace_sessions`` routing.

Bug (pre-fix): in workspace mode each repo has its own ``wiki.db``
registered under ``app.state.workspace_sessions[repo_id]``. The
chat endpoint resolved ``repo_id`` against the primary's session
factory (``app.state.session_factory``), which doesn't contain the
non-primary repos' rows. Result: every chat request to a
non-primary repo 404'd with ``Repository {repo_id} not found``,
even though the same id is listed in ``GET /api/repos`` and
``GET /api/workspace.repos[].repo_id``.

Fix: ``chat_messages`` now uses
:func:`repowise.server.deps.resolve_request_session_factory`,
which mirrors the routing logic that ``get_db_session`` (used by
the conversation endpoints) already encoded.

The test below builds a minimal FastAPI app with one primary repo
in the global session factory and a non-primary repo in
``workspace_sessions``, and asserts that POST /chat/messages on
the *non-primary* id passes the lookup (i.e. does not 404 on the
``Repository ... not found`` branch).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository
from repowise.server.routers import chat


_NOW = datetime(2026, 4, 12, 10, 0, 0, tzinfo=UTC)


async def _make_factory_with_repo(*, repo_id: str, name: str):
    """Build an in-memory async session factory containing one repo row."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add(
            Repository(
                id=repo_id,
                name=name,
                url=f"https://example.com/{name}",
                local_path=f"/workspace/{name}",
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.commit()
    return factory


def _build_app(*, primary_factory, workspace_sessions: dict) -> FastAPI:
    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    app = FastAPI(title="chat-workspace-test", lifespan=noop_lifespan)

    @app.exception_handler(LookupError)
    async def _lookup(_request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.state.session_factory = primary_factory
    app.state.workspace_sessions = workspace_sessions
    app.include_router(chat.router)
    return app


@pytest.mark.asyncio
async def test_chat_messages_resolves_non_primary_repo_in_workspace_mode():
    """Pin the workspace-routing fix: the non-primary id must NOT 404."""
    primary = await _make_factory_with_repo(repo_id="primary-id", name="primary")
    non_primary = await _make_factory_with_repo(
        repo_id="non-primary-id", name="non-primary"
    )

    app = _build_app(
        primary_factory=primary,
        workspace_sessions={"non-primary-id": non_primary},
    )

    # Stop after the chat handler has resolved the repo by replacing
    # the chat-provider factory; if the lookup 404s, this never runs.
    fake_provider = AsyncMock()
    fake_provider.provider_name = "openai"

    with (
        patch(
            "repowise.server.routers.chat.get_chat_provider_instance",
            return_value=fake_provider,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/repos/non-primary-id/chat/messages",
                json={"message": "hi"},
            )

    # Pre-fix this returned 404 "Repository non-primary-id not found".
    # Post-fix the lookup succeeds — the 422 here is the next branch
    # ("Provider does not support streaming chat") because our fake
    # provider isn't a ChatProvider. Either 200 (full happy path) or
    # 422 (provider check) proves the workspace routing worked.
    assert response.status_code != 404, response.text


@pytest.mark.asyncio
async def test_chat_messages_still_finds_primary_repo():
    """Single-factory fallback: when ``repo_id`` isn't in
    ``workspace_sessions``, the resolver falls back to
    ``app.state.session_factory`` — covers single-repo mode AND the
    primary repo of a workspace, both of which keep the row in the
    global factory."""
    primary = await _make_factory_with_repo(repo_id="primary-id", name="primary")
    app = _build_app(primary_factory=primary, workspace_sessions={})

    fake_provider = AsyncMock()
    fake_provider.provider_name = "openai"

    with patch(
        "repowise.server.routers.chat.get_chat_provider_instance",
        return_value=fake_provider,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/repos/primary-id/chat/messages",
                json={"message": "hi"},
            )

    assert response.status_code != 404, response.text
