"""Unit tests for DeepSeekProvider.

All tests mock the AsyncOpenAI client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm.base import (
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)
from repowise.core.providers.llm.deepseek import DeepSeekProvider


def test_provider_name():
    p = DeepSeekProvider(api_key="sk-test")
    assert p.provider_name == "deepseek"


def test_default_model_is_flash():
    p = DeepSeekProvider(api_key="sk-test")
    assert p.model_name == "deepseek-v4-flash"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-test")
    p = DeepSeekProvider()
    assert p.provider_name == "deepseek"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        DeepSeekProvider()


def test_custom_model():
    p = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-pro")
    assert p.model_name == "deepseek-v4-pro"


def test_custom_base_url():
    p = DeepSeekProvider(api_key="sk-test", base_url="https://custom.deepseek.com")
    assert p.provider_name == "deepseek"


def test_available_model_options_uses_models_endpoint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "deepseek-v4-flash"},
                    {"id": "deepseek-v4-pro"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = DeepSeekProvider(api_key="sk-test").available_model_options()

    assert captured["url"] == "https://api.deepseek.com/models"
    assert captured["headers"] == {"Authorization": "Bearer sk-test"}
    flash = next(option for option in options if option.model == "deepseek-v4-flash")
    assert flash.reasoning_modes == (
        "auto",
        "off",
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    assert flash.recommended is True


def _make_mock_chat_response(
    text: str = "# Doc\nContent.",
    *,
    finish_reason: str = "stop",
) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 120
    usage.completion_tokens = 60
    usage.total_tokens = 180

    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_mock_stream_chunks(text: str) -> list[MagicMock]:
    chunks = []
    for char in text:
        delta = MagicMock()
        delta.content = char
        delta.tool_calls = None
        choice = MagicMock()
        choice.delta = delta
        choice.finish_reason = None
        chunk = MagicMock()
        chunk.choices = [choice]
        chunk.usage = None
        chunks.append(chunk)

    finish_delta = MagicMock()
    finish_delta.content = None
    finish_delta.tool_calls = None
    finish_choice = MagicMock()
    finish_choice.delta = finish_delta
    finish_choice.finish_reason = "stop"
    finish_chunk = MagicMock()
    finish_chunk.choices = [finish_choice]
    finish_chunk.usage = None
    chunks.append(finish_chunk)

    return chunks


async def test_generate_returns_generated_response():
    provider = DeepSeekProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response("Hello from DeepSeek")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(
            system_prompt="You are a test assistant",
            user_prompt="Say hello",
        )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from DeepSeek"
    assert result.input_tokens == 120
    assert result.output_tokens == 60
    assert result.stop_reason == "end_turn"
    assert result.provider_stop_reason == "stop"


async def test_generate_uses_correct_model_name():
    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(
            system_prompt="system",
            user_prompt="user",
        )

        mock_client.return_value.chat.completions.create.assert_called_once()
        kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "deepseek-v4-flash"


async def test_generate_forwards_disabled_thinking():
    provider = DeepSeekProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        await provider.generate("system", "user", reasoning="off")

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_generate_rejects_reasoning_for_non_v4_model():
    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='high' is not supported"):
            await provider.generate("system", "user", reasoning="high")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_rate_limit_retry():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = DeepSeekProvider(api_key="sk-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIRateLimitError(
                message="Rate limited",
                body={},
                response=MagicMock(status_code=429),
            )
        )
        provider._client = mock_client.return_value

        with pytest.raises(RateLimitError):
            await provider.generate(
                system_prompt="system",
                user_prompt="user",
            )


async def test_generate_api_error():
    from openai import APIStatusError as _OpenAIAPIStatusError

    provider = DeepSeekProvider(api_key="sk-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIAPIStatusError(
                message="Internal error",
                body={},
                response=MagicMock(status_code=500),
            )
        )
        provider._client = mock_client.return_value

        with pytest.raises(ProviderError) as excinfo:
            await provider.generate(
                system_prompt="system",
                user_prompt="user",
            )
        assert excinfo.value.status_code == 500


async def test_cost_tracker_called():
    from repowise.core.generation.cost_tracker import CostTracker

    mock_tracker = MagicMock(spec=CostTracker)
    mock_tracker.record = AsyncMock(return_value=0.0)

    provider = DeepSeekProvider(api_key="sk-test", cost_tracker=mock_tracker)
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(
            system_prompt="system",
            user_prompt="user",
        )

    mock_tracker.record.assert_called_once()
    call_kwargs = mock_tracker.record.call_args.kwargs
    assert call_kwargs["model"] == "deepseek-v4-flash"
    assert call_kwargs["input_tokens"] == 120
    assert call_kwargs["output_tokens"] == 60


async def test_stream_chat_emits_text_delta_and_stop():
    provider = DeepSeekProvider(api_key="sk-test")

    async def _async_gen():
        for chunk in _make_mock_stream_chunks("Hi"):
            yield chunk

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=_async_gen())
        provider._client = mock_client.return_value

        events = []
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
            system_prompt="You are helpful",
        ):
            events.append(event)

    text_deltas = [e for e in events if e.type == "text_delta"]
    stops = [e for e in events if e.type == "stop"]
    assert len(text_deltas) == 2
    assert text_deltas[0].text == "H"
    assert text_deltas[1].text == "i"
    assert len(stops) == 1
    assert stops[0].stop_reason == "end_turn"
