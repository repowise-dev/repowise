"""MiniMax provider for repowise.

Uses MiniMax's Anthropic-compatible API endpoint, which mirrors the Anthropic
Messages API. Supports both document generation (generate) and streaming chat
with tool use (stream_chat).

Supported models:
    - MiniMax-M2.7            — Peak Performance. Ultimate Value. Master the Complex (default)
    - MiniMax-M2.7-highspeed  — Same performance, faster and more agile

API documentation:
    https://platform.minimax.io/docs/api-reference/text-anthropic-api
"""

from __future__ import annotations

import os

import structlog
from anthropic import AsyncAnthropic
from anthropic import RateLimitError as _AnthropicRateLimitError
from anthropic import APIStatusError as _AnthropicAPIStatusError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
    RetryError,
)

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)

from typing import TYPE_CHECKING, Any, AsyncIterator
from repowise.core.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 4.0

_DEFAULT_BASE_URL = "https://api.minimax.io/anthropic"


class MiniMaxProvider(BaseProvider):
    """MiniMax chat provider using the Anthropic-compatible API.

    MiniMax's Anthropic-compatible endpoint mirrors the Anthropic Messages API.
    This provider is a drop-in replacement for AnthropicProvider using MiniMax
    models instead of Claude models.

    Args:
        api_key:      MiniMax API key. Falls back to MINIMAX_API_KEY env var.
        model:        Model identifier. Defaults to MiniMax-M2.7.
        base_url:     Base URL for the API. Defaults to https://api.minimax.io/anthropic.
        rate_limiter: Optional pre-configured RateLimiter.
        cost_tracker: Optional CostTracker for recording usage.

    Notes:
        - temperature must be in (0.0, 1.0]. Values <= 0 are clamped to 0.01.
        - Values > 1.0 are clamped to 1.0.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "MiniMax-M2.7",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: "CostTracker | None" = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("MINIMAX_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "minimax",
                "No API key provided. Pass api_key= or set MINIMAX_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("MINIMAX_BASE_URL", _DEFAULT_BASE_URL)
        self._client = AsyncAnthropic(api_key=resolved_key, base_url=resolved_base_url)
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "minimax"

    @property
    def model_name(self) -> str:
        return self._model

    def _clamp_temperature(self, temperature: float) -> float:
        """Clamp temperature to MiniMax's valid range (0.0, 1.0]."""
        if temperature <= 0.0:
            return 0.01
        if temperature > 1.0:
            return 1.0
        return temperature

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        request_id: str | None = None,
    ) -> GeneratedResponse:
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "minimax.generate.start",
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
                "minimax",
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
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=self._clamp_temperature(temperature),
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except _AnthropicRateLimitError as exc:
            raise RateLimitError("minimax", str(exc), status_code=429) from exc
        except _AnthropicAPIStatusError as exc:
            raise ProviderError(
                "minimax", str(exc), status_code=exc.status_code
            ) from exc

        result = GeneratedResponse(
            content=next(
                (block.text for block in response.content if getattr(block, "type", None) == "text"),
                "",
            ),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_tokens=0,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
        log.debug(
            "minimax.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            import asyncio

            try:
                asyncio.get_event_loop().create_task(
                    self._cost_tracker.record(
                        model=self._model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        operation="doc_generation",
                        file_path=None,
                    )
                )
            except RuntimeError:
                pass  # No running event loop — skip async record

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

        # Convert OpenAI-format tools to Anthropic format
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", t)
            anthropic_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            })

        # Convert OpenAI-format messages to Anthropic format
        anthropic_messages = _to_anthropic_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": self._clamp_temperature(temperature),
            "system": system_prompt,
            "messages": anthropic_messages,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                current_tool_id: str | None = None
                current_tool_name: str | None = None
                current_tool_input_json = ""

                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if hasattr(block, "type") and block.type == "tool_use":
                            current_tool_id = block.id
                            current_tool_name = block.name
                            current_tool_input_json = ""
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "type"):
                            if delta.type == "text_delta":
                                yield ChatStreamEvent(type="text_delta", text=delta.text)
                            elif delta.type == "input_json_delta":
                                current_tool_input_json += delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool_name:
                            try:
                                args = _json.loads(current_tool_input_json) if current_tool_input_json else {}
                            except Exception:
                                args = {}
                            yield ChatStreamEvent(
                                type="tool_start",
                                tool_call=ChatToolCall(
                                    id=current_tool_id or "",
                                    name=current_tool_name,
                                    arguments=args,
                                ),
                            )
                            current_tool_id = None
                            current_tool_name = None
                            current_tool_input_json = ""
                    elif event.type == "message_delta":
                        stop = getattr(event.delta, "stop_reason", None)
                        usage = getattr(event, "usage", None)
                        if usage:
                            yield ChatStreamEvent(
                                type="usage",
                                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                            )
                        if stop:
                            yield ChatStreamEvent(type="stop", stop_reason=stop)
                    elif event.type == "message_stop":
                        pass  # Final cleanup; stop already yielded via message_delta
        except _AnthropicRateLimitError as exc:
            raise RateLimitError("minimax", str(exc), status_code=429) from exc
        except _AnthropicAPIStatusError as exc:
            raise ProviderError("minimax", str(exc), status_code=exc.status_code) from exc


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-format messages to Anthropic format for the MiniMax API."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue  # Handled via system= parameter

        if role == "tool":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            })
        elif role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            text = msg.get("content")
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                import json as _json
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                })
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})
        else:
            result.append({"role": "user", "content": msg.get("content", "")})

    return result
