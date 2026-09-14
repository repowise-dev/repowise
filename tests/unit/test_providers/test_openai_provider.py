"""Unit tests for OpenAIProvider.

All tests mock the AsyncOpenAI client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.llm.openai import OpenAIProvider

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = OpenAIProvider(api_key="sk-test")
    assert p.provider_name == "openai"


def test_default_model_is_luna():
    p = OpenAIProvider(api_key="sk-test")
    assert p.model_name == "gpt-5.6-luna"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-test")
    p = OpenAIProvider()
    assert p.provider_name == "openai"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        OpenAIProvider()


def test_custom_model():
    p = OpenAIProvider(api_key="sk-test", model="gpt-5.4-mini")
    assert p.model_name == "gpt-5.4-mini"


def test_supported_reasoning_modes_are_model_specific():
    assert OpenAIProvider(
        api_key="sk-test",
        model="gpt-5-mini",
    ).supported_reasoning_modes() == ("auto", "minimal", "low", "medium", "high")
    assert OpenAIProvider(
        api_key="sk-test",
        model="qwen3",
    ).supported_reasoning_modes() == ("auto", "off", "none")
    assert OpenAIProvider(
        api_key="sk-test",
        model="gpt-4o",
    ).supported_reasoning_modes() == ("auto",)


def test_gpt56_family_ladder_drops_minimal_and_max():
    """Pin the 5.6 ladder: it is neither the gpt-5 one nor what the docs say.

    Verified against the live API on 2026-08-15 for both variants. The model
    docs advertise `max`; the API rejects it with `unsupported_value` and names
    exactly these five. `minimal` is gone from 5.6 entirely, and offering it
    fails only on a real call, which is what this pins.
    """
    for model in ("gpt-5.6-luna", "gpt-5.6-sol"):
        assert OpenAIProvider(
            api_key="sk-test",
            model=model,
        ).supported_reasoning_modes() == ("auto", "none", "low", "medium", "high", "xhigh")

    # The generic gpt-5 branch is a prefix of gpt-5.6 and must not win.
    assert (
        "minimal"
        in OpenAIProvider(
            api_key="sk-test",
            model="gpt-5.4-nano",
        ).supported_reasoning_modes()
    )


def test_gpt54_model():
    p = OpenAIProvider(api_key="sk-test", model="gpt-5.4")
    assert p.model_name == "gpt-5.4"


def test_available_model_options_uses_models_endpoint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "gpt-5.4-nano"},
                    {"id": "gpt-4.1"},
                    {"id": "text-embedding-3-large"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = OpenAIProvider(api_key="sk-test").available_model_options()

    assert captured["url"] == "https://api.openai.com/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer sk-test"}
    assert "gpt-5.4-nano" in [option.model for option in options]
    assert "text-embedding-3-large" not in [option.model for option in options]
    assert next(option for option in options if option.model == "gpt-5.4-nano").reasoning_modes == (
        "auto",
        "minimal",
        "low",
        "medium",
        "high",
    )


def test_available_model_options_accepts_namespaced_compatible_models(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "ag/gemini-3.7-flash-medium"},
                    {"id": "ds/deepseek-v4-flash"},
                    {"id": "cmc/moonshotai/Kimi-K2.6"},
                    {"id": "text-embedding-3-large"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = OpenAIProvider(
        api_key="sk-test",
        base_url="http://localhost:20128/v1",
    ).available_model_options()
    models = [option.model for option in options]

    assert models == [
        "ag/gemini-3.7-flash-medium",
        "cmc/moonshotai/Kimi-K2.6",
        "ds/deepseek-v4-flash",
    ]
    assert captured["trust_env"] is False
    assert next(
        option for option in options if option.model == "ag/gemini-3.7-flash-medium"
    ).reasoning_modes == ("auto",)


def test_available_model_options_falls_back_to_configured_model(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("httpx.get", fake_get)

    options = OpenAIProvider(api_key="sk-test").available_model_options()

    assert options[0].model == "gpt-5.6-luna"
    assert options[0].recommended is True
    assert options[0].source == "fallback"


def test_discover_model_options_surfaces_endpoint_errors(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise RuntimeError("gateway unavailable")

    monkeypatch.setattr("httpx.get", fake_get)

    provider = OpenAIProvider(
        api_key="sk-test",
        base_url="http://localhost:20128/v1",
    )
    with pytest.raises(RuntimeError, match="gateway unavailable"):
        provider.discover_model_options()


def test_discover_model_options_rejects_malformed_payload(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"models": []}

    monkeypatch.setattr("httpx.get", lambda *_a, **_k: FakeResponse())

    provider = OpenAIProvider(
        api_key="sk-test",
        base_url="http://localhost:20128/v1",
    )
    with pytest.raises(ValueError, match="data list"):
        provider.discover_model_options()


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


def _make_mock_chat_response(
    text: str = "# Doc\nContent.",
    *,
    finish_reason: str = "stop",
) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 120
    usage.completion_tokens = 60
    usage.total_tokens = 180
    usage.prompt_tokens_details = MagicMock(cached_tokens=0)

    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


async def test_generate_returns_generated_response():
    provider = OpenAIProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response("Hello from OpenAI")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from OpenAI"
    assert result.stop_reason == "end_turn"
    assert result.provider_stop_reason == "stop"


async def test_generate_maps_length_to_token_limit():
    provider = OpenAIProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response(finish_reason="length")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        result = await provider.generate("sys", "user")

    assert result.stop_reason == "max_tokens"
    assert result.provider_stop_reason == "length"


async def test_generate_token_counts():
    provider = OpenAIProvider(api_key="sk-test")
    mock_response = _make_mock_chat_response()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client.return_value
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 60
    assert result.cached_tokens == 0


async def test_generate_sends_correct_messages():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", max_tokens=2048, temperature=0.5)

    kw = captured_kwargs[0]
    assert kw["model"] == "gpt-4o"
    assert kw["max_completion_tokens"] == 2048
    assert kw["temperature"] == 0.5
    assert "reasoning_effort" not in kw
    assert "extra_body" not in kw
    messages = kw["messages"]
    assert messages[0] == {"role": "system", "content": "system msg"}
    assert messages[1] == {"role": "user", "content": "user msg"}


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5-mini", "o3", "o1", "o4-mini"])
async def test_generate_clamps_temperature_for_reasoning_models(model):
    """GPT-5+ / o-series models only accept the default temperature of 1."""
    provider = OpenAIProvider(api_key="sk-test", model=model)
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", temperature=0.3)

    assert captured_kwargs[0]["temperature"] == 1.0


async def test_generate_forwards_minimal_reasoning_effort():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5-mini")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="minimal")

    assert captured_kwargs[0]["reasoning_effort"] == "minimal"


async def test_generate_forwards_xhigh_reasoning_effort_for_luna():
    """The top effort 5.6 does accept must reach the API, not be dropped.

    The mapping used to enumerate the efforts it forwarded, so an effort that
    passed validation but was missing from that list produced a request with no
    `reasoning_effort` at all, indistinguishable from success at the call site.
    """
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="xhigh")

    assert captured_kwargs[0]["reasoning_effort"] == "xhigh"


async def test_generate_rejects_max_for_luna():
    """`max` is a valid repowise mode that no OpenAI model accepts."""
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='max' is not supported"):
            await provider.generate("system msg", "user msg", reasoning="max")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_forwards_none_reasoning_effort_for_gpt51():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.1")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="none")

    assert captured_kwargs[0]["reasoning_effort"] == "none"


async def test_generate_forwards_off_reasoning_extra_body():
    provider = OpenAIProvider(api_key="sk-test", model="qwen3")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="off")

    assert captured_kwargs[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


async def test_generate_forwards_none_reasoning_as_thinking_disabled():
    provider = OpenAIProvider(api_key="sk-test", model="qwen3")
    mock_response = _make_mock_chat_response()
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        await provider.generate("system msg", "user msg", reasoning="none")

    assert captured_kwargs[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


async def test_generate_rejects_minimal_for_non_reasoning_model():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='minimal' is not supported"):
            await provider.generate("system msg", "user msg", reasoning="minimal")

    mock_client.return_value.chat.completions.create.assert_not_called()


@pytest.mark.parametrize(
    "model",
    ["gpt-5.1", "gpt-5-pro", "gpt-5.6-luna", "gpt-5.6-sol"],
)
async def test_generate_rejects_minimal_for_known_unsupported_reasoning_models(model):
    provider = OpenAIProvider(api_key="sk-test", model=model)

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='minimal' is not supported"):
            await provider.generate("system msg", "user msg", reasoning="minimal")

    mock_client.return_value.chat.completions.create.assert_not_called()


async def test_generate_rejects_off_for_non_qwen_model():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5-mini")

    with patch("openai.AsyncOpenAI") as mock_client:
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError, match="reasoning='off' is not supported"):
            await provider.generate("system msg", "user msg", reasoning="off")

    mock_client.return_value.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# Streaming chat compatibility
# ---------------------------------------------------------------------------


class _EmptyChatStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "openai/gpt-5.6-sol-2026-08-01",
    ],
)
async def test_stream_chat_disables_gpt_5_6_reasoning_with_function_tools(model):
    """Use the Chat Completions-compatible setting across the GPT-5.6 family."""
    provider = OpenAIProvider(api_key="sk-test", model=model)
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _EmptyChatStream()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        events = [
            event
            async for event in provider.stream_chat(
                messages=[{"role": "user", "content": "Inspect this repository"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_overview",
                            "description": "Read repository overview",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                system_prompt="Use repository tools.",
            )
        ]

    assert events == []
    assert captured_kwargs[0]["reasoning_effort"] == "none"
    assert captured_kwargs[0]["tools"][0]["function"]["name"] == "get_overview"


@pytest.mark.parametrize("model", ["gpt-5.6", "gpt-5.6-terra", "gpt-5.6-luna"])
async def test_stream_chat_keeps_gpt_5_6_default_reasoning_without_tools(model):
    provider = OpenAIProvider(api_key="sk-test", model=model)
    captured_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _EmptyChatStream()

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = fake_create
        provider._client = mock_client.return_value
        events = [
            event
            async for event in provider.stream_chat(
                messages=[{"role": "user", "content": "Say hello"}],
                tools=[],
                system_prompt="Be concise.",
            )
        ]

    assert events == []
    assert "reasoning_effort" not in captured_kwargs[0]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_rate_limit_error():
    from openai import RateLimitError as _OpenAIRateLimitError

    provider = OpenAIProvider(api_key="sk-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIRateLimitError(
                "rate limit", response=MagicMock(status_code=429), body={}
            )
        )
        provider._client = mock_client.return_value
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_api_status_error():
    from openai import APIStatusError as _OpenAIAPIStatusError

    provider = OpenAIProvider(api_key="sk-test")

    with patch("openai.AsyncOpenAI") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=_OpenAIAPIStatusError(
                "server error", response=MagicMock(status_code=500), body={}
            )
        )
        provider._client = mock_client.return_value
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")
