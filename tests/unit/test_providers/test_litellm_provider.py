"""Unit tests for LiteLLMProvider.

All tests mock the litellm.acompletion call — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("litellm", reason="litellm SDK not installed")

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.litellm import LiteLLMProvider

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_name():
    p = LiteLLMProvider(model="gpt-4o", api_key="sk-test")
    assert p.provider_name == "litellm"


def test_default_model():
    p = LiteLLMProvider(model="groq/llama-3.1-70b-versatile", api_key="sk-test")
    assert p.model_name == "groq/llama-3.1-70b-versatile"


def test_model_without_api_base():
    """Without api_base, model should be passed through unchanged."""
    p = LiteLLMProvider(model="groq/llama-3.1-70b-versatile", api_key="sk-test")
    assert p._litellm_model == "groq/llama-3.1-70b-versatile"


def test_model_with_api_base_adds_openai_prefix():
    """With api_base (local proxy), model should get openai/ prefix."""
    p = LiteLLMProvider(
        model="zai.glm-5",
        api_base="http://localhost:4000/v1",
    )
    assert p._litellm_model == "openai/zai.glm-5"
    assert p.model_name == "zai.glm-5"  # Public property shows original name


def test_model_with_api_base_and_existing_prefix():
    """If model already has openai/ prefix, don't add another."""
    p = LiteLLMProvider(
        model="openai/gpt-4o",
        api_base="http://localhost:4000/v1",
    )
    assert p._litellm_model == "openai/gpt-4o"


def test_no_api_key_or_base():
    """Provider can be created without API key or base (for some backends)."""
    p = LiteLLMProvider(model="groq/llama-3.1-70b-versatile")
    assert p._api_key is None
    assert p._api_base is None


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


async def test_generate_returns_generated_response():
    provider = LiteLLMProvider(model="gpt-4o", api_key="sk-test")
    mock_response = _make_mock_response("Hello from LiteLLM")

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_response
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from LiteLLM"


async def test_generate_token_counts():
    provider = LiteLLMProvider(model="gpt-4o", api_key="sk-test")
    mock_response = _make_mock_response()

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_response
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 120
    assert result.output_tokens == 60


async def test_generate_sends_correct_kwargs():
    provider = LiteLLMProvider(
        model="groq/llama-3.1-70b-versatile",
        api_key="sk-test",
    )
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = fake_acompletion
        await provider.generate("system msg", "user msg", max_tokens=2048, temperature=0.5)

    kw = captured_kwargs[0]
    assert kw["model"] == "groq/llama-3.1-70b-versatile"
    assert kw["max_tokens"] == 2048
    assert kw["temperature"] == 0.5
    assert kw["api_key"] == "sk-test"
    messages = kw["messages"]
    assert messages[0] == {"role": "system", "content": "system msg"}
    assert messages[1] == {"role": "user", "content": "user msg"}


async def test_generate_with_api_base():
    """With api_base (local proxy), should pass api_base and dummy key."""
    provider = LiteLLMProvider(
        model="zai.glm-5",
        api_base="http://localhost:4000/v1",
    )
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = fake_acompletion
        await provider.generate("sys", "user")

    kw = captured_kwargs[0]
    # Model should have openai/ prefix for proxy routing
    assert kw["model"] == "openai/zai.glm-5"
    assert kw["api_base"] == "http://localhost:4000/v1"
    # Dummy key should be added when using api_base without api_key
    assert kw["api_key"] == "sk-dummy"


async def test_generate_with_api_base_and_api_key():
    """With both api_base and api_key, should use provided key."""
    provider = LiteLLMProvider(
        model="zai.glm-5",
        api_key="sk-real-key",
        api_base="http://localhost:4000/v1",
    )
    mock_response = _make_mock_response()
    captured_kwargs: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_response

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = fake_acompletion
        await provider.generate("sys", "user")

    kw = captured_kwargs[0]
    assert kw["api_key"] == "sk-real-key"
    assert kw["api_base"] == "http://localhost:4000/v1"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_api_error():
    import litellm

    provider = LiteLLMProvider(model="gpt-4o", api_key="sk-test")

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = litellm.APIError(
            message="server error",
            llm_provider="openai",
            model="gpt-4o",
            status_code=500,
        )
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")
