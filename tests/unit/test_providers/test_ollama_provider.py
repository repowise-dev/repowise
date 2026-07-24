"""Unit tests for OllamaProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.ollama import OllamaProvider


def test_supported_reasoning_modes_are_auto_and_off():
    provider = OllamaProvider(model="test")

    assert provider.supported_reasoning_modes() == ("auto", "off")


def test_available_model_options_reads_local_tags(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "models": [
                    {
                        "name": "llama3.2:latest",
                        "details": {
                            "family": "llama",
                            "parameter_size": "3B",
                        },
                    },
                    {"model": "qwen2.5-coder:7b"},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    options = OllamaProvider(base_url="http://localhost:11434").available_model_options()

    assert captured["url"] == "http://localhost:11434/api/tags"
    model_names = [option.model for option in options]
    assert model_names == ["llama3.2:latest", "qwen2.5-coder:7b"]
    llama = options[0]
    assert llama.source == "local"
    assert llama.notes == "llama, 3B"
    assert llama.reasoning_modes == ("auto", "off")


def _make_mock_chat_response(text: str = "# Doc\nContent.") -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 120
    usage.completion_tokens = 60

    choice = MagicMock()
    choice.message.content = text

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


async def test_generate_auto_preserves_existing_request_shape():
    provider = OllamaProvider(model="test")
    provider._client.chat.completions.create = AsyncMock(return_value=_make_mock_chat_response())

    await provider.generate("system", "user", reasoning="auto")

    request_kwargs = provider._client.chat.completions.create.await_args.kwargs
    assert "reasoning_effort" not in request_kwargs


async def test_generate_off_disables_reasoning():
    provider = OllamaProvider(model="test")
    provider._client.chat.completions.create = AsyncMock(return_value=_make_mock_chat_response())

    await provider.generate("system", "user", reasoning="off")

    request_kwargs = provider._client.chat.completions.create.await_args.kwargs
    assert request_kwargs["reasoning_effort"] == "none"


@pytest.mark.parametrize("reasoning", ["none", "minimal", "low"])
async def test_generate_rejects_unsupported_reasoning_modes(reasoning):
    provider = OllamaProvider(model="test")
    provider._client.chat.completions.create = AsyncMock()

    with pytest.raises(ProviderError, match=f"reasoning='{reasoning}' is not supported"):
        await provider.generate("system", "user", reasoning=reasoning)

    provider._client.chat.completions.create.assert_not_called()


async def _assert_generate_wraps(exc: Exception) -> None:
    """Drive ``generate`` with a client that raises ``exc`` and assert the
    error is caught and re-raised as a provider-tagged ``ProviderError``.

    Shared by the #445 regression cases below: only ``APIStatusError`` used to
    be caught, so timeout/connection errors escaped uncaught, skipped tenacity
    (which retries ``ProviderError`` only), and surfaced as
    ``page_generation_failed``.
    """
    provider = OllamaProvider(model="test", base_url="http://localhost:9999")
    provider._rate_limiter = None
    provider._client.chat.completions.create = AsyncMock(side_effect=exc)
    with pytest.raises(ProviderError) as exc_info:
        await provider.generate(system_prompt="", user_prompt="", max_tokens=10)
    assert "ollama" in str(exc_info.value)


def _status_error() -> APIStatusError:
    resp = AsyncMock()
    resp.status_code = 500
    return APIStatusError("Internal Server Error", response=resp, body="")


def _connection_error() -> APIConnectionError:
    request = httpx.Request("POST", "http://localhost:9999/v1/chat/completions")
    return APIConnectionError(message="Connection refused", request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_exc",
    [
        pytest.param(_status_error, id="api_status_error"),
        pytest.param(lambda: APITimeoutError("Request timed out"), id="api_timeout_error"),
        pytest.param(_connection_error, id="api_connection_error"),
    ],
)
async def test_generate_wraps_openai_errors(make_exc):
    """Every ``openai.APIError`` subclass is wrapped as ``ProviderError`` (#445).

    ``APITimeoutError`` and ``APIConnectionError`` are NOT subclasses of
    ``APIStatusError``, so the old catch-only-``APIStatusError`` clause let them
    escape uncaught, skipping tenacity's retry loop entirely.
    """
    await _assert_generate_wraps(make_exc())


@pytest.mark.asyncio
async def test_all_openai_errors_are_subclasses_of_api_error():
    """Verify the class hierarchy: all OpenAI errors inherit from APIError."""
    resp = AsyncMock()
    resp.status_code = 500
    status_err = APIStatusError("err", response=resp, body="")
    timeout_err = APITimeoutError("timed out")
    request = httpx.Request("POST", "http://localhost:9999/v1/chat/completions")
    conn_err = APIConnectionError(message="refused", request=request)

    assert isinstance(status_err, APIError), "APIStatusError must be APIError"
    assert isinstance(timeout_err, APIError), "APITimeoutError must be APIError"
    assert isinstance(conn_err, APIError), "APIConnectionError must be APIError"
