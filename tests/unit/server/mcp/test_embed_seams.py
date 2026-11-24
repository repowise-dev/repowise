"""``mcp_server.embed`` is the public seam a host embeds the tools through."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.server import mcp_server
from repowise.server.mcp_server import _state, embed
from repowise.server.mcp_server._savings import wrapper


def test_public_names_are_the_engine_objects():
    assert embed.tool_middleware is mcp_server.tool_middleware
    assert embed.serving is _state.serving
    assert embed.request_context is _state.request_context
    assert set(embed.__all__) == {"tool_middleware", "serving", "request_context"}


def test_serving_sets_and_restores_the_request_context():
    ctx = SimpleNamespace(path=None, git=False)
    assert embed.request_context() is None
    with embed.serving(ctx, telemetry=False) as served:
        assert served is ctx
        assert embed.request_context() is ctx
        assert _state.telemetry_allowed() is False
    assert embed.request_context() is None
    assert _state.telemetry_allowed() is True


async def _calls_inside(monkeypatch: pytest.MonkeyPatch, **serving_kwargs) -> list[str]:
    from repowise.core.platform import telemetry

    events: list[str] = []
    monkeypatch.setattr(telemetry, "record_event", lambda event, props: events.append(event))

    async def get_overview() -> dict:
        return {"_meta": {}}

    with embed.serving(SimpleNamespace(path=None, git=False), **serving_kwargs):
        await wrapper.instrument(get_overview)()
    return events


@pytest.mark.asyncio
async def test_serving_records_telemetry_by_default(monkeypatch: pytest.MonkeyPatch):
    assert await _calls_inside(monkeypatch) == ["mcp_tool_call"]


@pytest.mark.asyncio
async def test_serving_without_telemetry_records_no_event(monkeypatch: pytest.MonkeyPatch):
    assert await _calls_inside(monkeypatch, telemetry=False) == []


def test_nested_serving_inherits_the_outer_opt_out_unless_it_sets_its_own():
    ctx = SimpleNamespace(path=None, git=False)
    with embed.serving(ctx, telemetry=False):
        with embed.serving(ctx):
            assert _state.telemetry_allowed() is False
        with embed.serving(ctx, telemetry=True):
            assert _state.telemetry_allowed() is True
        assert _state.telemetry_allowed() is False
    with embed.serving(ctx):
        assert _state.telemetry_allowed() is True
