"""Unit tests for the AnthropicProvider client.

Verifies that the Anthropic client correctly constructs payloads, executes requests,
handles responses, and raises appropriate provider exceptions under network failures.
Uses respx to intercept HTTP requests.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from repowise.core.providers.llm.anthropic import AnthropicProvider
from repowise.core.providers.llm.base import GeneratedResponse, ProviderError, RateLimitError
import repowise.core.providers.llm.base

# Scale down the tenacity wait multiplier so that retries execute immediately during tests.
repowise.core.providers.llm.base._WAIT_SCALE = 0.0


@pytest.mark.anyio
async def test_anthropic_generate_success() -> None:
    provider = AnthropicProvider(api_key="test-api-key", model="claude-sonnet-4-6")

    # Mock the /v1/messages endpoint
    with respx.mock(base_url="https://api.anthropic.com") as respx_mock:
        route = respx_mock.post("/v1/messages").mock(
            Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Hello, this is Claude!"}],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 8,
                        "cache_creation_input_tokens": 0,
                    },
                },
            )
        )

        response = await provider.generate(
            system_prompt="You are a helper.",
            user_prompt="Say hello.",
        )

        # Assert response properties are parsed and mapped correctly
        assert isinstance(response, GeneratedResponse)
        assert response.content == "Hello, this is Claude!"
        assert response.input_tokens == 12
        assert response.output_tokens == 20
        assert response.cached_tokens == 8
        assert response.total_tokens == 32
        assert response.usage["cache_read_input_tokens"] == 8

        # Verify request structure
        assert route.called
        last_request = route.calls.last.request
        import json
        req_body = json.loads(last_request.read().decode())
        assert req_body["model"] == "claude-sonnet-4-6"
        assert req_body["system"] == "You are a helper."
        assert req_body["messages"] == [{"role": "user", "content": "Say hello."}]


@pytest.mark.anyio
async def test_anthropic_generate_rate_limit_error() -> None:
    provider = AnthropicProvider(api_key="test-api-key")
    provider._client.max_retries = 0

    with respx.mock(base_url="https://api.anthropic.com") as respx_mock:
        respx_mock.post("/v1/messages").mock(
            Response(
                429,
                json={
                    "error": {
                        "type": "rate_limit_error",
                        "message": "Too many requests",
                    }
                },
                headers={"retry-after": "10"},
            )
        )

        # Retries are managed by tenacity; we check if the custom RateLimitError exception is raised
        with pytest.raises(RateLimitError) as exc_info:
            await provider._generate_with_retry(
                system_prompt="sys",
                user_prompt="user",
                max_tokens=100,
                temperature=0.5,
                request_id=None,
            )

        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 10.0


def test_anthropic_available_model_options_success() -> None:
    provider = AnthropicProvider(api_key="test-api-key")

    with respx.mock(base_url="https://api.anthropic.com") as respx_mock:
        route = respx_mock.get("/v1/models").mock(
            Response(
                200,
                json={
                    "data": [
                        {"id": "claude-haiku-4-5", "display_name": "Claude Haiku"},
                        {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet"},
                    ]
                },
            )
        )

        options = provider.available_model_options()

        assert len(options) == 2
        assert options[0].model == "claude-haiku-4-5"
        assert options[0].label == "Claude Haiku"
        assert options[1].model == "claude-sonnet-4-6"
        assert options[1].label == "Claude Sonnet"
        assert route.called
