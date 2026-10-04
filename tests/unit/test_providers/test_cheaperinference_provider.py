"""Unit tests for CheaperInferenceProvider.

All tests mock the AsyncOpenAI client and httpx. No real API calls are made.
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
from repowise.core.providers.llm.cheaperinference import CheaperInferenceProvider


def test_provider_name():
    p = CheaperInferenceProvider(api_key="test-key")
    assert p.provider_name == "cheaperinference"


def test_default_model_is_gpt_5_4_mini():
    p = CheaperInferenceProvider(api_key="test-key")
    assert p.model_name == "gpt-5.4-mini"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("CHEAPER_INFERENCE_API_KEY", "env-key")
    p = CheaperInferenceProvider()
    assert p.provider_name == "cheaperinference"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("CHEAPER_INFERENCE_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        CheaperInferenceProvider()


def test_default_base_url(monkeypatch):
    monkeypatch.delenv("CHEAPER_INFERENCE_BASE_URL", raising=False)
    p = CheaperInferenceProvider(api_key="test-key")
    assert p._base_url == "https://api.cheaperinference.com/v1"


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("CHEAPER_INFERENCE_BASE_URL", "https://proxy.example/v1/")
    p = CheaperInferenceProvider(api_key="test-key")
    assert p._base_url == "https://proxy.example/v1"


def test_exposes_only_auto_reasoning():
    p = CheaperInferenceProvider(api_key="test-key", model="claude-sonnet-5")
    assert p.supported_reasoning_modes() == ("auto",)


def test_available_model_options_lists_text_models_only(monkeypatch):
    monkeypatch.delenv("CHEAPER_INFERENCE_BASE_URL", raising=False)

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "gpt-5.4-mini", "type": "text"},
                    {"id": "claude-sonnet-5", "type": "text"},
                    {"id": "gpt-image-2", "type": "image"},
                    {"id": "veo-4", "type": "video"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = CheaperInferenceProvider(api_key="test-key").available_model_options()

    assert captured["url"] == "https://api.cheaperinference.com/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert [o.model for o in options] == ["claude-sonnet-5", "gpt-5.4-mini"]
    mini = next(o for o in options if o.model == "gpt-5.4-mini")
    assert mini.recommended is True
    assert mini.reasoning_modes == ("auto",)


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
    provider = CheaperInferenceProvider(api_key="test-key")
    mock_response = _make_mock_chat_response("Hello from Cheaper Inference")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        result = await provider.generate(
            system_prompt="You are a test assistant",
            user_prompt="Say hello",
        )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Cheaper Inference"
    assert result.input_tokens == 100
    assert result.output_tokens == 40


async def test_generate_uses_max_tokens_and_model():
    provider = CheaperInferenceProvider(api_key="test-key", model="deepseek-v4-flash")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value

        await provider.generate(system_prompt="system", user_prompt="user", max_tokens=512)

        kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "deepseek-v4-flash"
        assert kwargs["max_tokens"] == 512
        assert "reasoning_effort" not in kwargs


async def test_generate_rejects_explicit_reasoning():
    provider = CheaperInferenceProvider(api_key="test-key")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='high' is not supported"):
            await provider.generate("system", "user", reasoning="high")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_rate_limit_retry():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = CheaperInferenceProvider(api_key="test-key")

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


async def test_stream_chat_emits_text_delta_and_stop():
    provider = CheaperInferenceProvider(api_key="test-key")

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
    assert [e.text for e in text_deltas] == ["H", "i"]
    assert len(stops) == 1
    assert stops[0].stop_reason == "end_turn"
