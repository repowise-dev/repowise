"""Unit tests for KimiProvider.

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
from repowise.core.providers.llm.kimi import KimiProvider


def test_provider_name():
    p = KimiProvider(api_key="sk-test")
    assert p.provider_name == "kimi"


def test_default_model_is_kimi_for_coding():
    p = KimiProvider(api_key="sk-test")
    assert p.model_name == "kimi-for-coding"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-env-test")
    p = KimiProvider()
    assert p.provider_name == "kimi"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        KimiProvider()


def test_custom_model():
    p = KimiProvider(api_key="sk-test", model="kimi-k2.6")
    assert p.model_name == "kimi-k2.6"


def test_custom_base_url():
    p = KimiProvider(api_key="sk-test", base_url="https://custom.kimi.com")
    assert p.provider_name == "kimi"


def test_available_model_options_uses_models_endpoint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "kimi-for-coding"},
                    {"id": "kimi-for-coding-highspeed"},
                    {"id": "kimi-k2.5"},
                    {"id": "kimi-k2.6"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = KimiProvider(api_key="sk-test").available_model_options()

    assert captured["url"] == "https://api.kimi.com/coding/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer sk-test"}

    coding = next(option for option in options if option.model == "kimi-for-coding")
    assert coding.reasoning_modes == ("auto",)
    assert coding.recommended is True

    k2 = next(option for option in options if option.model == "kimi-k2.6")
    assert k2.reasoning_modes == (
        "auto",
        "off",
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


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
    provider = KimiProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response("Hello from Kimi")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(
            system_prompt="You are a test assistant",
            user_prompt="Say hello",
        )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Kimi"
    assert result.stop_reason == "end_turn"
    assert result.provider_stop_reason == "stop"
    assert result.input_tokens == 120
    assert result.output_tokens == 60


async def test_generate_uses_correct_model_name():
    provider = KimiProvider(api_key="sk-test", model="kimi-for-coding")
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
        assert kwargs["model"] == "kimi-for-coding"


async def test_kimi_for_coding_pins_sampling_parameters():
    provider = KimiProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(
            "system",
            "user",
            temperature=0.3,
        )

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.6
    assert kwargs["top_p"] == 0.95
    assert "presence_penalty" not in kwargs
    assert "frequency_penalty" not in kwargs
    assert "extra_body" not in kwargs


async def test_kimi_for_coding_highspeed_pins_sampling_parameters():
    provider = KimiProvider(api_key="sk-test", model="kimi-for-coding-highspeed")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate("system", "user", temperature=0.3)

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.6
    assert kwargs["top_p"] == 0.95
    assert "extra_body" not in kwargs


async def test_k2_instant_mode_pins_sampling_parameters():
    provider = KimiProvider(api_key="sk-test", model="kimi-k2.6")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(
            "system",
            "user",
            temperature=0.3,
            reasoning="off",
        )

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.6
    assert kwargs["top_p"] == 0.95
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "presence_penalty" not in kwargs
    assert "frequency_penalty" not in kwargs


async def test_k2_thinking_mode_pins_sampling_parameters():
    provider = KimiProvider(api_key="sk-test", model="kimi-k2.6")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(
            "system",
            "user",
            temperature=0.3,
            reasoning="high",
        )

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 1.0
    assert kwargs["top_p"] == 0.95
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "presence_penalty" not in kwargs
    assert "frequency_penalty" not in kwargs


async def test_generate_rejects_reasoning_for_non_k2_model():
    provider = KimiProvider(api_key="sk-test", model="kimi-for-coding")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='high' is not supported"):
            await provider.generate("system", "user", reasoning="high")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_rate_limit_retry():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = KimiProvider(api_key="sk-test")

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

    provider = KimiProvider(api_key="sk-test")

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

    provider = KimiProvider(api_key="sk-test", cost_tracker=mock_tracker)
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
    assert call_kwargs["model"] == "kimi-for-coding"
    assert call_kwargs["input_tokens"] == 120
    assert call_kwargs["output_tokens"] == 60


async def test_stream_chat_uses_kimi_sampling_parameters():
    provider = KimiProvider(api_key="sk-test")

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

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.6
    assert kwargs["top_p"] == 0.95
    assert "presence_penalty" not in kwargs
    assert "frequency_penalty" not in kwargs

    text_deltas = [e for e in events if e.type == "text_delta"]
    stops = [e for e in events if e.type == "stop"]
    assert len(text_deltas) == 2
    assert text_deltas[0].text == "H"
    assert text_deltas[1].text == "i"
    assert len(stops) == 1
    assert stops[0].stop_reason == "end_turn"
