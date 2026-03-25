"""OpenAI provider for WikiCode.

Supports all OpenAI Chat Completions models (GPT-4o, o1, o3, etc.).
Also works as a base for any OpenAI-compatible API endpoint via the
`base_url` parameter.

Recommended models (as of 2026):
    - gpt-4o         — strong reasoning, multimodal ready
    - gpt-4o-mini    — cost-efficient
    - o3-mini        — strong for code understanding tasks
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI
from openai import RateLimitError as _OpenAIRateLimitError
from openai import APIStatusError as _OpenAIAPIStatusError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
    RetryError,
)

from wikicode.core.providers.base import (
    BaseProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)

from typing import Any, AsyncIterator
from wikicode.core.rate_limiter import RateLimiter

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 4.0


class OpenAIProvider(BaseProvider):
    """OpenAI Chat Completions provider.

    Args:
        api_key:   OpenAI API key. Reads OPENAI_API_KEY env var if not set.
        model:     Model identifier. Defaults to gpt-4o.
        base_url:  Optional custom base URL for OpenAI-compatible endpoints.
        rate_limiter: Optional RateLimiter instance.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._rate_limiter = rate_limiter

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
    ) -> GeneratedResponse:
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "openai.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            request_id=request_id,
        )

        try:
            return await self._generate_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id,
            )
        except RetryError as exc:
            raise ProviderError(
                "openai",
                f"All {_MAX_RETRIES} retries exhausted: {exc}",
            ) from exc

    @retry(
        retry=retry_if_exception_type(ProviderError),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential_jitter(initial=_MIN_WAIT, max=_MAX_WAIT),
        reraise=True,
    )
    async def _generate_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        request_id: str | None,
    ) -> GeneratedResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openai", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError(
                "openai", str(exc), status_code=exc.status_code
            ) from exc

        usage = response.usage
        result = GeneratedResponse(
            content=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=0,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
        )
        log.debug(
            "openai.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )
        return result

    # --- ChatProvider protocol implementation ---

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        request_id: str | None = None,
        tool_executor: Any | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        import json as _json

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openai", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc

        # Track in-progress tool calls (OpenAI streams them incrementally)
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    if chunk.usage:
                        yield ChatStreamEvent(
                            type="usage",
                            input_tokens=chunk.usage.prompt_tokens or 0,
                            output_tokens=chunk.usage.completion_tokens or 0,
                        )
                    continue

                delta = choice.delta
                finish = choice.finish_reason

                # Text content
                if delta and delta.content:
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

                # Tool call fragments
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_calls_acc[idx]
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                acc["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                acc["arguments"] += tc_delta.function.arguments

                if finish:
                    # Emit accumulated tool calls
                    for idx in sorted(tool_calls_acc.keys()):
                        acc = tool_calls_acc[idx]
                        try:
                            args = _json.loads(acc["arguments"]) if acc["arguments"] else {}
                        except Exception:
                            args = {}
                        yield ChatStreamEvent(
                            type="tool_start",
                            tool_call=ChatToolCall(
                                id=acc["id"],
                                name=acc["name"],
                                arguments=args,
                            ),
                        )
                    tool_calls_acc.clear()

                    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
                    yield ChatStreamEvent(type="stop", stop_reason=stop_reason)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openai", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc
