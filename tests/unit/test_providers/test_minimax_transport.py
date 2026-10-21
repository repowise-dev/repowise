"""Exercise the registered provider with the real SDK and an offline HTTP transport."""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.minimax import MiniMaxProvider
from repowise.core.providers.llm.registry import get_provider


@pytest.fixture
def captured_requests(monkeypatch):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        if payload.get("stream"):
            chunk = {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": payload["model"],
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    def client(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr("repowise.core.providers.llm.minimax.AsyncOpenAI", client)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-only")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    return requests


@pytest.mark.parametrize(
    "endpoint", [None, "https://api.minimaxi.com/v1", "https://custom.invalid/v1"]
)
@pytest.mark.parametrize(
    "model", [None, "minimax-m3.1-flash", "MiniMax-M3", "MiniMax-M2.7", "saved-custom"]
)
async def test_registered_provider_serializes_default_and_saved_models(
    captured_requests, monkeypatch, endpoint, model
):
    if endpoint:
        monkeypatch.setenv("MINIMAX_BASE_URL", endpoint)
    kwargs = {"model": model} if model else {}
    provider = get_provider("minimax", **kwargs)
    try:
        result = await provider.generate("system", "user")
    finally:
        await provider._client.close()
    request, payload = captured_requests[0]
    assert str(request.url) == f"{endpoint or 'https://api.minimax.io/v1'}/chat/completions"
    assert payload["model"] == (model or "MiniMax-M3.1-Flash")
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert result.content == "ok"
    assert not {"thinking", "reasoning_effort", "service_tier", "reasoning_split"} & payload.keys()


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
async def test_flash_effort_reaches_http_payload(captured_requests, effort):
    provider = get_provider("minimax")
    try:
        await provider.generate("system", "user", reasoning=effort)
    finally:
        await provider._client.close()
    payload = captured_requests[0][1]
    assert payload["reasoning_effort"] == effort
    assert "thinking" not in payload


@pytest.mark.parametrize("reasoning", ["off", "none", "minimal"])
async def test_flash_rejects_unsupported_modes_before_http(captured_requests, reasoning):
    provider = get_provider("minimax")
    try:
        with pytest.raises(ProviderError):
            await provider.generate("system", "user", reasoning=reasoning)
    finally:
        await provider._client.close()
    assert captured_requests == []


@pytest.mark.parametrize("reasoning", ["off", "none"])
async def test_saved_m3_can_disable_thinking(captured_requests, reasoning):
    provider = get_provider("minimax", model="minimax-m3")
    try:
        await provider.generate("system", "user", reasoning=reasoning)
    finally:
        await provider._client.close()
    assert captured_requests[0][1]["thinking"] == {"type": "disabled"}


async def test_stream_preserves_tools_reasoning_history_and_media(captured_requests):
    messages = [
        {
            "role": "assistant",
            "content": "<think>prior thought</think>",
            "reasoning_content": "prior reasoning",
            "tool_calls": [
                {
                    "id": "call-test",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-test", "content": "result"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Summarize"},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
                {"type": "video_url", "video_url": {"url": "mm_file://fixture"}},
            ],
        },
    ]
    tools = [
        {
            "type": "function",
            "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}},
        }
    ]
    provider = get_provider("minimax")
    try:
        events = [event async for event in provider.stream_chat(messages, tools, "system")]
    finally:
        await provider._client.close()
    payload = captured_requests[0][1]
    assert payload["model"] == "MiniMax-M3.1-Flash"
    assert payload["messages"][1:] == messages
    assert payload["tools"] == tools
    assert payload["stream"] is True
    assert events[0].text == "ok"


@pytest.mark.parametrize("saved", ["MiniMax-M3.1-Flash", "MiniMax-M2.7", "custom-model"])
@pytest.mark.parametrize("discovery", ["success", "failure", "malformed"])
def test_catalog_filters_legacy_without_resetting_saved_model(monkeypatch, saved, discovery):
    def get(*args, **kwargs):
        if discovery == "failure":
            raise httpx.ConnectError("offline")
        data = [
            {"id": "MiniMax-M2.7"},
            {"id": "MiniMax-M3"},
            {"id": "MiniMax-M3.1-Flash"},
            {"id": "unrelated-model"},
        ]
        return httpx.Response(
            200,
            request=httpx.Request("GET", "https://example.invalid/models"),
            json={"data": data if discovery == "success" else None},
        )

    monkeypatch.setattr("httpx.get", get)
    provider = MiniMaxProvider(api_key="test-only", model=saved)
    options = provider.available_model_options()
    expected = ["MiniMax-M3.1-Flash", "MiniMax-M3"]
    if saved not in expected:
        expected.append(saved)
    assert [option.model for option in options] == expected
    assert [option.model for option in options if option.recommended] == ["MiniMax-M3.1-Flash"]
    assert provider.model_name == saved


def test_gateway_catalogs_filter_legacy_and_keep_saved_choices(monkeypatch):
    from repowise.core.providers.llm import opencode, openrouter

    models = [
        "minimax/minimax-m2.7",
        "minimax/minimax-m3",
        "minimax/minimax-m3.1-flash:free",
        "other/model",
    ]
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **kw: httpx.Response(
            200,
            request=httpx.Request("GET", "https://example.invalid/models"),
            json={
                "data": [{"id": model, "supported_parameters": ["reasoning"]} for model in models]
            },
        ),
    )
    options = openrouter._openrouter_model_options(
        "test", "https://openrouter.ai/api/v1", "other/model"
    )
    assert [o.model for o in options] == [models[2], models[1], models[3]]
    assert "none" not in options[0].reasoning_modes
    saved = openrouter._openrouter_model_options("test", "https://openrouter.ai/api/v1", models[0])
    assert models[0] in [o.model for o in saved]

    zen_flash = "opencode/minimax-m3.1-flash-free"
    monkeypatch.setattr(opencode, "_load_opencode_model_catalog", lambda cmd: {*models, zen_flash})
    options = opencode._opencode_model_options("opencode")
    assert [o.model for o in options][1:] == [
        "opencode/minimax/minimax-m3.1-flash:free",
        "opencode/minimax/minimax-m3",
        zen_flash,
        "opencode/other/model",
    ]
    saved = opencode._opencode_model_options("opencode", models[0])
    assert f"opencode/{models[0]}" in [o.model for o in saved]


@pytest.mark.parametrize("reasoning", ["auto", "high"])
async def test_openrouter_flash_route_serializes_without_remapping(
    captured_requests, monkeypatch, reasoning
):
    from repowise.core.providers.llm import minimax, openrouter

    monkeypatch.setattr(openrouter, "AsyncOpenAI", minimax.AsyncOpenAI)
    provider = get_provider("openrouter", api_key="test", model="minimax/minimax-m3.1-flash:free")
    try:
        await provider.generate("system", "user", reasoning=reasoning)
    finally:
        await provider._client.close()
    request, payload = captured_requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert payload["model"] == "minimax/minimax-m3.1-flash:free"
    assert "thinking" not in payload
    if reasoning == "high":
        assert payload["reasoning"] == {"effort": "high"}


async def test_openrouter_flash_rejects_disabling_thinking(captured_requests, monkeypatch):
    from repowise.core.providers.llm import minimax, openrouter

    monkeypatch.setattr(openrouter, "AsyncOpenAI", minimax.AsyncOpenAI)
    provider = get_provider("openrouter", api_key="test", model="minimax/minimax-m3.1-flash:free")
    try:
        with pytest.raises(ProviderError):
            await provider.generate("system", "user", reasoning="off")
    finally:
        await provider._client.close()
    assert captured_requests == []
