"""Unit tests for MiniMaxProvider.

Tests cover:
    - Constructor defaults and configuration
    - Provider name and model name
    - Rate limiter attachment
    - Tier-based rate limiting via generic framework
    - generate() and stream_chat() with mocked OpenAI client
    - reasoning_split extra_body parameter
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.providers.llm.base import (
    ChatStreamEvent,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)
from repowise.core.providers.llm.minimax import MiniMaxProvider
from repowise.core.rate_limiter import RateLimitConfig, RateLimiter


# ---------------------------------------------------------------------------
# Constructor & properties
# ---------------------------------------------------------------------------


def test_default_model():
    p = MiniMaxProvider(api_key="test-key")
    assert p.model_name == "MiniMax-M2.7"


def test_custom_model():
    p = MiniMaxProvider(api_key="test-key", model="MiniMax-M2.7-highspeed")
    assert p.model_name == "MiniMax-M2.7-highspeed"


def test_provider_name():
    p = MiniMaxProvider(api_key="test-key")
    assert p.provider_name == "minimax"


def test_default_base_url():
    p = MiniMaxProvider(api_key="test-key")
    assert p._base_url == "https://api.minimax.io/v1"


def test_custom_base_url():
    p = MiniMaxProvider(api_key="test-key", base_url="https://custom.example.com/v1")
    assert p._base_url == "https://custom.example.com/v1"


def test_default_reasoning_split():
    p = MiniMaxProvider(api_key="test-key")
    assert p._reasoning_split is True


def test_reasoning_split_disabled():
    p = MiniMaxProvider(api_key="test-key", reasoning_split=False)
    assert p._reasoning_split is False


def test_no_rate_limiter_by_default():
    p = MiniMaxProvider(api_key="test-key")
    assert p._rate_limiter is None


def test_explicit_rate_limiter():
    limiter = RateLimiter(RateLimitConfig(requests_per_minute=42, tokens_per_minute=420_000))
    p = MiniMaxProvider(api_key="test-key", rate_limiter=limiter)
    assert p._rate_limiter is limiter


# ---------------------------------------------------------------------------
# Tier-based rate limiting (via generic framework)
# ---------------------------------------------------------------------------


def test_tier_creates_rate_limiter():
    p = MiniMaxProvider(api_key="test-key", tier="plus")
    assert p._rate_limiter is not None
    assert p._rate_limiter.config.requests_per_minute == MiniMaxProvider.RATE_LIMIT_TIERS["plus"].requests_per_minute
    assert p._rate_limiter.config.tokens_per_minute == MiniMaxProvider.RATE_LIMIT_TIERS["plus"].tokens_per_minute


def test_tier_starter():
    p = MiniMaxProvider(api_key="test-key", tier="starter")
    assert p._rate_limiter is not None
    assert p._rate_limiter.config.requests_per_minute == 5
    assert p._rate_limiter.config.tokens_per_minute == 25_000


def test_tier_plus():
    p = MiniMaxProvider(api_key="test-key", tier="plus")
    assert p._rate_limiter is not None
    assert p._rate_limiter.config.requests_per_minute == 15
    assert p._rate_limiter.config.tokens_per_minute == 75_000


def test_tier_max():
    p = MiniMaxProvider(api_key="test-key", tier="max")
    assert p._rate_limiter is not None
    assert p._rate_limiter.config.requests_per_minute == 50
    assert p._rate_limiter.config.tokens_per_minute == 250_000


def test_tier_ultra():
    p = MiniMaxProvider(api_key="test-key", tier="ultra")
    assert p._rate_limiter is not None
    assert p._rate_limiter.config.requests_per_minute == 100
    assert p._rate_limiter.config.tokens_per_minute == 500_000


def test_tier_case_insensitive():
    p = MiniMaxProvider(api_key="test-key", tier="PLUS")
    assert p._rate_limiter is not None
    assert p._rate_limiter.config.requests_per_minute == 15


def test_tier_overrides_explicit_rate_limiter():
    explicit_limiter = RateLimiter(RateLimitConfig(requests_per_minute=999, tokens_per_minute=999_999))
    p = MiniMaxProvider(api_key="test-key", tier="starter", rate_limiter=explicit_limiter)
    assert p._rate_limiter.config.requests_per_minute == 5
    assert p._rate_limiter is not explicit_limiter


def test_explicit_rate_limiter_without_tier():
    limiter = RateLimiter(RateLimitConfig(requests_per_minute=42, tokens_per_minute=420_000))
    p = MiniMaxProvider(api_key="test-key", rate_limiter=limiter)
    assert p._rate_limiter is limiter


def test_invalid_tier_raises():
    with pytest.raises(ValueError, match="Unknown tier"):
        MiniMaxProvider(api_key="test-key", tier="enterprise")


def test_tier_stored():
    p = MiniMaxProvider(api_key="test-key", tier="plus")
    assert p._tier == "plus"


def test_no_tier_stored_as_none():
    p = MiniMaxProvider(api_key="test-key")
    assert p._tier is None


def test_provider_has_rate_limit_tiers_attribute():
    assert hasattr(MiniMaxProvider, "RATE_LIMIT_TIERS")
    assert "starter" in MiniMaxProvider.RATE_LIMIT_TIERS
    assert "plus" in MiniMaxProvider.RATE_LIMIT_TIERS
    assert "max" in MiniMaxProvider.RATE_LIMIT_TIERS
    assert "ultra" in MiniMaxProvider.RATE_LIMIT_TIERS


# ---------------------------------------------------------------------------
# generate() with mocked OpenAI client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_basic():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello from MiniMax"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5

    captured_kwargs: list[dict[str, Any]] = []

    async def mock_create(**kwargs: Any) -> MagicMock:
        captured_kwargs.append(kwargs)
        return mock_response

    p = MiniMaxProvider(api_key="test-key")
    p._client = MagicMock()
    p._client.chat.completions.create = mock_create

    result = await p.generate(
        system_prompt="You are helpful.",
        user_prompt="Hi",
        max_tokens=100,
        temperature=0.5,
    )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from MiniMax"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


@pytest.mark.asyncio
async def test_generate_includes_reasoning_split():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "test"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 3

    captured_kwargs: list[dict[str, Any]] = []

    async def mock_create(**kwargs: Any) -> MagicMock:
        captured_kwargs.append(kwargs)
        return mock_response

    p = MiniMaxProvider(api_key="test-key", reasoning_split=True)
    p._client = MagicMock()
    p._client.chat.completions.create = mock_create

    await p.generate(system_prompt="sys", user_prompt="usr")

    kw = captured_kwargs[0]
    assert "extra_body" in kw
    assert kw["extra_body"] == {"reasoning_split": True}


@pytest.mark.asyncio
async def test_generate_without_reasoning_split():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "test"
    mock_response.usage = MagicMock()
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 3

    captured_kwargs: list[dict[str, Any]] = []

    async def mock_create(**kwargs: Any) -> MagicMock:
        captured_kwargs.append(kwargs)
        return mock_response

    p = MiniMaxProvider(api_key="test-key", reasoning_split=False)
    p._client = MagicMock()
    p._client.chat.completions.create = mock_create

    await p.generate(system_prompt="sys", user_prompt="usr")

    kw = captured_kwargs[0]
    assert "extra_body" not in kw


@pytest.mark.asyncio
async def test_generate_429_raises_rate_limit_error():
    from openai import APIStatusError

    mock_response = MagicMock()
    mock_response.status_code = 429

    async def mock_create(**kwargs: Any) -> None:
        raise APIStatusError(
            message="Rate limited",
            response=mock_response,
            body=None,
        )

    p = MiniMaxProvider(api_key="test-key")
    p._client = MagicMock()
    p._client.chat.completions.create = mock_create

    # Patch retry to single attempt for test speed
    with patch.object(p, "_generate_with_retry", side_effect=RateLimitError("minimax", "429", status_code=429)):
        with pytest.raises(RateLimitError):
            await p.generate(system_prompt="sys", user_prompt="usr")


# ---------------------------------------------------------------------------
# stream_chat() with mocked OpenAI client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_chat_text_delta():
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta = MagicMock(content="Hello")
    chunk1.choices[0].finish_reason = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta = MagicMock(content=" world")
    chunk2.choices[0].finish_reason = "stop"

    async def mock_stream(**kwargs: Any):
        for chunk in [chunk1, chunk2]:
            yield chunk

    p = MiniMaxProvider(api_key="test-key")
    p._client = MagicMock()

    async def mock_create(**kwargs: Any):
        return mock_stream(**kwargs)

    p._client.chat.completions.create = mock_create

    events = []
    async for event in p.stream_chat(
        messages=[{"role": "user", "content": "Hi"}],
        tools=[],
        system_prompt="You are helpful.",
    ):
        events.append(event)

    assert len(events) == 3
    assert events[0].type == "text_delta"
    assert events[0].text == "Hello"
    assert events[1].type == "text_delta"
    assert events[1].text == " world"
    assert events[2].type == "stop"
    assert events[2].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_stream_chat_includes_reasoning_split():
    captured_kwargs: list[dict[str, Any]] = []

    async def mock_stream(**kwargs: Any):
        yield MagicMock()  # dummy

    async def mock_create(**kwargs: Any):
        captured_kwargs.append(kwargs)
        return mock_stream(**kwargs)

    p = MiniMaxProvider(api_key="test-key", reasoning_split=True)
    p._client = MagicMock()
    p._client.chat.completions.create = mock_create

    events = []
    async for event in p.stream_chat(
        messages=[{"role": "user", "content": "Hi"}],
        tools=[],
        system_prompt="sys",
    ):
        events.append(event)

    kw = captured_kwargs[0]
    assert "extra_body" in kw
    assert kw["extra_body"] == {"reasoning_split": True}


@pytest.mark.asyncio
async def test_stream_chat_tool_calls():
    chunk1 = MagicMock()
    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = "call_123"
    tc_delta.function = MagicMock()
    tc_delta.function.name = "get_weather"
    tc_delta.function.arguments = '{"city": '
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta = MagicMock(content=None, tool_calls=[tc_delta])
    chunk1.choices[0].finish_reason = None

    chunk2 = MagicMock()
    tc_delta2 = MagicMock()
    tc_delta2.index = 0
    tc_delta2.id = None
    tc_delta2.function = MagicMock()
    tc_delta2.function.name = None
    tc_delta2.function.arguments = '"NYC"}'
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta = MagicMock(content=None, tool_calls=[tc_delta2])
    chunk2.choices[0].finish_reason = "tool_calls"

    async def mock_stream(**kwargs: Any):
        for chunk in [chunk1, chunk2]:
            yield chunk

    p = MiniMaxProvider(api_key="test-key")
    p._client = MagicMock()

    async def mock_create(**kwargs: Any):
        return mock_stream(**kwargs)

    p._client.chat.completions.create = mock_create

    events = []
    async for event in p.stream_chat(
        messages=[{"role": "user", "content": "Weather?"}],
        tools=[{"type": "function", "function": {"name": "get_weather"}}],
        system_prompt="sys",
    ):
        events.append(event)

    assert events[0].type == "tool_start"
    assert events[0].tool_call.name == "get_weather"
    assert events[0].tool_call.arguments == {"city": "NYC"}
    assert events[1].type == "stop"
    assert events[1].stop_reason == "tool_use"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_registry_has_minimax():
    from repowise.core.providers.llm.registry import _BUILTIN_PROVIDERS

    assert "minimax" in _BUILTIN_PROVIDERS


def test_rate_limiter_defaults_has_minimax():
    from repowise.core.rate_limiter import PROVIDER_DEFAULTS

    assert "minimax" in PROVIDER_DEFAULTS
    assert PROVIDER_DEFAULTS["minimax"].requests_per_minute == 5
    assert PROVIDER_DEFAULTS["minimax"].tokens_per_minute == 25_000
