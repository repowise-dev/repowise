"""Anthropic provider for repowise.

Supports all Claude models. Prompt caching is applied automatically to system
prompts — Anthropic's API caches prompts > 1024 tokens and charges ~10% of
the normal input price on cache hits.

Recommended models (as of 2026):
    - claude-haiku-4-5   — fastest and cheapest (default; ample for doc pages)
    - claude-sonnet-4-6  — best quality/cost ratio
    - claude-opus-4-6    — highest quality, most expensive
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

import structlog
from anthropic import APIStatusError as _AnthropicAPIStatusError
from anthropic import AsyncAnthropic
from anthropic import RateLimitError as _AnthropicRateLimitError
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    ensure_reasoning_supported,
    fallback_model_option,
    normalize_stop_reason,
    parse_tool_arguments,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    record_generation_cost,
    temperature_kwargs,
    translate_sdk_errors,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.anthropic.com"


def _anthropic_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(fallback_model)
    try:
        import httpx

        models_url = base_url.rstrip("/")
        if not models_url.endswith("/v1"):
            models_url = f"{models_url}/v1"
        response = httpx.get(
            f"{models_url}/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
    except Exception:
        return (fallback,)

    if not isinstance(data, list):
        return (fallback,)

    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        model_id = raw["id"]
        display_name = raw.get("display_name")
        options.append(
            ProviderModelOption(
                model=model_id,
                label=display_name if isinstance(display_name, str) else model_id,
                reasoning_modes=("auto",),
                recommended=model_id == fallback_model,
                source="api",
            )
        )

    if not options:
        return (fallback,)

    return tuple(options)


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider with automatic prompt caching.

    Args:
        api_key:      Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        model:        Model identifier. Defaults to claude-haiku-4-5.
        base_url:     Optional custom API base URL (for proxies/self-hosted endpoints).
        rate_limiter: Optional pre-configured RateLimiter. If None, no rate limiting
                      is applied (useful when the caller manages concurrency via semaphore).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-haiku-4-5",
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
        self._api_key = resolved_key
        self._base_url = resolved_base_url or _DEFAULT_BASE_URL
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

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _anthropic_model_options(self._api_key, self._base_url, self._model)

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
                f"All retries exhausted: {exc}",
            ) from exc

    @retry(
        retry=provider_should_retry,
        stop=provider_retry_stop,
        wait=provider_retry_wait,
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
        with _translate_anthropic_errors():
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_param,
                messages=messages_param,
                **temperature_kwargs(self._model, temperature),
            )

        cached = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        stop_reason, provider_stop_reason = normalize_stop_reason(response.stop_reason)

        text_content = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_content = block.text
                break

        result = GeneratedResponse(
            content=text_content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_tokens=cached,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
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

        await record_generation_cost(self._cost_tracker, model=self._model, result=result)
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
        # Convert OpenAI-format tools and messages to Anthropic format
        anthropic_tools = [_to_anthropic_tool(t) for t in tools]
        anthropic_messages = _to_anthropic_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": anthropic_messages,
            **temperature_kwargs(self._model, temperature),
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        with _translate_anthropic_errors():
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in _iter_anthropic_stream_events(stream):
                    yield event


def _translate_anthropic_errors() -> AbstractContextManager[None]:
    return translate_sdk_errors(
        "anthropic",
        rate_limit_error=_AnthropicRateLimitError,
        status_error=_AnthropicAPIStatusError,
    )


def _to_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert one OpenAI-format tool definition to Anthropic's shape."""
    fn = tool.get("function", tool)
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {}),
    }


async def _iter_anthropic_stream_events(stream: Any) -> AsyncIterator[ChatStreamEvent]:
    """Map Anthropic message-stream events to ``ChatStreamEvent``s.

    A ``tool_use`` block streams its input as JSON fragments between its
    start and stop events; it is emitted whole at ``content_block_stop``.
    """
    tool_id: str | None = None
    tool_name: str | None = None
    tool_input_json = ""

    async for event in stream:
        if event.type == "content_block_start":
            block = event.content_block
            if getattr(block, "type", None) == "tool_use":
                tool_id, tool_name, tool_input_json = block.id, block.name, ""
        elif event.type == "content_block_delta":
            delta_type = getattr(event.delta, "type", None)
            if delta_type == "text_delta":
                yield ChatStreamEvent(type="text_delta", text=event.delta.text)
            elif delta_type == "input_json_delta":
                tool_input_json += event.delta.partial_json
        elif event.type == "content_block_stop":
            if tool_name:
                yield ChatStreamEvent(
                    type="tool_start",
                    tool_call=ChatToolCall(
                        id=tool_id or "",
                        name=tool_name,
                        arguments=parse_tool_arguments(tool_input_json),
                    ),
                )
                tool_id, tool_name, tool_input_json = None, None, ""
        elif event.type == "message_delta":
            for message_event in _message_delta_events(event):
                yield message_event
        # message_stop needs nothing: the stop was already yielded via message_delta.


def _message_delta_events(event: Any) -> list[ChatStreamEvent]:
    """Usage and stop events carried by a ``message_delta``, in that order."""
    events: list[ChatStreamEvent] = []
    usage = getattr(event, "usage", None)
    if usage:
        events.append(
            ChatStreamEvent(
                type="usage",
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            )
        )
    stop = getattr(event.delta, "stop_reason", None)
    if stop:
        events.append(ChatStreamEvent(type="stop", stop_reason=stop))
    return events


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
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": msg.get("content", ""),
                        }
                    ],
                }
            )
        elif role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            # Text content
            text = msg.get("content")
            if text:
                content_blocks.append({"type": "text", "text": text})
            # Tool calls
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    args = parse_tool_arguments(args)
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    }
                )
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})
        else:
            # User message
            result.append({"role": "user", "content": msg.get("content", "")})

    return result
