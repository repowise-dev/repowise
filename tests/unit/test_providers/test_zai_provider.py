"""Unit tests for ZAIProvider.

All tests mock the OpenAI client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.llm.zai import _DEFAULT_MODEL, ZAIProvider

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = ZAIProvider(api_key="test-key")
    assert p.provider_name == "zai"


def test_default_model():
    p = ZAIProvider(api_key="test-key")
    assert p.model_name == _DEFAULT_MODEL
    assert p.model_name == "glm-5.1"


def test_custom_model():
    p = ZAIProvider(model="glm-5-turbo", api_key="test-key")
    assert p.model_name == "glm-5-turbo"


def test_default_thinking_disabled():
    p = ZAIProvider(api_key="test-key")
    assert p._thinking == "disabled"


def test_custom_thinking():
    p = ZAIProvider(api_key="test-key", thinking="enabled")
    assert p._thinking == "enabled"


def test_default_plan_is_coding():
    """Default plan should be 'coding'."""
    p = ZAIProvider(api_key="test-key")
    assert p._plan == "coding"


def test_coding_plan_base_url():
    """Coding plan should use coding endpoint."""
    p = ZAIProvider(api_key="test-key", plan="coding")
    assert p._base_url == "https://api.z.ai/api/coding/paas/v4/v1"


def test_general_plan_base_url():
    """General plan should use general endpoint."""
    p = ZAIProvider(api_key="test-key", plan="general")
    assert p._base_url == "https://api.z.ai/api/paas/v4/v1"


def test_base_url_overrides_plan():
    """Explicit base_url should take precedence over plan."""
    p = ZAIProvider(api_key="test-key", plan="coding", base_url="https://custom.api.com")
    assert p._base_url == "https://custom.api.com/v1"


def test_custom_base_url():
    """Custom base URL should be used and normalized."""
    p = ZAIProvider(api_key="test-key", base_url="https://api.z.ai/api/paas/v4")
    assert p._base_url == "https://api.z.ai/api/paas/v4/v1"


def test_base_url_normalization():
    """Base URL should be normalized to end with /v1."""
    p = ZAIProvider(api_key="test-key", base_url="https://custom.api.com")
    assert p._base_url == "https://custom.api.com/v1"


def test_base_url_already_has_v1():
    """Base URL already ending with /v1 should not get another suffix."""
    p = ZAIProvider(api_key="test-key", base_url="https://custom.api.com/v1")
    assert p._base_url == "https://custom.api.com/v1"


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


def _make_mock_response(text: str = "# Doc\nContent.") -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 120
    usage.completion_tokens = 60

    choice = MagicMock()
    choice.message.content = text

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


@pytest.mark.asyncio
async def test_generate_returns_generated_response():
    provider = ZAIProvider(api_key="test-key")
    mock_response = _make_mock_response("Hello from Z.AI")

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Z.AI"


@pytest.mark.asyncio
async def test_generate_token_counts():
    provider = ZAIProvider(api_key="test-key")
    mock_response = _make_mock_response()

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 60


@pytest.mark.asyncio
async def test_generate_sends_correct_kwargs():
    provider = ZAIProvider(model="glm-5-turbo", api_key="test-key")
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=fake_create,
    ):
        await provider.generate("system msg", "user msg", max_tokens=2048, temperature=0.5)

    kw = captured_kwargs[0]
    assert kw["model"] == "glm-5-turbo"
    assert kw["max_tokens"] == 2048
    assert kw["temperature"] == 0.5
    messages = kw["messages"]
    assert messages[0] == {"role": "system", "content": "system msg"}
    assert messages[1] == {"role": "user", "content": "user msg"}


@pytest.mark.asyncio
async def test_generate_disables_thinking_by_default():
    """By default, thinking should be disabled via extra_body."""
    provider = ZAIProvider(api_key="test-key")
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=fake_create,
    ):
        await provider.generate("sys", "user")

    kw = captured_kwargs[0]
    assert "extra_body" in kw
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_generate_with_thinking_enabled():
    """When thinking is enabled, extra_body should not contain disabled."""
    provider = ZAIProvider(api_key="test-key", thinking="enabled")
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=fake_create,
    ):
        await provider.generate("sys", "user")

    kw = captured_kwargs[0]
    # When thinking is enabled, we don't send extra_body with thinking disabled
    assert kw.get("extra_body") is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_error():
    from openai import APIStatusError

    provider = ZAIProvider(api_key="test-key")

    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=APIStatusError(
            "server error",
            response=mock_response,
            body={},
        ),
    ), pytest.raises(ProviderError) as exc_info:
        await provider.generate("sys", "user")

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_rate_limit_error():
    from openai import APIStatusError

    provider = ZAIProvider(api_key="test-key")

    mock_response = MagicMock()
    mock_response.status_code = 429

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=APIStatusError(
            "rate limit exceeded",
            response=mock_response,
            body={},
        ),
    ), pytest.raises(RateLimitError) as exc_info:
        await provider.generate("sys", "user")

    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# Stream chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_chat_yields_text_deltas():
    provider = ZAIProvider(api_key="test-key")

    # Create mock chunks
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta = MagicMock(content="Hello")
    chunk1.choices[0].finish_reason = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta = MagicMock(content=" world")
    chunk2.choices[0].finish_reason = None

    chunk3 = MagicMock()
    chunk3.choices = [MagicMock()]
    chunk3.choices[0].delta = MagicMock(content=None)
    chunk3.choices[0].finish_reason = "stop"

    async def fake_stream():
        yield chunk1
        yield chunk2
        yield chunk3

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=fake_stream(),
    ):
        events = []
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            system_prompt="sys",
        ):
            events.append(event)

    # Should have text deltas and a stop event
    assert len(events) == 3
    assert events[0].type == "text_delta"
    assert events[0].text == "Hello"
    assert events[1].type == "text_delta"
    assert events[1].text == " world"
    assert events[2].type == "stop"


@pytest.mark.asyncio
async def test_stream_chat_disables_thinking():
    """Stream chat should also disable thinking by default."""
    provider = ZAIProvider(api_key="test-key")
    captured_kwargs: list[dict] = []

    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock(content="test")
    chunk.choices[0].finish_reason = "stop"

    async def fake_stream():
        yield chunk

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return fake_stream()

    with patch.object(
        provider._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=fake_create,
    ):
        events = []
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            system_prompt="sys",
        ):
            events.append(event)

    kw = captured_kwargs[0]
    assert "extra_body" in kw
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}