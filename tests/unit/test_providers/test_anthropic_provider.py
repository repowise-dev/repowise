"""Unit tests for AnthropicProvider.

All tests mock the AsyncAnthropic client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.llm.anthropic import AnthropicProvider


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = AnthropicProvider(api_key="sk-ant-test")
    assert p.provider_name == "anthropic"


def test_default_model():
    p = AnthropicProvider(api_key="sk-ant-test")
    assert p.model_name == "claude-sonnet-4-6"


def test_opus_model():
    p = AnthropicProvider(api_key="sk-ant-test", model="claude-opus-4-6")
    assert p.model_name == "claude-opus-4-6"


def test_haiku_model():
    p = AnthropicProvider(api_key="sk-ant-test", model="claude-haiku-4-5")
    assert p.model_name == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


def _make_mock_response(text: str = "# Doc\nContent.") -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = 200
    usage.output_tokens = 80
    usage.cache_read_input_tokens = 50
    usage.cache_creation_input_tokens = 0

    content_block = MagicMock()
    content_block.text = text

    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


async def test_generate_returns_generated_response():
    provider = AnthropicProvider(api_key="sk-ant-test")
    mock_response = _make_mock_response("Hello from Claude")

    with patch("anthropic.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(return_value=mock_response)
        provider._client = MockClient.return_value
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Claude"


async def test_generate_token_counts_with_cache():
    provider = AnthropicProvider(api_key="sk-ant-test")
    mock_response = _make_mock_response()

    with patch("anthropic.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(return_value=mock_response)
        provider._client = MockClient.return_value
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 200
    assert result.output_tokens == 80
    assert result.cached_tokens == 50


async def test_generate_sends_correct_params():
    provider = AnthropicProvider(api_key="sk-ant-test", model="claude-haiku-4-5")
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("anthropic.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = fake_create
        provider._client = MockClient.return_value
        await provider.generate("system msg", "user msg", max_tokens=1024, temperature=0.1)

    kw = captured_kwargs[0]
    assert kw["model"] == "claude-haiku-4-5"
    assert kw["max_tokens"] == 1024
    assert kw["temperature"] == 0.1
    assert kw["system"] == "system msg"
    assert kw["messages"] == [{"role": "user", "content": "user msg"}]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_rate_limit_error():
    from anthropic import RateLimitError as _AnthropicRateLimitError

    provider = AnthropicProvider(api_key="sk-ant-test")

    with patch("anthropic.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(
            side_effect=_AnthropicRateLimitError.__new__(_AnthropicRateLimitError)
        )
        provider._client = MockClient.return_value
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_api_status_error():
    from anthropic import APIStatusError as _AnthropicAPIStatusError

    provider = AnthropicProvider(api_key="sk-ant-test")

    err = _AnthropicAPIStatusError.__new__(_AnthropicAPIStatusError)
    err.status_code = 500

    with patch("anthropic.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(side_effect=err)
        provider._client = MockClient.return_value
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")
