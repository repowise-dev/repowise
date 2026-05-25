"""Unit tests for GeminiProvider.

All tests mock google.genai.Client — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.genai", reason="google-genai SDK not installed")

from repowise.core.providers.llm import gemini as gemini_module
from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
from repowise.core.providers.llm.gemini import GeminiProvider

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


def test_available_model_options_lists_generate_content_models(monkeypatch):
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return self._payload

    calls: list[dict[str, object]] = []

    def fake_get(url, *, headers, params, timeout):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        return FakeResponse(
            {
                "models": [
                    {
                        "name": "models/gemini-3.1-flash-lite-preview",
                        "displayName": "Gemini Flash Lite",
                        "supportedGenerationMethods": ["generateContent"],
                        "thinking": True,
                    },
                    {
                        "name": "models/gemini-embed",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }
        )

    monkeypatch.setattr("httpx.get", fake_get)

    options = GeminiProvider(api_key="k").available_model_options()

    assert calls[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert calls[0]["headers"] == {"x-goog-api-key": "k"}
    models = [option.model for option in options]
    assert "gemini-3.1-flash-lite-preview" in models
    assert "gemini-embed" not in models
    option = next(option for option in options if option.model == "gemini-3.1-flash-lite-preview")
    assert option.label == "Gemini Flash Lite"
    assert option.reasoning_modes == ("auto", "minimal", "low", "medium", "high")
    assert GeminiProvider(api_key="k").supported_reasoning_modes() == (
        "auto",
        "minimal",
        "low",
        "medium",
        "high",
    )


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

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.return_value = mock_response
        result = await provider.generate("sys", "user")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello world"


async def test_generate_token_counts():
    provider = GeminiProvider(api_key="fake-key")
    mock_response = _make_mock_response()

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.return_value = mock_response
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

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.side_effect = fake_generate_content
        await provider.generate("sys", "user", max_tokens=1234)

    # max_output_tokens intentionally omitted — Gemini flash models default to 65k
    assert captured[0].max_output_tokens is None


async def test_generate_forwards_thinking_level():
    gemini_module._GEMINI_THINKING_MODELS_BY_BASE[gemini_module._gemini_cache_key(None)] = {
        "gemini-3.1-flash-lite-preview"
    }
    provider = GeminiProvider(api_key="fake-key")
    mock_response = _make_mock_response()
    captured: list = []

    def fake_generate_content(model, contents, config):
        captured.append(config)
        return mock_response

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.side_effect = fake_generate_content
        await provider.generate("sys", "user", reasoning="high")

    assert captured[0].thinking_config.thinking_level.value == "HIGH"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_rate_limit_error_on_429():
    provider = GeminiProvider(api_key="fake-key")

    class FakeRateLimitError(Exception):
        status_code = 429

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.side_effect = FakeRateLimitError(
            "quota exceeded"
        )
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_rate_limit_error_on_quota_message():
    provider = GeminiProvider(api_key="fake-key")

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.side_effect = Exception(
            "quota exceeded for project"
        )
        with pytest.raises(RateLimitError):
            await provider.generate("sys", "user")


async def test_api_error_on_generic_exception():
    provider = GeminiProvider(api_key="fake-key")

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.side_effect = Exception(
            "internal server error"
        )
        with pytest.raises(ProviderError):
            await provider.generate("sys", "user")


async def test_provider_error_message_includes_exception_type():
    provider = GeminiProvider(api_key="fake-key")

    class CustomError(Exception):
        pass

    with patch("google.genai.Client") as mock_client:
        mock_client.return_value.models.generate_content.side_effect = CustomError("bad request")
        with pytest.raises(ProviderError, match="CustomError"):
            await provider.generate("sys", "user")
