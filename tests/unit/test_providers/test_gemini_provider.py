"""Unit tests for GeminiProvider.

All tests mock google.genai.Client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from repowise.core.providers.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.gemini import GeminiProvider


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_provider_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="No API key found"):
        GeminiProvider(api_key=None)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    p = GeminiProvider()
    assert p.provider_name == "gemini"


def test_google_api_key_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    p = GeminiProvider()
    assert p._api_key == "google-key"


def test_provider_name():
    p = GeminiProvider(api_key="k")
    assert p.provider_name == "gemini"


def test_model_name_default():
    p = GeminiProvider(api_key="k")
    assert p.model_name == "gemini-3.1-flash-lite-preview"


def test_model_name_custom():
    p = GeminiProvider(model="gemini-3-flash-preview", api_key="k")
    assert p.model_name == "gemini-3-flash-preview"


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


def _make_mock_response(text: str = "# Doc\nContent here.") -> MagicMock:
    usage = MagicMock()
    usage.prompt_token_count = 100
    usage.candidates_token_count = 50
    usage.cached_content_token_count = 0
    usage.total_token_count = 150

    response = MagicMock()
    response.text = text
    response.usage_metadata = usage
    return response


async def test_generate_returns_generated_response():
    provider = GeminiProvider(api_key="fake-key")
    mock_response = _make_mock_response("Hello world")

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.return_value = mock_response
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello world"


async def test_generate_token_counts():
    provider = GeminiProvider(api_key="fake-key")
    mock_response = _make_mock_response()

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.return_value = mock_response
        result = await provider.generate("sys", "user")

    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cached_tokens == 0


async def test_generate_passes_max_tokens():
    """max_output_tokens is intentionally omitted in the Gemini config
    (flash models default to 65k which is better for doc generation).
    Verify the config is created but max_output_tokens is not set."""
    provider = GeminiProvider(api_key="fake-key")
    mock_response = _make_mock_response()
    captured: list = []

    def fake_generate_content(model, contents, config):
        captured.append(config)
        return mock_response

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.side_effect = fake_generate_content
        await provider.generate("sys", "user", max_tokens=1234)

    # max_output_tokens intentionally omitted — Gemini flash models default to 65k
    assert captured[0].max_output_tokens is None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_rate_limit_error_on_429():
    provider = GeminiProvider(api_key="fake-key")

    class FakeRateLimit(Exception):
        status_code = 429

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.side_effect = FakeRateLimit("quota exceeded")
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_rate_limit_error_on_quota_message():
    provider = GeminiProvider(api_key="fake-key")

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.side_effect = Exception("quota exceeded for project")
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_api_error_on_generic_exception():
    provider = GeminiProvider(api_key="fake-key")

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.side_effect = Exception("internal server error")
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")


async def test_provider_error_message_includes_exception_type():
    provider = GeminiProvider(api_key="fake-key")

    class CustomError(Exception):
        pass

    with patch("google.genai.Client") as MockClient:
        MockClient.return_value.models.generate_content.side_effect = CustomError("bad request")
        with pytest.raises(ProviderError, match="CustomError"):
            await provider.generate("sys", "user")
