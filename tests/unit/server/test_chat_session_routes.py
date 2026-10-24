"""The headless session routes: open or resume, look up by thread, send."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from repowise.server.auth import Principal, set_authenticator
from tests.unit.server.test_chat_router import _ScriptedProvider, _text, _tool
from tests.unit.server.test_chat_stream_terminal_event import _REPO_ID, _data_events, _make_app

_PROVIDER = "repowise.server.routers.chat.get_chat_provider_instance"
_EXECUTE = "repowise.server.chat_tools.execute_tool"


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _open(client: AsyncClient, **body) -> dict:
    response = await client.post("/api/chat/sessions", json={"repo_id": _REPO_ID, **body})
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_thread_key_resumes_the_same_session():
    app = await _make_app()
    async with await _client(app) as client:
        first = await _open(client, namespace="bot", external_ref="C1/171.2")
        again = await _open(client, namespace="bot", external_ref="C1/171.2")
        fresh = await _open(client)
        found = await client.get(
            "/api/chat/sessions", params={"namespace": "bot", "external_ref": "C1/171.2"}
        )
        missing = await client.get("/api/chat/sessions", params={"external_ref": "nope"})

    assert first["created"] and not again["created"] and fresh["created"]
    assert again["id"] == first["id"] != fresh["id"]
    assert first["scope"] == "repo"
    assert first["policy"]["safety_ceiling"] == "read_only"
    assert first["policy"]["max_tool_loops"] == 20
    assert [row["id"] for row in found.json()] == [first["id"]]
    assert missing.json() == []


async def test_a_policy_may_narrow_but_not_widen():
    app = await _make_app()
    async with await _client(app) as client:
        narrowed = await _open(client, policy={"max_tool_loops": 5, "tool_allowlist": ["get_risk"]})
        widened = await client.post(
            "/api/chat/sessions", json={"repo_id": _REPO_ID, "policy": {"max_tool_loops": 50}}
        )

    assert narrowed["policy"]["max_tool_loops"] == 5
    assert narrowed["policy"]["tool_allowlist"] == ["get_risk"]
    assert widened.status_code == 422


async def test_send_returns_the_turn_result_as_json_and_replays_a_retry():
    app = await _make_app()
    provider = _ScriptedProvider(
        [[_tool("t1", "get_risk", {"targets": ["src/a.py"]})], [_text("Risky.")]]
    )
    async with await _client(app) as client:
        session = await _open(client)
        with patch(_PROVIDER, return_value=provider), patch(
            _EXECUTE, return_value={"targets": {}, "citations": ["src/a.py"]}
        ):
            url = f"/api/chat/sessions/{session['id']}/messages"
            body = {"message": "Is a.py risky?", "idempotency_key": "evt-1"}
            first = await client.post(url, json=body)
            retry = await client.post(url, json=body)

    assert first.status_code == 200, first.text
    result = first.json()
    assert result["answer"] == "Risky."
    assert result["stop_reason"] == "answered"
    (citation,) = result["citations"]
    assert citation["file_path"] == "src/a.py" and len(citation["artifact_id"]) == 32
    assert citation["ref"]["type"] == "file" and citation["ref"]["id"] == "src/a.py"
    assert result["figures"] == [] and result["diagnostics"] == {}
    assert [t["name"] for t in result["tool_trace"]] == ["get_risk"]
    assert result["usage"]["model_calls"] == 2
    assert retry.json()["replayed"] is True
    assert retry.json()["message_id"] == result["message_id"]
    assert len(provider.calls) == 2


async def test_a_reused_key_with_another_message_is_a_conflict():
    app = await _make_app()
    provider = _ScriptedProvider(lambda _i: [_text("ok")])
    async with await _client(app) as client:
        session = await _open(client)
        url = f"/api/chat/sessions/{session['id']}/messages"
        with patch(_PROVIDER, return_value=provider):
            first = await client.post(url, json={"message": "one", "idempotency_key": "k"})
            json_conflict = await client.post(url, json={"message": "two", "idempotency_key": "k"})
            sse_conflict = await client.post(
                url, json={"message": "two", "idempotency_key": "k", "stream": True}
            )

    assert first.status_code == 200
    assert json_conflict.status_code == 409
    assert sse_conflict.status_code == 409
    assert len(provider.calls) == 1


async def test_send_can_stream_the_same_events_the_web_reads():
    app = await _make_app()
    provider = _ScriptedProvider([[_text("Hello.")]])
    async with await _client(app) as client:
        session = await _open(client)
        with patch(_PROVIDER, return_value=provider):
            response = await client.post(
                f"/api/chat/sessions/{session['id']}/messages",
                json={"message": "hi", "stream": True},
            )

    events = _data_events(response.text)
    assert [e["type"] for e in events] == ["text_delta", "done"]
    assert events[-1]["conversation_id"] == session["id"]


async def test_a_session_turn_never_offers_a_non_read_only_tool():
    app = await _make_app()
    provider = _ScriptedProvider([[_text("ok")]])
    async with await _client(app) as client:
        session = await _open(client)
        with patch(_PROVIDER, return_value=provider):
            await client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    assert "generate_refactoring_code" not in provider.calls[0]["system_prompt"]
    assert "set_finding_status" not in provider.calls[0]["system_prompt"]


async def test_an_unknown_session_is_not_found():
    app = await _make_app()
    async with await _client(app) as client:
        response = await client.post("/api/chat/sessions/nope/messages", json={"message": "hi"})

    assert response.status_code == 404


class _RepoScoped:
    authorization_servers = ()

    def configured(self) -> bool:
        return True

    async def verify_token(self, token):
        return None

    async def authenticate(self, request):
        if request.headers.get("authorization") != "Bearer scoped":
            raise HTTPException(401, "no")
        return Principal("token:t", "token", repo_scope=frozenset({"elsewhere"}))


@pytest.fixture
def repo_scoped_auth():
    set_authenticator(_RepoScoped())
    yield {"Authorization": "Bearer scoped"}
    set_authenticator(None)


async def test_a_principal_outside_the_repo_is_refused(repo_scoped_auth):
    app = await _make_app()
    async with await _client(app) as client:
        refused = await client.post(
            "/api/chat/sessions", json={"repo_id": _REPO_ID}, headers=repo_scoped_auth
        )

    assert refused.status_code == 403
