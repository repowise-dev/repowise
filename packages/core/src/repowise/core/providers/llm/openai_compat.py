"""Shared plumbing for providers that speak the OpenAI Chat Completions shape.

OpenAI, OpenRouter, Ollama, DeepSeek, Kimi and Eden AI all drive the ``openai``
SDK against their own endpoint, and LiteLLM returns the same chunk and
completion objects. What differs between them is request construction
(reasoning, temperature, headers), so that stays in each provider. What is
identical lives here: SDK error translation, streamed tool-call assembly, the
completion-to-``GeneratedResponse`` mapping and the ``/models`` listing fetch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from typing import Any

from openai import APIError as _OpenAIAPIError
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import RateLimitError as _OpenAIRateLimitError

from repowise.core.providers.llm.base import (
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderModelOption,
    fallback_model_option,
    normalize_stop_reason,
    parse_tool_arguments,
    translate_sdk_errors,
)
from repowise.core.reasoning import ReasoningMode


def translate_openai_errors(
    provider: str, *, include_api_error: bool = True
) -> AbstractContextManager[None]:
    """Re-raise ``openai`` SDK errors as repowise provider errors.

    *include_api_error* also wraps status-less SDK errors (connection
    failures, timeouts). Kimi and Eden AI have never wrapped those, which
    leaves them unretried there; kept as-is rather than changed in a refactor.
    """
    return translate_sdk_errors(
        provider,
        rate_limit_error=_OpenAIRateLimitError,
        status_error=_OpenAIAPIStatusError,
        api_error=_OpenAIAPIError if include_api_error else None,
    )


class ToolCallAccumulator:
    """Reassembles tool calls that Chat Completions streams in fragments.

    Each fragment carries an ``index``; the id and name arrive once and the
    JSON arguments arrive in pieces, so they are concatenated per index and
    parsed only when the choice finishes.
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

    *emit_usage* yields the trailing choice-less usage chunk as a ``usage``
    event. Ollama and LiteLLM have never reported it, so they pass False.
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

    SDK errors are translated both when the request is opened and while the
    stream is consumed, since a 429 can surface at either point.
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

    *cached_tokens*, when given, is reported and added to the usage dict.
    The two ``include_*`` flags preserve per-provider differences that predate
    this helper: Eden AI never reported a stop reason and Ollama never recorded
    ``total_tokens``.
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
    """Return the ``data`` list from an OpenAI-style ``GET /models``.

    ``None`` when the endpoint is unreachable, rejects the key, or answers
    with something other than a list, so callers fall back to the configured
    model.
    """
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

    *reasoning_modes_for* supplies each id's explicit modes (``auto`` is
    prepended) and *notes_for* its note, given the id and the full modes.
    Falls back to the configured model when the listing is unavailable or
    names no model.
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
