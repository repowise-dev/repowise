"""Unit tests for EdenAIProvider.

All tests mock the AsyncOpenAI client / httpx — no real API calls are made.
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
from repowise.core.providers.llm.edenai import EdenAIProvider


def test_provider_name():
    p = EdenAIProvider(api_key="test-key")
    assert p.provider_name == "edenai"


def test_default_model_is_mistral_small():
    p = EdenAIProvider(api_key="test-key")
    assert p.model_name == "mistral/mistral-small-latest"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("EDENAI_API_KEY", "env-key")
    p = EdenAIProvider()
    assert p.provider_name == "edenai"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("EDENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        EdenAIProvider()


def test_custom_model():
    p = EdenAIProvider(api_key="test-key", model="openai/gpt-4o-mini")
    assert p.model_name == "openai/gpt-4o-mini"


def test_default_base_url_is_global():
    p = EdenAIProvider(api_key="test-key")
    assert p._base_url == "https://api.edenai.run/v3"


def test_eu_base_url_from_env(monkeypatch):
    monkeypatch.setenv("EDENAI_BASE_URL", "https://api.eu.edenai.run/v3")
    p = EdenAIProvider(api_key="test-key")
    assert p._base_url == "https://api.eu.edenai.run/v3"


def test_non_reasoning_model_exposes_only_auto():
    p = EdenAIProvider(api_key="test-key", model="mistral/mistral-small-latest")
    assert p.supported_reasoning_modes() == ("auto",)


def test_openai_reasoning_model_exposes_efforts():
    p = EdenAIProvider(api_key="test-key", model="openai/gpt-5-mini")
    assert p.supported_reasoning_modes() == ("auto", "minimal", "low", "medium", "high")


def test_available_model_options_uses_models_endpoint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "mistral/mistral-small-latest"},
                    {"id": "openai/gpt-5-mini"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = EdenAIProvider(api_key="test-key").available_model_options()

    assert captured["url"] == "https://api.edenai.run/v3/models"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    mistral = next(o for o in options if o.model == "mistral/mistral-small-latest")
    assert mistral.recommended is True
    assert mistral.reasoning_modes == ("auto",)
    gpt5 = next(o for o in options if o.model == "openai/gpt-5-mini")
    assert gpt5.reasoning_modes == ("auto", "minimal", "low", "medium", "high")


def _make_mock_chat_response(text: str = "# Doc\nContent.") -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 40
    usage.total_tokens = 140

    choice = MagicMock()
    choice.message.content = text

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
    provider = EdenAIProvider(api_key="test-key")
    mock_response = _make_mock_chat_response("Hello from Eden AI")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(
            system_prompt="You are a test assistant",
            user_prompt="Say hello",
        )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Eden AI"
    assert result.input_tokens == 100
    assert result.output_tokens == 40


async def test_generate_uses_max_tokens_and_model():
    provider = EdenAIProvider(api_key="test-key", model="mistral/mistral-small-latest")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(system_prompt="system", user_prompt="user", max_tokens=512)

        kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "mistral/mistral-small-latest"
        assert kwargs["max_tokens"] == 512
        # No reasoning_effort for a non-reasoning model on an "auto" request.
        assert "reasoning_effort" not in kwargs


async def test_generate_forwards_reasoning_effort_for_openai_model():
    provider = EdenAIProvider(api_key="test-key", model="openai/gpt-5-mini")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        await provider.generate("system", "user", reasoning="low")

    kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["reasoning_effort"] == "low"


async def test_generate_rejects_reasoning_for_non_reasoning_model():
    provider = EdenAIProvider(api_key="test-key", model="mistral/mistral-small-latest")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='high' is not supported"):
            await provider.generate("system", "user", reasoning="high")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_rate_limit_retry():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = EdenAIProvider(api_key="test-key")

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

    provider = EdenAIProvider(api_key="test-key")

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

    provider = EdenAIProvider(api_key="test-key", cost_tracker=mock_tracker)
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(system_prompt="system", user_prompt="user")

    mock_tracker.record.assert_called_once()
    call_kwargs = mock_tracker.record.call_args.kwargs
    assert call_kwargs["model"] == "mistral/mistral-small-latest"
    assert call_kwargs["input_tokens"] == 100
    assert call_kwargs["output_tokens"] == 40


async def test_stream_chat_emits_text_delta_and_stop():
    provider = EdenAIProvider(api_key="test-key")

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
