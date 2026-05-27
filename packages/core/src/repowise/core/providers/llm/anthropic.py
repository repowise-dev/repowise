"""Anthropic provider for repowise.

Supports all Claude models. Prompt caching is applied automatically to system
prompts — Anthropic's API caches prompts > 1024 tokens and charges ~10% of
the normal input price on cache hits.

Recommended models (as of 2026):
    - claude-opus-4-6    — highest quality, most expensive
    - claude-sonnet-4-6  — best quality/cost ratio (default)
    - claude-haiku-4-5   — fastest, cheapest (good for low-value pages)
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any, AsyncIterator

import structlog
from anthropic import APIStatusError as _AnthropicAPIStatusError
from anthropic import AsyncAnthropic
from anthropic import RateLimitError as _AnthropicRateLimitError
from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
    ensure_reasoning_supported,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 4.0


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider with automatic prompt caching.

    Args:
        api_key:      Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        model:        Model identifier. Defaults to claude-sonnet-4-6.
        base_url:     Optional custom API base URL (for proxies/self-hosted endpoints).
        rate_limiter: Optional pre-configured RateLimiter. If None, no rate limiting
                      is applied (useful when the caller manages concurrency via semaphore).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "anthropic",
                "No API key provided. Pass api_key= or set ANTHROPIC_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self._client = AsyncAnthropic(api_key=resolved_key, base_url=resolved_base_url)
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "anthropic"

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
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple[CacheHint, ...] = (),
    ) -> GeneratedResponse:
        ensure_reasoning_supported("anthropic", self._model, reasoning)
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "anthropic.generate.start",
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
                cache_hints=cache_hints,
            )
        except RetryError as exc:
            raise ProviderError(
                "anthropic",
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
        cache_hints: tuple[CacheHint, ...] = (),
    ) -> GeneratedResponse:
        system_param, messages_param = _build_cached_payload(
            system_prompt, user_prompt, cache_hints
        )
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_param,
                messages=messages_param,
            )
        except _AnthropicRateLimitError as exc:
            raise RateLimitError("anthropic", str(exc), status_code=429) from exc
        except _AnthropicAPIStatusError as exc:
            raise ProviderError(
                "anthropic", str(exc), status_code=exc.status_code
            ) from exc

        cached = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        result = GeneratedResponse(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_tokens=cached,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", 0
                )
                or 0,
                "cache_read_input_tokens": cached,
            },
        )
        log.debug(
            "anthropic.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            # Await the cost record inline rather than spawning a detached
            # task. A fire-and-forget create_task can still be flushing its
            # aiosqlite write when the event loop is torn down (e.g. the
            # asyncio.run teardown after doc generation), which surfaces as a
            # noisy "Event loop is closed" worker-thread traceback. record()
            # swallows its own persistence errors, so generation is unaffected.
            with contextlib.suppress(Exception):
                await self._cost_tracker.record(
                    model=self._model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    operation="doc_generation",
                    file_path=None,
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
            "temperature": temperature,
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
            raise RateLimitError("anthropic", str(exc), status_code=429) from exc
        except _AnthropicAPIStatusError as exc:
            raise ProviderError("anthropic", str(exc), status_code=exc.status_code) from exc


def _build_cached_payload(
    system_prompt: str,
    user_prompt: str,
    cache_hints: tuple[CacheHint, ...],
) -> tuple[Any, list[dict[str, Any]]]:
    """Translate cache hints into Anthropic-format system/messages blocks.

    Without hints, returns the plain (system_string, [{user content}]) form.
    With hints, returns content-block lists tagged with cache_control.
    Anthropic enforces a hard limit of 4 cache breakpoints — we honor at most
    two (system, then optional user prefix), matching the supported segments.
    """
    if not cache_hints:
        return system_prompt, [{"role": "user", "content": user_prompt}]

    system_param: Any = system_prompt
    if any(h.segment == "system" for h in cache_hints) and system_prompt:
        system_param = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    user_prefix_hint = next(
        (h for h in cache_hints if h.segment == "user_prefix" and h.prefix_chars > 0),
        None,
    )
    if user_prefix_hint is not None and user_prefix_hint.prefix_chars < len(user_prompt):
        prefix = user_prompt[: user_prefix_hint.prefix_chars]
        rest = user_prompt[user_prefix_hint.prefix_chars :]
        user_content: Any = [
            {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": rest},
        ]
    else:
        user_content = user_prompt

    return system_param, [{"role": "user", "content": user_content}]


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-format messages to Anthropic format.

    Key differences:
    - Anthropic has no 'system' role in messages (handled via top-level param)
    - Tool results go as 'user' messages with tool_result content blocks
    - assistant tool_calls become content blocks with type=tool_use
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue  # Handled via system= parameter

        if role == "tool":
            # OpenAI tool result → Anthropic user message with tool_result block
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
            # Text content
            text = msg.get("content")
            if text:
                content_blocks.append({"type": "text", "text": text})
            # Tool calls
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
            # User message
            result.append({"role": "user", "content": msg.get("content", "")})

    return result
