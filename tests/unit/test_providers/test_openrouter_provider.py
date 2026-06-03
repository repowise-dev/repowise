"""Unit tests for OpenRouterProvider.

All tests mock the AsyncOpenAI client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm import openrouter as openrouter_module
from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.llm.openrouter import OpenRouterProvider

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = OpenRouterProvider(api_key="sk-or-test")
    assert p.provider_name == "openrouter"


def test_default_model():
    p = OpenRouterProvider(api_key="sk-or-test")
    assert p.model_name == "anthropic/claude-sonnet-4.6"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env-test")
    p = OpenRouterProvider()
    assert p.provider_name == "openrouter"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        OpenRouterProvider()


def test_custom_model():
    p = OpenRouterProvider(api_key="sk-or-test", model="google/gemini-3.1-flash-lite-preview")
    assert p.model_name == "google/gemini-3.1-flash-lite-preview"


def test_supported_reasoning_modes_are_model_specific():
    assert OpenRouterProvider(
        api_key="sk-or-test",
        model="x-ai/grok-4",
    ).supported_reasoning_modes() == ("auto",)
    assert OpenRouterProvider(
        api_key="sk-or-test",
        model="anthropic/claude-sonnet-4.6",
    ).supported_reasoning_modes() == ("auto",)


def test_default_headers_app_title():
    """Default app_title='repowise' sets X-Title header."""
    p = OpenRouterProvider(api_key="sk-or-test")
    headers = p._client._custom_headers
    assert headers.get("X-Title") == "repowise"


def test_default_headers_with_referer():
    """When http_referer is provided, HTTP-Referer header is set."""
    p = OpenRouterProvider(api_key="sk-or-test", http_referer="https://example.com")
    headers = p._client._custom_headers
    assert headers.get("HTTP-Referer") == "https://example.com"
    assert headers.get("X-Title") == "repowise"


def test_no_headers_when_empty():
    """When app_title is empty and no referer, no custom headers."""
    p = OpenRouterProvider(api_key="sk-or-test", app_title="")
    # default_headers should be None → no custom headers set
    headers = p._client._custom_headers
    assert not headers.get("X-Title")


def test_accepts_cost_tracker_kwarg():
    """cost_tracker is accepted for registry parity but ignored (OpenRouter proxies
    200+ models with varying prices; repowise's fallback pricing would be misleading)."""
    sentinel = object()
    p = OpenRouterProvider(api_key="sk-or-test", cost_tracker=sentinel)
    assert p.provider_name == "openrouter"


def test_rejects_unknown_kwargs():
    """Unknown kwargs must fail loud — silently swallowing them would hide future
    registry changes (e.g. new tier=, budget= params passed through)."""
    with pytest.raises(TypeError):
        OpenRouterProvider(api_key="sk-or-test", future_param="oops")


def test_available_model_options_uses_models_endpoint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {
                        "id": "vendor/reasoner",
                        "name": "Vendor Reasoner",
                        "supported_parameters": ["reasoning", "tools"],
                    },
                    {
                        "id": "vendor/plain",
                        "name": "Vendor Plain",
                        "supported_parameters": ["tools"],
                    },
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    provider = OpenRouterProvider(api_key="sk-or-test")
    options = provider.available_model_options()

    assert captured["url"] == "https://openrouter.ai/api/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer sk-or-test"}
    reasoner = next(option for option in options if option.model == "vendor/reasoner")
    assert reasoner.reasoning_modes == (
        "auto",
        "off",
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert (
        OpenRouterProvider(
            api_key="sk-or-test",
            model="vendor/reasoner",
        ).supported_reasoning_modes()
        == reasoner.reasoning_modes
    )


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


def _make_mock_chat_response(text: str = "# Doc\nContent.") -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 120
    usage.completion_tokens = 60
    usage.total_tokens = 180

    choice = MagicMock()
    choice.message.content = text

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


async def test_generate_returns_generated_response():
    provider = OpenRouterProvider(api_key="sk-or-test")
    mock_response = _make_mock_chat_response("Hello from OpenRouter")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from OpenRouter"


async def test_generate_token_counts():
    provider = OpenRouterProvider(api_key="sk-or-test")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 60
    assert result.cached_tokens == 0


async def test_generate_sends_correct_messages():
    provider = OpenRouterProvider(
        api_key="sk-or-test", model="google/gemini-3.1-flash-lite-preview"
    )
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", max_tokens=2048, temperature=0.5)

    kw = captured_kwargs[0]
    assert kw["model"] == "google/gemini-3.1-flash-lite-preview"
    assert kw["max_tokens"] == 2048
    assert kw["temperature"] == 0.5
    assert "extra_body" not in kw
    messages = kw["messages"]
    assert messages[0] == {"role": "system", "content": "system msg"}
    assert messages[1] == {"role": "user", "content": "user msg"}


async def test_generate_forwards_minimal_reasoning_extra_body():
    openrouter_module._OPENROUTER_REASONING_MODELS_BY_BASE["https://openrouter.ai/api/v1"] = {
        "openai/gpt-5"
    }
    provider = OpenRouterProvider(api_key="sk-or-test", model="openai/gpt-5")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="minimal")

    assert captured_kwargs[0]["extra_body"] == {"reasoning": {"effort": "minimal"}}


async def test_generate_forwards_high_reasoning_extra_body():
    openrouter_module._OPENROUTER_REASONING_MODELS_BY_BASE["https://openrouter.ai/api/v1"] = {
        "x-ai/grok-4"
    }
    provider = OpenRouterProvider(api_key="sk-or-test", model="x-ai/grok-4")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="high")

    assert captured_kwargs[0]["extra_body"] == {"reasoning": {"effort": "high"}}


async def test_generate_forwards_off_reasoning_extra_body():
    openrouter_module._OPENROUTER_REASONING_MODELS_BY_BASE["https://openrouter.ai/api/v1"] = {
        "x-ai/grok-4"
    }
    provider = OpenRouterProvider(api_key="sk-or-test", model="x-ai/grok-4")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="off")

    assert captured_kwargs[0]["extra_body"] == {"reasoning": {"effort": "none"}}


async def test_generate_forwards_none_reasoning_extra_body():
    openrouter_module._OPENROUTER_REASONING_MODELS_BY_BASE["https://openrouter.ai/api/v1"] = {
        "x-ai/grok-4"
    }
    provider = OpenRouterProvider(api_key="sk-or-test", model="x-ai/grok-4")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="none")

    assert captured_kwargs[0]["extra_body"] == {"reasoning": {"effort": "none"}}


async def test_generate_rejects_reasoning_for_unsupported_openrouter_model(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("httpx.get", fake_get)
    provider = OpenRouterProvider(api_key="sk-or-test", model="anthropic/claude-sonnet-4.6")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='minimal' is not supported"):
            await provider.generate("system msg", "user msg", reasoning="minimal")

    mock_client.return_value.chat.completions.create.assert_not_called()


@pytest.mark.parametrize(
    "model",
    ["openai/gpt-4.1", "anthropic/claude-sonnet-4.6"],
)
async def test_generate_rejects_reasoning_for_known_unsupported_openai_route(monkeypatch, model):
    def fake_get(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("httpx.get", fake_get)
    provider = OpenRouterProvider(api_key="sk-or-test", model=model)

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='minimal' is not supported"):
            await provider.generate("system msg", "user msg", reasoning="minimal")

    mock_client.return_value.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_rate_limit_error():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = OpenRouterProvider(api_key="sk-or-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIRateLimitError(
                "rate limit", response=MagicMock(status_code=429), body={}
            )
        )
        provider._client = mock_client.return_value
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_api_status_error():
    from openai import APIStatusError as _OpenAIAPIStatusError

    provider = OpenRouterProvider(api_key="sk-or-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIAPIStatusError(
                "server error", response=MagicMock(status_code=500), body={}
            )
        )
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")


# ---------------------------------------------------------------------------
# stream_chat
# ---------------------------------------------------------------------------


def _make_stream_chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    tool_calls: list | None = None,
) -> MagicMock:
    """Build a single streaming chunk matching the OpenAI SDK shape."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason

    chunk = MagicMock()
    chunk.choices = [choice]
    chunk.usage = None
    return chunk


async def _collect_events(async_iter):
    """Collect all events from an async iterator."""
    events = []
    async for event in async_iter:
        events.append(event)
    return events


async def test_stream_chat_text_deltas():
    provider = OpenRouterProvider(api_key="sk-or-test")

    chunks = [
        _make_stream_chunk(content="Hello"),
        _make_stream_chunk(content=" world"),
        _make_stream_chunk(finish_reason="stop"),
    ]

    async def fake_stream():
        for c in chunks:
            yield c

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=fake_stream())
        provider._client = mock_client.return_value
        events = await _collect_events(
            provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                system_prompt="sys",
            )
        )

    text_events = [e for e in events if e.type == "text_delta"]
    assert len(text_events) == 2
    assert text_events[0].text == "Hello"
    assert text_events[1].text == " world"

    stop_events = [e for e in events if e.type == "stop"]
    assert len(stop_events) == 1
    assert stop_events[0].stop_reason == "end_turn"


async def test_stream_chat_tool_calls():
    provider = OpenRouterProvider(api_key="sk-or-test")

    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = "call_123"
    tc_delta.function = MagicMock()
    tc_delta.function.name = "search"
    tc_delta.function.arguments = '{"query": "test"}'

    chunks = [
        _make_stream_chunk(tool_calls=[tc_delta]),
        _make_stream_chunk(finish_reason="tool_calls"),
    ]

    async def fake_stream():
        for c in chunks:
            yield c

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=fake_stream())
        provider._client = mock_client.return_value
        events = await _collect_events(
            provider.stream_chat(
                messages=[{"role": "user", "content": "search for test"}],
                tools=[{"type": "function", "function": {"name": "search"}}],
                system_prompt="sys",
            )
        )

    tool_events = [e for e in events if e.type == "tool_start"]
    assert len(tool_events) == 1
    assert tool_events[0].tool_call.name == "search"
    assert tool_events[0].tool_call.arguments == {"query": "test"}

    stop_events = [e for e in events if e.type == "stop"]
    assert len(stop_events) == 1
    assert stop_events[0].stop_reason == "tool_use"


async def test_stream_chat_rate_limit_error():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = OpenRouterProvider(api_key="sk-or-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIRateLimitError(
                "rate limit", response=MagicMock(status_code=429), body={}
            )
        )
        provider._client = mock_client.return_value
        with pytest.raises(RateLimitError):
            await _collect_events(
                provider.stream_chat(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                    system_prompt="sys",
                )
            )
