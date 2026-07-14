"""Unit tests for OllamaProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.ollama import OllamaProvider


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
    assert llama.reasoning_modes == ("auto",)


@pytest.mark.asyncio
async def test_generate_wraps_api_status_error():
    """APIStatusError (HTTP 4xx/5xx) is caught and wrapped as ProviderError."""
    p = OllamaProvider(model="test", base_url="http://localhost:9999")
    p._rate_limiter = None
    resp = AsyncMock()
    resp.status_code = 500
    p._client.chat.completions.create = AsyncMock(
        side_effect=APIStatusError("Internal Server Error", response=resp, body=""),
    )
    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)
    assert "ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_generate_wraps_api_timeout_error():
    """APITimeoutError (request timed out) is caught and wrapped as ProviderError.

    Regression test for issue #445: only APIStatusError was caught, so
    APITimeoutError escaped the except clause and was never wrapped as
    ProviderError. That meant tenacity never retried it (retry only fires
    on ProviderError), and the raw exception became page_generation_failed.
    """
    p = OllamaProvider(model="test", base_url="http://localhost:9999")
    p._rate_limiter = None
    p._client.chat.completions.create = AsyncMock(
        side_effect=APITimeoutError("Request timed out"),
    )
    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)
    assert "ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_generate_wraps_api_connection_error():
    """APIConnectionError (connection refused) is caught and wrapped as ProviderError.

    Same root cause as #445: APIConnectionError is NOT a subclass of
    APIStatusError, so it was escaping uncaught alongside APITimeoutError.
    """
    p = OllamaProvider(model="test", base_url="http://localhost:9999")
    p._rate_limiter = None
    request = httpx.Request("POST", "http://localhost:9999/v1/chat/completions")
    p._client.chat.completions.create = AsyncMock(
        side_effect=APIConnectionError(message="Connection refused", request=request),
    )
    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)
    assert "ollama" in str(exc_info.value)


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
