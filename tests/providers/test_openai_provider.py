"""Unit tests for the OpenAIProvider client.

Verifies that the OpenAI client correctly constructs payloads, executes requests,
handles responses, handles rate limiting, cost tracking, and streaming, and raises
appropriate provider exceptions under network or status failures.
Uses respx to intercept HTTP requests.
"""

from __future__ import annotations

import os
import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock

from repowise.core.providers.llm.openai import OpenAIProvider
from repowise.core.providers.llm.base import (
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)
import repowise.core.providers.llm.base

# Scale down the tenacity wait multiplier so that retries execute immediately during tests.
repowise.core.providers.llm.base._WAIT_SCALE = 0.0


from repowise.core.generation.cost_tracker import CostTracker

class MockCostTracker(CostTracker):
    def __init__(self, should_fail: bool = False) -> None:
        super().__init__()
        self.should_fail = should_fail
        self.calls: list[dict] = []

    async def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str,
        file_path: str | None = None,
    ) -> float:
        self.calls.append({
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "operation": operation,
            "file_path": file_path,
        })
        if self.should_fail:
            raise Exception("Cost tracker database failure")
        return 0.0


from repowise.core.rate_limiter import RateLimiter, RateLimitConfig

class MockRateLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(RateLimitConfig(requests_per_minute=5000, tokens_per_minute=4000000))
        self.calls: list[int] = []

    async def acquire(self, estimated_tokens: int = 1_000) -> None:
        self.calls.append(estimated_tokens)


def test_openai_init_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Initialize with explicit key
    provider = OpenAIProvider(api_key="explicit-key", model="gpt-5.4-nano")
    assert provider.provider_name == "openai"
    assert provider.model_name == "gpt-5.4-nano"
    assert provider._api_key == "explicit-key"
    assert provider._base_url == "https://api.openai.com/v1"

    # 2. Initialize with env var key
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    provider2 = OpenAIProvider()
    assert provider2._api_key == "env-key"

    # 3. Initialize with custom base URL env var
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom-openai.com/v1")
    provider3 = OpenAIProvider()
    assert provider3._base_url == "https://custom-openai.com/v1"


def test_openai_init_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as exc_info:
        OpenAIProvider(api_key=None)
    assert "No API key provided" in str(exc_info.value)
    assert exc_info.value.provider == "openai"


def test_openai_supported_reasoning_modes() -> None:
    # 1. qwen / chat template thinking toggle
    provider = OpenAIProvider(api_key="test", model="qwen-72b-instruct")
    assert provider.supported_reasoning_modes() == ("auto", "off", "none")

    # 2. non-reasoning
    provider = OpenAIProvider(api_key="test", model="gpt-4o")
    assert provider.supported_reasoning_modes() == ("auto",)

    # 3. codex-max
    provider = OpenAIProvider(api_key="test", model="codex-max-1")
    assert provider.supported_reasoning_modes() == ("auto", "none", "medium", "high", "xhigh")

    # 4. gpt-5.1
    provider = OpenAIProvider(api_key="test", model="gpt-5.1-mini")
    assert provider.supported_reasoning_modes() == ("auto", "none", "low", "medium", "high")

    # 5. gpt-5-pro
    provider = OpenAIProvider(api_key="test", model="gpt-5-pro-model")
    assert provider.supported_reasoning_modes() == ("auto", "high")

    # 6. gpt-5.4-nano
    provider = OpenAIProvider(api_key="test", model="gpt-5.4-nano")
    assert provider.supported_reasoning_modes() == ("auto", "minimal", "low", "medium", "high")

    # 7. other reasoning models (e.g. o1)
    provider = OpenAIProvider(api_key="test", model="o1-preview")
    assert provider.supported_reasoning_modes() == ("auto", "low", "medium", "high")


def test_openai_reasoning_kwargs() -> None:
    from repowise.core.providers.llm.openai import _openai_reasoning_kwargs

    # auto
    assert _openai_reasoning_kwargs("auto", model="gpt-5.4-nano") == {}

    # qwen off/none
    assert _openai_reasoning_kwargs("off", model="qwen-72b") == {
        "extra_body": {
            "chat_template_kwargs": {
                "enable_thinking": False,
            }
        }
    }
    assert _openai_reasoning_kwargs("none", model="qwen-72b") == {
        "extra_body": {
            "chat_template_kwargs": {
                "enable_thinking": False,
            }
        }
    }

    # off for regular model
    assert _openai_reasoning_kwargs("off", model="gpt-5.4-nano") == {}

    # other valid modes
    assert _openai_reasoning_kwargs("medium", model="gpt-5.4-nano") == {"reasoning_effort": "medium"}
    assert _openai_reasoning_kwargs("high", model="gpt-5.4-nano") == {"reasoning_effort": "high"}

    # max mode (valid reasoning mode, but falls through to empty dict)
    assert _openai_reasoning_kwargs("max", model="gpt-5.4-nano") == {}


def test_available_model_options_success() -> None:
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.get("/models").mock(
            Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-5.4-nano"},
                        {"id": "gpt-5.4-mini"},
                        {"id": "text-embedding-3"},  # filtered out
                    ]
                },
            )
        )

        options = provider.available_model_options()
        assert len(options) == 2
        assert options[0].model == "gpt-5.4-mini"  # sorted alphabetically
        assert options[1].model == "gpt-5.4-nano"


def test_available_model_options_not_list() -> None:
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.get("/models").mock(
            Response(
                200,
                json={"data": "not a list"},
            )
        )

        options = provider.available_model_options()
        assert len(options) == 1
        assert options[0].model == "gpt-5.4-nano"


def test_available_model_options_empty() -> None:
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.get("/models").mock(
            Response(
                200,
                json={"data": []},
            )
        )

        options = provider.available_model_options()
        assert len(options) == 1
        assert options[0].model == "gpt-5.4-nano"


def test_available_model_options_error() -> None:
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.get("/models").mock(Response(500))

        options = provider.available_model_options()
        assert len(options) == 1
        assert options[0].model == "gpt-5.4-nano"


@pytest.mark.anyio
async def test_openai_generate_success() -> None:
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        route = respx_mock.post("/chat/completions").mock(
            Response(
                200,
                json={
                    "choices": [{
                        "index": 0,
                        "message": {
                          "role": "assistant",
                          "content": "Hello, this is OpenAI!"
                        },
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 15,
                        "total_tokens": 25,
                        "prompt_tokens_details": {
                            "cached_tokens": 5
                        }
                    }
                }
            )
        )

        response = await provider.generate(
            system_prompt="sys",
            user_prompt="user",
        )

        assert isinstance(response, GeneratedResponse)
        assert response.content == "Hello, this is OpenAI!"
        assert response.input_tokens == 10
        assert response.output_tokens == 15
        assert response.cached_tokens == 5
        assert response.total_tokens == 25
        assert response.usage["cached_tokens"] == 5

        assert route.called


@pytest.mark.anyio
async def test_openai_generate_success_with_cost_tracker() -> None:
    cost_tracker = MockCostTracker()
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano", cost_tracker=cost_tracker)

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                200,
                json={
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "OpenAI says hi"}
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 10}
                }
            )
        )

        await provider.generate(system_prompt="sys", user_prompt="user")
        assert len(cost_tracker.calls) == 1
        assert cost_tracker.calls[0]["model"] == "gpt-5.4-nano"
        assert cost_tracker.calls[0]["input_tokens"] == 5
        assert cost_tracker.calls[0]["output_tokens"] == 10


@pytest.mark.anyio
async def test_openai_generate_success_with_cost_tracker_failure() -> None:
    cost_tracker = MockCostTracker(should_fail=True)
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano", cost_tracker=cost_tracker)

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                200,
                json={
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "OpenAI says hi"}
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 10}
                }
            )
        )

        # Should not raise exception even when cost tracker fails
        res = await provider.generate(system_prompt="sys", user_prompt="user")
        assert res.content == "OpenAI says hi"


@pytest.mark.anyio
async def test_openai_generate_rate_limiter() -> None:
    rate_limiter = MockRateLimiter()
    provider = OpenAIProvider(api_key="test-api-key", model="gpt-5.4-nano", rate_limiter=rate_limiter)

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                200,
                json={
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"}
                    }],
                }
            )
        )

        await provider.generate(system_prompt="sys", user_prompt="user", max_tokens=123)
        assert rate_limiter.calls == [123]


@pytest.mark.anyio
async def test_openai_generate_rate_limit_error() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                429,
                json={"error": {"message": "Rate limit exceeded"}},
                headers={"retry-after": "5"},
            )
        )

        with pytest.raises(RateLimitError) as exc_info:
            await provider._generate_with_retry(
                system_prompt="sys",
                user_prompt="user",
                max_tokens=100,
                temperature=0.5,
                request_id=None,
                reasoning="auto",
            )

        assert exc_info.value.provider == "openai"
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 5.0


@pytest.mark.anyio
async def test_openai_generate_status_error() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                400,
                json={"error": {"message": "Bad request parameters"}},
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await provider._generate_with_retry(
                system_prompt="sys",
                user_prompt="user",
                max_tokens=100,
                temperature=0.5,
                request_id=None,
                reasoning="auto",
            )

        assert exc_info.value.provider == "openai"
        assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_openai_generate_retry_exhausted() -> None:
    from tenacity import RetryError, Future

    provider = OpenAIProvider(api_key="test-api-key")

    fut = Future(1)
    fut.set_exception(ValueError("some error"))
    retry_error = RetryError(fut)

    provider._generate_with_retry = AsyncMock(side_effect=retry_error)

    with pytest.raises(ProviderError) as exc_info:
        await provider.generate(system_prompt="sys", user_prompt="user")

    assert "All retries exhausted" in str(exc_info.value)
    assert exc_info.value.provider == "openai"


@pytest.mark.anyio
async def test_openai_stream_chat_text_success() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    stream_content = (
        "data: {\"choices\": [{\"index\": 0, \"delta\": {\"content\": \"Hello\"}, \"finish_reason\": null}]}\n\n"
        "data: {\"choices\": [{\"index\": 0, \"delta\": {\"content\": \" world\"}, \"finish_reason\": null}]}\n\n"
        "data: {\"choices\": [], \"usage\": {\"prompt_tokens\": 10, \"completion_tokens\": 20}}\n\n"
        "data: {\"choices\": [{\"index\": 0, \"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n"
        "data: [DONE]\n\n"
    )

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                200,
                content=stream_content,
                headers={"Content-Type": "text/event-stream"},
            )
        )

        events = []
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system_prompt="sys",
        ):
            events.append(event)

        assert len(events) == 4
        # 1. text delta: Hello
        assert events[0].type == "text_delta"
        assert events[0].text == "Hello"
        # 2. text delta:  world
        assert events[1].type == "text_delta"
        assert events[1].text == " world"
        # 3. usage event
        assert events[2].type == "usage"
        assert events[2].input_tokens == 10
        assert events[2].output_tokens == 20
        # 4. stop event
        assert events[3].type == "stop"
        assert events[3].stop_reason == "end_turn"


@pytest.mark.anyio
async def test_openai_stream_chat_tool_success() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    stream_content = (
        "data: {\"choices\": [{\"index\": 0, \"delta\": {\"tool_calls\": [{\"index\": 0, \"id\": \"call_123\", \"function\": {\"name\": \"my_tool\", \"arguments\": \"{\\\"a\\\"\"}}]}, \"finish_reason\": null}]}\n\n"
        "data: {\"choices\": [{\"index\": 0, \"delta\": {\"tool_calls\": [{\"index\": 0, \"function\": {\"arguments\": \": 1}\"}}]}, \"finish_reason\": null}]}\n\n"
        "data: {\"choices\": [{\"index\": 0, \"delta\": {}, \"finish_reason\": \"tool_calls\"}]}\n\n"
        "data: [DONE]\n\n"
    )

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                200,
                content=stream_content,
                headers={"Content-Type": "text/event-stream"},
            )
        )

        events = []
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "my_tool"}],
            system_prompt="sys",
        ):
            events.append(event)

        assert len(events) == 2
        assert events[0].type == "tool_start"
        assert events[0].tool_call is not None
        assert events[0].tool_call.id == "call_123"
        assert events[0].tool_call.name == "my_tool"
        assert events[0].tool_call.arguments == {"a": 1}

        assert events[1].type == "stop"
        assert events[1].stop_reason == "tool_use"


@pytest.mark.anyio
async def test_openai_stream_chat_tool_invalid_json() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    stream_content = (
        "data: {\"choices\": [{\"index\": 0, \"delta\": {\"tool_calls\": [{\"index\": 0, \"id\": \"call_123\", \"function\": {\"name\": \"my_tool\", \"arguments\": \"{invalid_json\"}}]}, \"finish_reason\": null}]}\n\n"
        "data: {\"choices\": [{\"index\": 0, \"delta\": {}, \"finish_reason\": \"tool_calls\"}]}\n\n"
        "data: [DONE]\n\n"
    )

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                200,
                content=stream_content,
                headers={"Content-Type": "text/event-stream"},
            )
        )

        events = []
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "my_tool"}],
            system_prompt="sys",
        ):
            events.append(event)

        assert len(events) == 2
        assert events[0].type == "tool_start"
        assert events[0].tool_call is not None
        assert events[0].tool_call.id == "call_123"
        assert events[0].tool_call.name == "my_tool"
        assert events[0].tool_call.arguments == {}  # empty dict fallback


@pytest.mark.anyio
async def test_openai_stream_chat_rate_limit_error() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                429,
                json={"error": {"message": "Rate limit exceeded"}},
                headers={"retry-after": "5"},
            )
        )

        with pytest.raises(RateLimitError) as exc_info:
            async for _ in provider.stream_chat(
                messages=[],
                tools=[],
                system_prompt="sys",
            ):
                pass

        assert exc_info.value.provider == "openai"
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 5.0


@pytest.mark.anyio
async def test_openai_stream_chat_status_error() -> None:
    provider = OpenAIProvider(api_key="test-api-key")

    with respx.mock(base_url="https://api.openai.com/v1") as respx_mock:
        respx_mock.post("/chat/completions").mock(
            Response(
                400,
                json={"error": {"message": "Bad request"}},
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.stream_chat(
                messages=[],
                tools=[],
                system_prompt="sys",
            ):
                pass

        assert exc_info.value.provider == "openai"
        assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_openai_stream_chat_iteration_rate_limit_error() -> None:
    from openai import RateLimitError as _OpenAIRateLimitError
    from httpx import Request, Response as HttpxResponse

    provider = OpenAIProvider(api_key="test-api-key")

    dummy_req = Request("POST", "https://api.openai.com/v1/chat/completions")
    dummy_res = HttpxResponse(429, request=dummy_req, headers={"retry-after": "7"})
    err = _OpenAIRateLimitError("Rate limit", response=dummy_res, body=None)

    class MockStream:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise err

    provider._client.chat.completions.create = AsyncMock(return_value=MockStream())

    with pytest.raises(RateLimitError) as exc_info:
        async for _ in provider.stream_chat(
            messages=[],
            tools=[],
            system_prompt="sys",
        ):
            pass

    assert exc_info.value.provider == "openai"
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after == 7.0


@pytest.mark.anyio
async def test_openai_stream_chat_iteration_status_error() -> None:
    from openai import APIStatusError as _OpenAIAPIStatusError
    from httpx import Request, Response as HttpxResponse

    provider = OpenAIProvider(api_key="test-api-key")

    dummy_req = Request("POST", "https://api.openai.com/v1/chat/completions")
    dummy_res = HttpxResponse(500, request=dummy_req)
    err = _OpenAIAPIStatusError("Internal Server Error", response=dummy_res, body=None)

    class MockStream:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise err

    provider._client.chat.completions.create = AsyncMock(return_value=MockStream())

    with pytest.raises(ProviderError) as exc_info:
        async for _ in provider.stream_chat(
            messages=[],
            tools=[],
            system_prompt="sys",
        ):
            pass

    assert exc_info.value.provider == "openai"
    assert exc_info.value.status_code == 500
