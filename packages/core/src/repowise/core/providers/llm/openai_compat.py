"""Shared plumbing for providers that speak the OpenAI Chat Completions shape.

Request construction stays in each provider; error translation, stream parsing,
response mapping and the ``/models`` listing live here.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from openai import APIError as _OpenAIAPIError
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import AsyncOpenAI
from openai import RateLimitError as _OpenAIRateLimitError
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    BaseProvider,
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
    translate_sdk_errors,
)
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.rate_limiter import RateLimiter

log = structlog.get_logger(__name__)


def translate_openai_errors(
    provider: str, *, include_api_error: bool = True
) -> AbstractContextManager[None]:
    """Re-raise ``openai`` SDK errors as repowise provider errors.

    *include_api_error* also wraps status-less errors (connection failures,
    timeouts), which makes them retryable.
    """
    return translate_sdk_errors(
        provider,
        rate_limit_error=_OpenAIRateLimitError,
        status_error=_OpenAIAPIStatusError,
        api_error=_OpenAIAPIError if include_api_error else None,
    )


class ToolCallAccumulator:
    """Reassembles tool calls that Chat Completions streams in fragments.

    Arguments arrive in pieces per ``index`` and are parsed once the choice finishes.
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def add(self, fragment: Any) -> None:
        acc = self._calls.setdefault(
            fragment.index,
            {"id": getattr(fragment, "id", "") or "", "name": "", "arguments": ""},
        )
        if getattr(fragment, "id", None):
            acc["id"] = fragment.id
        fn = getattr(fragment, "function", None)
        if not fn:
            return
        if getattr(fn, "name", None):
            acc["name"] = fn.name
        if getattr(fn, "arguments", None):
            acc["arguments"] += fn.arguments

    def drain(self) -> list[ChatToolCall]:
        """Return the completed calls in index order and reset."""
        calls = [
            ChatToolCall(
                id=acc["id"],
                name=acc["name"],
                arguments=parse_tool_arguments(acc["arguments"]),
            )
            for _, acc in sorted(self._calls.items())
        ]
        self._calls.clear()
        return calls


async def iter_chat_stream_events(
    stream: AsyncIterator[Any],
    *,
    emit_usage: bool = True,
) -> AsyncIterator[ChatStreamEvent]:
    """Map streamed Chat Completions chunks to ``ChatStreamEvent``s.

    *emit_usage* reports the trailing choice-less chunk as a ``usage`` event.
    """
    tool_calls = ToolCallAccumulator()
    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            if emit_usage and chunk.usage:
                yield ChatStreamEvent(
                    type="usage",
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                )
            continue

        delta = choice.delta
        if delta and getattr(delta, "content", None):
            yield ChatStreamEvent(type="text_delta", text=delta.content)
        if delta and getattr(delta, "tool_calls", None):
            for fragment in delta.tool_calls:
                tool_calls.add(fragment)

        finish = choice.finish_reason
        if finish:
            for call in tool_calls.drain():
                yield ChatStreamEvent(type="tool_start", tool_call=call)
            stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
            yield ChatStreamEvent(type="stop", stop_reason=stop_reason)


async def stream_openai_chat(
    client: Any,
    provider: str,
    kwargs: dict[str, Any],
    *,
    emit_usage: bool = True,
    include_api_error: bool = True,
) -> AsyncIterator[ChatStreamEvent]:
    """Open a streamed completion on an ``AsyncOpenAI`` client and yield events.

    Errors are translated on open and while consuming: a 429 can surface at either.
    """
    with translate_openai_errors(provider, include_api_error=include_api_error):
        stream = await client.chat.completions.create(**kwargs)
    with translate_openai_errors(provider, include_api_error=include_api_error):
        async for event in iter_chat_stream_events(stream, emit_usage=emit_usage):
            yield event


def completion_to_response(
    response: Any,
    *,
    cached_tokens: int | None = None,
    include_stop_reason: bool = True,
    include_total_tokens: bool = True,
) -> GeneratedResponse:
    """Build a ``GeneratedResponse`` from a non-streamed chat completion.

    *cached_tokens*, when given, is also added to the usage dict. The
    ``include_*`` flags drop fields a provider does not report.
    """
    usage = response.usage
    choice = response.choices[0]
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    usage_dict: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if include_total_tokens:
        usage_dict["total_tokens"] = usage.total_tokens if usage else 0
    if cached_tokens is not None:
        usage_dict["cached_tokens"] = cached_tokens
    stop_reason, provider_stop_reason = (
        normalize_stop_reason(choice.finish_reason) if include_stop_reason else (None, None)
    )
    return GeneratedResponse(
        content=choice.message.content or "",
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        cached_tokens=cached_tokens or 0,
        stop_reason=stop_reason,
        provider_stop_reason=provider_stop_reason,
        usage=usage_dict,
    )


def fetch_model_list(api_key: str, base_url: str) -> list[Any] | None:
    """The ``data`` list from an OpenAI-style ``GET /models``, or None on any failure."""
    try:
        import httpx

        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
    except Exception:
        return None
    return data if isinstance(data, list) else None


def listed_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
    *,
    reasoning_modes_for: Callable[[str], tuple[ReasoningMode, ...]],
    notes_for: Callable[[str, tuple[ReasoningMode, ...]], str],
    sort: bool = False,
) -> tuple[ProviderModelOption, ...]:
    """Model options from ``GET /models`` for an endpoint that lists ids only.

    Falls back to the configured model when the listing is unavailable or empty.
    """
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=("auto", *reasoning_modes_for(fallback_model)),
    )
    data = fetch_model_list(api_key, base_url)
    if data is None:
        return (fallback,)

    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        model_id = raw["id"]
        reasoning_modes: tuple[ReasoningMode, ...] = ("auto", *reasoning_modes_for(model_id))
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=reasoning_modes,
                recommended=model_id == fallback_model,
                source="api",
                notes=notes_for(model_id, reasoning_modes),
            )
        )

    if not options:
        return (fallback,)
    if sort:
        options.sort(key=lambda option: option.model)
    return tuple(options)


class OpenAICompatibleProvider(BaseProvider):
    """Base for a gateway that is the Chat Completions API at another URL.

    Subclasses set the class attributes and override the ``_*_kwargs`` hooks.
    """

    provider_id: ClassVar[str]
    api_key_env: ClassVar[str]
    base_url_env: ClassVar[str]
    default_base_url: ClassVar[str]
    #: Appended to the "reasoning not supported" error.
    reasoning_detail: ClassVar[str]
    #: Wrap status-less SDK errors (connection failures, timeouts) so they retry.
    wrap_connection_errors: ClassVar[bool] = True
    #: Report the completion's stop reason on ``GeneratedResponse``.
    reports_stop_reason: ClassVar[bool] = True
    #: Fixed cost-tracker operation; ``None`` uses the tracker's current one.
    cost_operation: ClassVar[str | None] = None

    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str | None,
        rate_limiter: RateLimiter | None,
        cost_tracker: CostTracker | None,
    ) -> None:
        resolved_key = api_key or os.environ.get(self.api_key_env)
        if not resolved_key:
            raise ProviderError(
                self.provider_id,
                f"No API key provided. Pass api_key= or set {self.api_key_env}.",
            )
        resolved_base_url = (
            base_url or os.environ.get(self.base_url_env) or self.default_base_url
        )
        self._api_key = resolved_key
        self._base_url = self._clean_base_url(resolved_base_url)
        self._client = AsyncOpenAI(api_key=resolved_key, base_url=self._base_url)
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return self.provider_id

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *self._explicit_reasoning_modes(self._model))

    # --- hooks -----------------------------------------------------------

    def _clean_base_url(self, base_url: str) -> str:
        return base_url

    def _explicit_reasoning_modes(self, model: str) -> tuple[ReasoningMode, ...]:
        """The reasoning modes *model* accepts besides ``auto``."""
        raise NotImplementedError

    def _reasoning_kwargs(self, reasoning: ReasoningMode) -> dict[str, Any]:
        """Request kwargs that carry a validated reasoning mode."""
        raise NotImplementedError

    def _completion_kwargs(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        reasoning: ReasoningMode,
    ) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            **self._reasoning_kwargs(reasoning),
        }

    def _stream_kwargs(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
        }

    # --- generation --------------------------------------------------------

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple = (),
    ) -> GeneratedResponse:
        reasoning_mode = ensure_reasoning_supported(
            self.provider_id,
            self._model,
            normalize_reasoning(reasoning),
            self._explicit_reasoning_modes(self._model),
            detail=self.reasoning_detail,
        )
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            f"{self.provider_id}.generate.start",
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
                reasoning=reasoning_mode,
            )
        except RetryError as exc:
            raise ProviderError(
                self.provider_id,
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
        reasoning: ReasoningMode,
    ) -> GeneratedResponse:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        kwargs = self._completion_kwargs(messages, max_tokens, temperature, reasoning)
        with translate_openai_errors(
            self.provider_id, include_api_error=self.wrap_connection_errors
        ):
            response = await self._client.chat.completions.create(**kwargs)

        result = completion_to_response(response, include_stop_reason=self.reports_stop_reason)
        log.debug(
            f"{self.provider_id}.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        await record_generation_cost(
            self._cost_tracker, model=self._model, result=result, operation=self.cost_operation
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
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        kwargs = self._stream_kwargs(full_messages, max_tokens, temperature)
        if tools:
            kwargs["tools"] = tools

        async for event in stream_openai_chat(
            self._client,
            self.provider_id,
            kwargs,
            include_api_error=self.wrap_connection_errors,
        ):
            yield event
