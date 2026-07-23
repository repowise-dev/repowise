"""Integration test for Issue #445: APITimeoutError retry behavior.

Tests the full chain: OllamaProvider._generate_with_retry → tenacity retry
→ generate(). Verifies that APITimeoutError is wrapped as ProviderError
and that tenacity actually retries (not just catches).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_ollama_retries_on_timeout():
    """Verify tenacity retries N times before giving up on APITimeoutError.

    With the fix, APITimeoutError gets caught, wrapped as ProviderError,
    and retried by tenacity up to _MAX_RETRIES (3) times.
    """
    p = OllamaProvider(model="test-model", base_url="http://localhost:9999")
    p._rate_limiter = None

    # Track how many times the client is called
    call_count = 0

    async def timeout_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise APITimeoutError(f"Request timed out (attempt {call_count})")

    p._client.chat.completions.create = AsyncMock(side_effect=timeout_side_effect)

    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)

    # Tenacity should have retried _MAX_RETRIES times (= 3)
    assert call_count == 3, (
        f"Expected 3 retries before ProviderError, got {call_count}. Fix may not be working."
    )
    assert "ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_retries_on_connection_error():
    """Same as above but with APIConnectionError."""
    p = OllamaProvider(model="test-model", base_url="http://localhost:9999")
    p._rate_limiter = None

    call_count = 0

    async def conn_error_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        request = httpx.Request("POST", "http://localhost:9999/v1/chat/completions")
        raise APIConnectionError(
            message=f"Connection refused (attempt {call_count})",
            request=request,
        )

    p._client.chat.completions.create = AsyncMock(side_effect=conn_error_side_effect)

    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)

    assert call_count == 3, (
        f"Expected 3 retries before ProviderError, got {call_count}. Fix may not be working."
    )
    assert "ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_succeeds_on_retry():
    """Verify that after a timeout failure, the retry succeeds and returns a result.

    This mimics the real-world scenario: Ollama is overloaded, first request
    times out, but the retry succeeds.
    """
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    from openai.types.completion_usage import CompletionUsage

    p = OllamaProvider(model="test-model", base_url="http://localhost:9999")
    p._rate_limiter = None

    call_count = 0

    async def flaky_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise APITimeoutError("First attempt timed out")
        # Second attempt succeeds
        return ChatCompletion(
            id="test",
            model="test-model",
            object="chat.completion",
            created=1234567890,
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(
                        role="assistant",
                        content="Generated wiki content",
                    ),
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            ),
        )

    p._client.chat.completions.create = AsyncMock(side_effect=flaky_side_effect)

    result = await p.generate(system_prompt="", user_prompt="Test", max_tokens=10)

    assert call_count == 2, f"Expected 2 calls (1 fail + 1 retry), got {call_count}"
    assert result.content == "Generated wiki content"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.stop_reason == "end_turn"
    assert result.provider_stop_reason == "stop"


@pytest.mark.asyncio
async def test_ollama_does_not_retry_invalid_request():
    """Verify 400 (invalid request) fails immediately without retries.

    Issue #445 point 3: non-retryable errors must fail fast. The shared
    retry policy treats 400/401/403/404/405/413/422 as non-retryable, so
    the client is called exactly once.
    """
    p = OllamaProvider(model="test-model", base_url="http://localhost:9999")
    p._rate_limiter = None

    call_count = 0

    async def bad_request_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = httpx.Response(
            400,
            request=httpx.Request("POST", "http://localhost:9999/v1/chat/completions"),
        )
        raise APIStatusError("Bad Request", response=resp, body=None)

    p._client.chat.completions.create = AsyncMock(side_effect=bad_request_side_effect)

    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)

    assert call_count == 1, (
        f"Non-retryable 400 must fail immediately, but was called {call_count} times."
    )
    assert "ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_does_not_retry_authentication_error():
    """Verify 401 (authentication) fails immediately without retries.

    Issue #445 point 3: authentication errors must not burn retry attempts.
    """
    p = OllamaProvider(model="test-model", base_url="http://localhost:9999")
    p._rate_limiter = None

    call_count = 0

    async def auth_error_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = httpx.Response(
            401,
            request=httpx.Request("POST", "http://localhost:9999/v1/chat/completions"),
        )
        raise AuthenticationError("Unauthorized", response=resp, body=None)

    p._client.chat.completions.create = AsyncMock(side_effect=auth_error_side_effect)

    with pytest.raises(ProviderError) as exc_info:
        await p.generate(system_prompt="", user_prompt="", max_tokens=10)

    assert call_count == 1, (
        f"Authentication error must fail immediately, but was called {call_count} times."
    )
    assert "ollama" in str(exc_info.value)
