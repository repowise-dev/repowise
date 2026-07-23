"""Kimi provider for repowise.

Access Kimi models via the Kimi Code API at
https://api.kimi.com/coding/v1. The API is fully OpenAI-compatible — this provider
uses the openai Python SDK with a custom base_url, following the same pattern
as OpenRouterProvider.

Models:
    - kimi-for-coding  — coding-focused model [default]
    - kimi-for-coding-highspeed — faster Kimi Code model
    - kimi-k2.5        — K2.5 with instant and thinking modes
    - kimi-k2.6        — K2.6 with instant and thinking modes
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
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
    RateLimitError,
    ensure_reasoning_supported,
    fallback_model_option,
    normalize_stop_reason,
    parse_retry_after,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
_KIMI_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "off",
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def _kimi_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if model.startswith(("kimi-k2.5", "kimi-k2.6")):
        return _KIMI_REASONING_MODES
    return ()


def _resolve_kimi_reasoning_mode(
    reasoning: ReasoningMode,
    *,
    model: str,
) -> ReasoningMode:
    return ensure_reasoning_supported(
        "kimi",
        model,
        normalize_reasoning(reasoning),
        _kimi_supported_reasoning_modes(model),
        detail=(
            "Kimi /models lists IDs only; reasoning controls are enabled "
            "for the documented K2 model family."
        ),
    )


def _kimi_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    thinking_type = "disabled" if mode in ("off", "none") else "enabled"
    return {"extra_body": {"thinking": {"type": thinking_type}}}


def _kimi_temperature(
    model: str,
    reasoning: ReasoningMode,
    requested: float,
) -> float:
    if model.startswith("kimi-for-coding"):
        return 0.6
    if model.startswith(("kimi-k2.5", "kimi-k2.6")):
        return 0.6 if normalize_reasoning(reasoning) in ("off", "none") else 1.0
    return requested


def _kimi_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=("auto", *_kimi_supported_reasoning_modes(fallback_model)),
    )
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
        return (fallback,)

    if not isinstance(data, list):
        return (fallback,)

    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        model_id = raw["id"]
        reasoning_modes = ("auto", *_kimi_supported_reasoning_modes(model_id))
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=reasoning_modes,
                recommended=model_id == fallback_model,
                source="api",
                notes=(
                    "reasoning controls documented for Kimi K2" if len(reasoning_modes) > 1 else ""
                ),
            )
        )

    if not options:
        return (fallback,)

    return tuple(options)


class KimiProvider(BaseProvider):
    """Kimi provider — access Kimi models via OpenAI-compatible API.

    Args:
        api_key:      Kimi API key. Falls back to KIMI_API_KEY env var.
        model:        Model identifier. Defaults to kimi-for-coding.
        base_url:     Override the Kimi API URL (rarely needed).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "kimi-for-coding",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("KIMI_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "kimi",
                "No API key provided. Pass api_key= or set KIMI_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("KIMI_BASE_URL") or _DEFAULT_BASE_URL
        self._api_key = resolved_key
        self._base_url = resolved_base_url
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
        )
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "kimi"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_kimi_supported_reasoning_modes(self._model))

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _kimi_model_options(self._api_key, self._base_url, self._model)

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
        reasoning_mode = _resolve_kimi_reasoning_mode(reasoning, model=self._model)
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "kimi.generate.start",
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
                "kimi",
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
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": max_tokens,
                "temperature": _kimi_temperature(self._model, reasoning, temperature),
                "top_p": 0.95,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            kwargs.update(_kimi_reasoning_kwargs(reasoning))
            response = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "kimi",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("kimi", str(exc), status_code=exc.status_code) from exc

        usage = response.usage
        choice = response.choices[0]
        stop_reason, provider_stop_reason = normalize_stop_reason(choice.finish_reason)
        result = GeneratedResponse(
            content=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=0,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
        )
        log.debug(
            "kimi.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
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
                    operation=self._cost_tracker.operation,
                    file_path=None,
                )

        return result

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
            "temperature": _kimi_temperature(self._model, "auto", temperature),
            "top_p": 0.95,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "kimi",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("kimi", str(exc), status_code=exc.status_code) from exc

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

                if delta and delta.content:
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

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
            raise RateLimitError(
                "kimi",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("kimi", str(exc), status_code=exc.status_code) from exc
