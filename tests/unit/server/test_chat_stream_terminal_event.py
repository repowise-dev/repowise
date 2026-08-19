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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository
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
        response = await client.post(
            f"/api/repos/{_REPO_ID}/chat/messages", json=payload
        )
    assert response.status_code == 200, response.text
    return response.text


@pytest.mark.asyncio
async def test_unknown_conversation_ends_the_stream_with_a_typed_error():
    app = await _make_app()

    with patch(
        "repowise.server.routers.chat.get_chat_provider_instance",
        return_value=_SilentProvider(),
    ):
        body = await _post(
            app, {"message": "hi", "conversation_id": "no-such-conversation"}
        )

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
