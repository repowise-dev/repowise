"""Unit tests for OpenAIProvider.

All tests mock the AsyncOpenAI client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.llm.openai import OpenAIProvider


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = OpenAIProvider(api_key="sk-test")
    assert p.provider_name == "openai"


def test_default_model_is_nano():
    p = OpenAIProvider(api_key="sk-test")
    assert p.model_name == "gpt-5.4-nano"


def test_custom_model():
    p = OpenAIProvider(api_key="sk-test", model="gpt-5.4-mini")
    assert p.model_name == "gpt-5.4-mini"


def test_gpt54_model():
    p = OpenAIProvider(api_key="sk-test", model="gpt-5.4")
    assert p.model_name == "gpt-5.4"


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
    provider = OpenAIProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response("Hello from OpenAI")

    with patch("openai.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = MockClient.return_value
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from OpenAI"


async def test_generate_token_counts():
    provider = OpenAIProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = MockClient.return_value
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 60
    assert result.cached_tokens == 0


async def test_generate_sends_correct_messages():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.4-mini")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = fake_create
        provider._client = MockClient.return_value
        await provider.generate("system msg", "user msg", max_tokens=2048, temperature=0.5)

    kw = captured_kwargs[0]
    assert kw["model"] == "gpt-5.4-mini"
    assert kw["max_completion_tokens"] == 2048
    assert kw["temperature"] == 0.5
    messages = kw["messages"]
    assert messages[0] == {"role": "system", "content": "system msg"}
    assert messages[1] == {"role": "user", "content": "user msg"}


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_rate_limit_error():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = OpenAIProvider(api_key="sk-test")

    with patch("openai.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIRateLimitError("rate limit", response=MagicMock(status_code=429), body={})
        )
        provider._client = MockClient.return_value
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_api_status_error():
    from openai import APIStatusError as _OpenAIAPIStatusError

    provider = OpenAIProvider(api_key="sk-test")

    with patch("openai.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIAPIStatusError(
                "server error", response=MagicMock(status_code=500), body={}
            )
        )
        provider._client = MockClient.return_value
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")
