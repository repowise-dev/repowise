"""The chat SSE stream must always end with an event the client can read.

The web client parses ``data:`` lines only and switches on the payload's
``type`` field (``packages/web/src/lib/hooks/use-chat.ts``). ``done`` and
``error`` are the two values that stop the "assistant is typing" state.

Bug (pre-fix): the unknown-conversation branch emitted its failure on the
``error`` channel with a payload carrying only ``message`` and no ``type``.
The client dropped it, the generator returned, and the chat window sat on a
spinner with nothing on screen to explain why. Every other failure in the
handler already used ``data`` + ``type``; this one branch did not.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence import crud
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository
from repowise.server.chat_artifacts import normalize_message_artifacts
from repowise.server.routers import chat

_NOW = datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC)
_REPO_ID = "repo-1"


class _SilentProvider:
    """A ChatProvider that ends its turn without emitting anything.

    ``ChatProvider`` is a runtime-checkable Protocol, so implementing
    ``stream_chat`` is all the handler's isinstance check asks for. Yielding
    nothing drives the agentic loop straight to its "no pending tool calls"
    exit, which is the shortest path to the end of the stream.
    """

    provider_name = "test"
    model_name = "test-model"

    async def stream_chat(self, **_kwargs) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover - makes this an async generator


async def _make_app() -> FastAPI:
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
                id=_REPO_ID,
                name="repo",
                url="https://example.com/repo",
                local_path="/tmp/repo",
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.commit()

    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    app = FastAPI(title="chat-stream-test", lifespan=noop_lifespan)
    app.state.session_factory = factory
    app.state.workspace_sessions = {}
    app.include_router(chat.router)
    return app


def _data_events(body: str) -> list[dict]:
    """Parse the stream the way the web client does: ``data:`` lines only."""
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    return events


async def _post(app: FastAPI, payload: dict) -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(f"/api/repos/{_REPO_ID}/chat/messages", json=payload)
    assert response.status_code == 200, response.text
    return response.text


@pytest.mark.asyncio
async def test_unknown_conversation_ends_the_stream_with_a_typed_error():
    app = await _make_app()

    with patch(
        "repowise.server.routers.chat.get_chat_provider_instance",
        return_value=_SilentProvider(),
    ):
        body = await _post(app, {"message": "hi", "conversation_id": "no-such-conversation"})

    events = _data_events(body)
    assert events, f"stream carried no readable data events: {body!r}"
    assert events[-1]["type"] == "error"
    assert "not found" in events[-1]["message"].lower()


@pytest.mark.asyncio
async def test_a_completed_turn_ends_the_stream_with_done():
    app = await _make_app()

    with patch(
        "repowise.server.routers.chat.get_chat_provider_instance",
        return_value=_SilentProvider(),
    ):
        body = await _post(app, {"message": "hi"})

    events = _data_events(body)
    assert events, f"stream carried no readable data events: {body!r}"
    assert events[-1]["type"] == "done"
    assert events[-1]["conversation_id"]
    assert events[-1]["user_message_id"]
    assert events[-1]["provider"] == "test"
    assert events[-1]["model"] == "test-model"


@pytest.mark.asyncio
async def test_conversation_history_supports_rename_pin_fork_and_delete_undo():
    app = await _make_app()
    with patch(
        "repowise.server.routers.chat.get_chat_provider_instance",
        return_value=_SilentProvider(),
    ):
        events = _data_events(await _post(app, {"message": "original question"}))
    conversation_id = events[-1]["conversation_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        updated = await client.patch(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}",
            json={"title": "Pinned investigation", "pinned": True},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Pinned investigation"
        assert updated.json()["pinned"] is True

        messages = await client.get(f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}")
        user_message_id = messages.json()["messages"][0]["id"]
        forked_before = await client.post(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}/fork",
            json={"before_message_id": user_message_id},
        )
        assert forked_before.status_code == 200
        assert forked_before.json()["message_count"] == 0

        bad_point = await client.post(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}/fork",
            json={"before_message_id": "missing"},
        )
        assert bad_point.status_code == 404

        blank_title = await client.patch(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}",
            json={"title": "   "},
        )
        assert blank_title.status_code == 422

        forked = await client.post(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}/fork",
            json={},
        )
        assert forked.status_code == 200
        assert forked.json()["message_count"] == 2

        deleted = await client.delete(f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}")
        assert deleted.status_code == 200
        listed = await client.get(f"/api/repos/{_REPO_ID}/chat/conversations")
        assert all(row["id"] != conversation_id for row in listed.json())

        restored = await client.post(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation_id}/restore"
        )
        assert restored.status_code == 200
        listed = await client.get(f"/api/repos/{_REPO_ID}/chat/conversations")
        assert any(row["id"] == conversation_id for row in listed.json())


@pytest.mark.asyncio
async def test_artifact_lookup_pin_and_repository_isolation():
    app = await _make_app()
    async with app.state.session_factory() as session:
        conversation = await crud.create_conversation(
            session,
            repository_id=_REPO_ID,
            title="Artifacts",
        )
        message = await crud.create_chat_message(
            session,
            conversation_id=conversation.id,
            role="assistant",
            content={
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "name": "get_context",
                        "result": {"targets": {"src/a.py": {}}},
                        "artifact_type": "context",
                    }
                ]
            },
        )
        await session.commit()

    normalized = normalize_message_artifacts(
        json.loads(message.content_json),
        message_id=message.id,
    )
    artifact_id = normalized["tool_calls"][0]["artifact"]["id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        fetched = await client.get(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation.id}/artifacts/{artifact_id}"
        )
        assert fetched.status_code == 200
        assert fetched.json()["id"] == artifact_id

        isolated = await client.get(
            f"/api/repos/other-repo/chat/conversations/{conversation.id}/artifacts/{artifact_id}"
        )
        assert isolated.status_code == 404

        pinned = await client.patch(
            f"/api/repos/{_REPO_ID}/chat/conversations/{conversation.id}/artifacts/{artifact_id}",
            json={"pinned": True},
        )
        assert pinned.status_code == 200
        assert pinned.json()["pinned"] is True

        restored = await client.get(f"/api/repos/{_REPO_ID}/chat/conversations/{conversation.id}")
        call = restored.json()["messages"][0]["content"]["tool_calls"][0]
        assert call["artifact"]["pinned"] is True
        assert "result" not in call


def test_conversation_refinement_migration_upgrades_sqlite() -> None:
    core_root = Path("packages/core").resolve()
    with tempfile.TemporaryDirectory() as tmp:
        database_path = Path(tmp) / "chat-migration.db"
        url = f"sqlite+aiosqlite:///{database_path}"
        previous_url = os.environ.get("DATABASE_URL")
        previous_cwd = Path.cwd()
        os.environ["DATABASE_URL"] = url
        try:
            os.chdir(core_root)
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", url)
            with patch("logging.config.fileConfig"):
                command.upgrade(config, "0055")
                command.upgrade(config, "0056")
        finally:
            os.chdir(previous_cwd)
            if previous_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_url

        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        async def verify() -> None:
            async with engine.connect() as connection:
                columns = await connection.run_sync(
                    lambda sync_connection: {
                        column["name"]
                        for column in inspect(sync_connection).get_columns("conversations")
                    }
                )
                assert {"pinned", "deleted_at"} <= columns
            await engine.dispose()

        import asyncio

        asyncio.run(verify())
