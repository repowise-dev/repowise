"""Unit tests for MiniMaxProvider.

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
from repowise.core.providers.llm.minimax import MiniMaxProvider, _minimax_temperature


def test_provider_name():
    p = MiniMaxProvider(api_key="sk-test")
    assert p.provider_name == "minimax"


def test_default_model_is_m3():
    p = MiniMaxProvider(api_key="sk-test")
    assert p.model_name == "MiniMax-M3"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-env-test")
    p = MiniMaxProvider()
    assert p.provider_name == "minimax"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        MiniMaxProvider()


def test_custom_model():
    p = MiniMaxProvider(api_key="sk-test", model="MiniMax-M2.7")
    assert p.model_name == "MiniMax-M2.7"


def test_default_base_url_is_global(monkeypatch):
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    p = MiniMaxProvider(api_key="sk-test")
    assert p._base_url == "https://api.minimax.io/v1"


def test_cn_base_url_from_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    p = MiniMaxProvider(api_key="sk-test")
    assert p._base_url == "https://api.minimaxi.com/v1"


def test_explicit_base_url_wins_over_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    p = MiniMaxProvider(api_key="sk-test", base_url="https://api.minimax.io/v1")
    assert p._base_url == "https://api.minimax.io/v1"


def test_m3_supports_adaptive_and_disabled_thinking():
    """M3 keeps adaptive (auto) and can be disabled (off/none)."""
    p = MiniMaxProvider(api_key="sk-test", model="MiniMax-M3")
    assert p.supported_reasoning_modes() == ("auto", "off", "none")


def test_m27_is_always_on_and_offers_auto_only():
    """M2.7 thinks always-on: no explicit toggle, auto only."""
    p = MiniMaxProvider(api_key="sk-test", model="MiniMax-M2.7")
    assert p.supported_reasoning_modes() == ("auto",)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.0, 0.01),
        (-1.0, 0.01),
        (0.3, 0.3),
        (1.0, 1.0),
        (1.5, 1.0),
    ],
)
def test_temperature_is_clamped_to_minimax_range(raw, expected):
    assert _minimax_temperature(raw) == expected


def test_available_model_options_uses_models_endpoint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "MiniMax-M3"},
                    {"id": "MiniMax-M2.7"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = MiniMaxProvider(api_key="sk-test").available_model_options()

    assert captured["url"] == "https://api.minimax.io/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer sk-test"}
    m3 = next(option for option in options if option.model == "MiniMax-M3")
    assert m3.reasoning_modes == ("auto", "off", "none")
    assert m3.recommended is True
    m27 = next(option for option in options if option.model == "MiniMax-M2.7")
    assert m27.reasoning_modes == ("auto",)


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
    provider = MiniMaxProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response("Hello from MiniMax")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(
            system_prompt="You are a test assistant",
            user_prompt="Say hello",
        )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from MiniMax"
    assert result.input_tokens == 120
    assert result.output_tokens == 60
    assert result.stop_reason == "end_turn"
    assert result.provider_stop_reason == "stop"


async def test_generate_uses_correct_model_name():
    provider = MiniMaxProvider(api_key="sk-test", model="MiniMax-M3")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(system_prompt="system", user_prompt="user")

        kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "MiniMax-M3"


async def test_generate_clamps_temperature_into_range():
    provider = MiniMaxProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        await provider.generate("system", "user", temperature=0.0)

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.01


async def test_adaptive_auto_sends_no_thinking_override():
    provider = MiniMaxProvider(api_key="sk-test", model="MiniMax-M3")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        await provider.generate("system", "user", reasoning="auto")

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs


async def test_m3_disabled_thinking_is_forwarded():
    provider = MiniMaxProvider(api_key="sk-test", model="MiniMax-M3")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        await provider.generate("system", "user", reasoning="off")

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_m27_rejects_disabling_its_always_on_thinking():
    provider = MiniMaxProvider(api_key="sk-test", model="MiniMax-M2.7")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='off' is not supported"):
            await provider.generate("system", "user", reasoning="off")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_rate_limit_retry():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = MiniMaxProvider(api_key="sk-test")

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
            await provider.generate(system_prompt="system", user_prompt="user")


async def test_generate_api_error():
    from openai import APIStatusError as _OpenAIAPIStatusError

    provider = MiniMaxProvider(api_key="sk-test")

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
            await provider.generate(system_prompt="system", user_prompt="user")
        assert excinfo.value.status_code == 500


async def test_cost_tracker_called():
    from repowise.core.generation.cost_tracker import CostTracker

    mock_tracker = MagicMock(spec=CostTracker)
    mock_tracker.record = AsyncMock(return_value=0.0)

    provider = MiniMaxProvider(api_key="sk-test", cost_tracker=mock_tracker)
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(system_prompt="system", user_prompt="user")

    mock_tracker.record.assert_called_once()
    call_kwargs = mock_tracker.record.call_args.kwargs
    assert call_kwargs["model"] == "MiniMax-M3"
    assert call_kwargs["input_tokens"] == 120
    assert call_kwargs["output_tokens"] == 60


async def test_stream_chat_emits_text_delta_and_stop():
    provider = MiniMaxProvider(api_key="sk-test")

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
