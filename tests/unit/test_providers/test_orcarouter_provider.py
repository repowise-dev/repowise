"""Unit tests for OrcaRouterProvider.

All tests mock the AsyncOpenAI client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm.base import ProviderError, RateLimitError
from repowise.core.providers.llm.orcarouter import OrcaRouterProvider

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = OrcaRouterProvider(api_key="sk-orca-test")
    assert p.provider_name == "orcarouter"


def test_default_model():
    p = OrcaRouterProvider(api_key="sk-orca-test")
    assert p.model_name == "google/gemini-3.5-flash-lite"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-env-test")
    p = OrcaRouterProvider()
    assert p.provider_name == "orcarouter"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        OrcaRouterProvider()


def test_default_base_url():
    p = OrcaRouterProvider(api_key="sk-orca-test")
    assert p._base_url == "https://api.orcarouter.ai/v1"


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("ORCAROUTER_BASE_URL", "https://proxy.example/v1")
    p = OrcaRouterProvider(api_key="sk-orca-test")
    assert p._base_url == "https://proxy.example/v1"


def test_custom_model():
    p = OrcaRouterProvider(api_key="sk-orca-test", model="anthropic/claude-haiku-4.5")
    assert p.model_name == "anthropic/claude-haiku-4.5"


def test_accepts_cost_tracker_kwarg():
    """cost_tracker is accepted for registry parity but unused (OrcaRouter proxies
    many models with varying prices; repowise's fallback pricing would be misleading)."""
    sentinel = object()
    p = OrcaRouterProvider(api_key="sk-orca-test", cost_tracker=sentinel)
    assert p.provider_name == "orcarouter"


def test_rejects_unknown_kwargs():
    """Unknown kwargs must fail loud — silently swallowing them would hide future
    registry changes (e.g. new tier=, budget= params passed through)."""
    with pytest.raises(TypeError):
        OrcaRouterProvider(api_key="sk-orca-test", future_param="oops")


def test_supported_reasoning_modes_by_family():
    assert OrcaRouterProvider(
        api_key="sk-orca-test",
        model="google/gemini-3.5-flash-lite",
    ).supported_reasoning_modes() != ("auto",)
    assert OrcaRouterProvider(
        api_key="sk-orca-test",
        model="anthropic/claude-haiku-4.5",
    ).supported_reasoning_modes() != ("auto",)
    assert OrcaRouterProvider(
        api_key="sk-orca-test",
        model="orcarouter/fusion",
    ).supported_reasoning_modes() == ("auto",)


# ---------------------------------------------------------------------------
# available_model_options — filters to chat-capable models
# ---------------------------------------------------------------------------


def test_available_model_options_filters_chat_models(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "anthropic/claude-haiku-4.5", "supported_endpoint_types": ["anthropic", "openai"]},
                    {"id": "openai/gpt-5.4-mini", "supported_endpoint_types": ["openai"]},
                    {"id": "kling/kling-video", "supported_endpoint_types": ["openai-video"]},
                    {"id": "google/imagen-4.0", "supported_endpoint_types": ["image-generation"]},
                    {"id": "orcarouter/fusion", "supported_endpoint_types": None},
                ]
            }

    monkeypatch.setattr("httpx.get", MagicMock(return_value=FakeResponse()))
    options = OrcaRouterProvider(api_key="sk-orca-test").available_model_options()
    ids = [option.model for option in options]
    assert "anthropic/claude-haiku-4.5" in ids
    assert "openai/gpt-5.4-mini" in ids
    assert "orcarouter/fusion" in ids
    assert "kling/kling-video" not in ids
    assert "google/imagen-4.0" not in ids


def test_available_model_options_falls_back_on_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.get", _boom)
    options = OrcaRouterProvider(api_key="sk-orca-test").available_model_options()
    assert len(options) == 1
    assert options[0].model == "google/gemini-3.5-flash-lite"


# ---------------------------------------------------------------------------
# generate / stream_chat — mocked AsyncOpenAI
# ---------------------------------------------------------------------------


def _mock_response(content: str = "hello", finish_reason: str = "stop") -> MagicMock:
    usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    choice = MagicMock(finish_reason=finish_reason)
    choice.message.content = content
    response = MagicMock(usage=usage, choices=[choice])
    return response


@pytest.mark.asyncio
async def test_generate_success(monkeypatch):
    provider = OrcaRouterProvider(api_key="sk-orca-test")
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_response())
    provider._client = client

    result = await provider.generate(system_prompt="sys", user_prompt="usr")
    assert result.content == "hello"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    create_kwargs = client.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "google/gemini-3.5-flash-lite"
    assert create_kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


@pytest.mark.asyncio
async def test_generate_sends_reasoning_effort(monkeypatch):
    provider = OrcaRouterProvider(
        api_key="sk-orca-test", model="anthropic/claude-haiku-4.5"
    )
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_response())
    provider._client = client

    await provider.generate(
        system_prompt="sys", user_prompt="usr", reasoning="high"
    )
    create_kwargs = client.chat.completions.create.call_args.kwargs
    assert create_kwargs["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_generate_clamps_max_to_xhigh(monkeypatch):
    provider = OrcaRouterProvider(
        api_key="sk-orca-test", model="anthropic/claude-haiku-4.5"
    )
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_response())
    provider._client = client

    await provider.generate(system_prompt="sys", user_prompt="usr", reasoning="max")
    create_kwargs = client.chat.completions.create.call_args.kwargs
    assert create_kwargs["reasoning_effort"] == "xhigh"


@pytest.mark.asyncio
async def test_generate_off_sends_no_reasoning(monkeypatch):
    provider = OrcaRouterProvider(
        api_key="sk-orca-test", model="anthropic/claude-haiku-4.5"
    )
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_response())
    provider._client = client

    await provider.generate(system_prompt="sys", user_prompt="usr", reasoning="off")
    create_kwargs = client.chat.completions.create.call_args.kwargs
    assert "reasoning_effort" not in create_kwargs


@pytest.mark.asyncio
async def test_generate_rate_limit_error(monkeypatch):
    provider = OrcaRouterProvider(api_key="sk-orca-test")
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(
        side_effect=__import__(
            "openai"
        ).RateLimitError(
            "rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
    )
    provider._client = client

    with pytest.raises(RateLimitError):
        await provider.generate(system_prompt="sys", user_prompt="usr")


@pytest.mark.asyncio
async def test_stream_chat_yields_text_and_stop(monkeypatch):
    from openai.types.chat import ChatCompletionChunk

    provider = OrcaRouterProvider(api_key="sk-orca-test")

    chunk = MagicMock(spec=ChatCompletionChunk)
    chunk.choices = [
        MagicMock(delta=MagicMock(content="hi", tool_calls=None), finish_reason=None)
    ]
    stop = MagicMock(spec=ChatCompletionChunk)
    stop.choices = [MagicMock(delta=MagicMock(content="", tool_calls=None), finish_reason="stop")]

    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_stream(chunk, stop))
    provider._client = client

    events = [e async for e in provider.stream_chat(
        messages=[{"role": "user", "content": "u"}],
        tools=[],
        system_prompt="sys",
    )]
    types = [e.type for e in events]
    assert "text_delta" in types
    assert "stop" in types


async def _stream(*chunks):
    for chunk in chunks:
        yield chunk
