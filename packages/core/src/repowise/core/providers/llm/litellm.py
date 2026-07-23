"""LiteLLM provider for repowise.

LiteLLM acts as a proxy layer that normalizes 100+ LLMs behind the OpenAI API.
Use this provider for:
    - Together AI (Meta Llama, Mistral, etc.)
    - Groq (ultra-fast inference)
    - Replicate
    - Azure OpenAI
    - Any other LiteLLM-supported endpoint

LiteLLM model strings use the format: "<provider>/<model>"
    - "together_ai/meta-llama/Llama-3-8b-chat-hf"
    - "groq/llama-3.1-70b-versatile"
    - "azure/gpt-4o"
    - "bedrock/claude-sonnet-4-6"

Reference: https://docs.litellm.ai/docs/providers
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
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

_LITELLM_REASONING_MODES: tuple[ReasoningMode, ...] = ("low", "medium", "high")


def _litellm_supports_reasoning(model: str) -> bool:
    try:
        import litellm  # type: ignore[import-untyped]

        return bool(litellm.supports_reasoning(model=model))
    except Exception:
        return False


def _litellm_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if _litellm_supports_reasoning(model):
        return _LITELLM_REASONING_MODES
    return ()


def _litellm_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, object]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    return {"reasoning_effort": mode}


def _litellm_model_options(fallback_model: str) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=("auto", *_litellm_supported_reasoning_modes(fallback_model)),
    )
    try:
        import litellm  # type: ignore[import-untyped]

        model_ids = sorted(
            {
                model
                for model in getattr(litellm, "model_list", []) or []
                if isinstance(model, str) and model
            }
        )
    except Exception:
        return (fallback,)

    if not model_ids:
        return (fallback,)

    options: list[ProviderModelOption] = []
    for model_id in model_ids:
        try:
            supports_reasoning = bool(litellm.supports_reasoning(model=model_id))
        except Exception:
            supports_reasoning = False
        reasoning_modes = (
            (
                "auto",
                *_LITELLM_REASONING_MODES,
            )
            if supports_reasoning
            else ("auto",)
        )
        notes = ""
        if supports_reasoning:
            notes = "LiteLLM reports reasoning support"
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=reasoning_modes,
                recommended=model_id == fallback_model,
                source="local",
                notes=notes,
            )
        )

    return tuple(options)


class LiteLLMProvider(BaseProvider):
    """LiteLLM proxy provider — 100+ LLMs through a single interface.

    Args:
        model:        LiteLLM model string (e.g., "groq/llama-3.1-70b-versatile").
        api_key:      API key for the target provider. Some providers read from
                      environment variables (e.g., GROQ_API_KEY, TOGETHER_API_KEY).
        api_base:     Optional custom API base URL (e.g., for self-hosted deployments).
        base_url:     Alias for api_base for OpenAI-compatible proxies.
        rate_limiter: Optional RateLimiter instance.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = (
            api_base
            or base_url
            or os.environ.get("LITELLM_API_BASE")
            or os.environ.get("LITELLM_BASE_URL")
        )
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "litellm"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_litellm_supported_reasoning_modes(self._model))

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _litellm_model_options(self._model)

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
            "litellm",
            self._model,
            reasoning,
            _litellm_supported_reasoning_modes(self._model),
            detail="LiteLLM reasoning support comes from litellm.supports_reasoning().",
        )
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

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
                "litellm",
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
        # Import litellm lazily — it's a large package and only needed at call time
        import litellm  # type: ignore[import-untyped]

        # Suppress LiteLLM's verbose feedback/debug output
        litellm.set_verbose = False
        litellm.suppress_debug_info = True

        call_kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._api_key:
            call_kwargs["api_key"] = self._api_key
        if self._api_base:
            call_kwargs["api_base"] = self._api_base
        call_kwargs.update(_litellm_reasoning_kwargs(reasoning))

        try:
            response = await litellm.acompletion(**call_kwargs)
        except litellm.RateLimitError as exc:
            raise RateLimitError(
                "litellm",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except litellm.APIError as exc:
            raise ProviderError("litellm", str(exc)) from exc
        except Exception as exc:
            log.error("litellm.generate.error", model=self._model, error=str(exc))
            raise ProviderError("litellm", f"{type(exc).__name__}: {exc}") from exc

        usage = response.usage
        choice = response.choices[0]
        stop_reason, provider_stop_reason = normalize_stop_reason(choice.finish_reason)
        result = GeneratedResponse(
            content=choice.message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            cached_tokens=0,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
            usage=dict(usage) if usage else {},
        )
        log.debug(
            "litellm.generate.done",
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

        import litellm  # type: ignore[import-untyped]

        litellm.set_verbose = False
        litellm.suppress_debug_info = True

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        call_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            call_kwargs["tools"] = tools
        if self._api_key:
            call_kwargs["api_key"] = self._api_key
        if self._api_base:
            call_kwargs["api_base"] = self._api_base

        try:
            stream = await litellm.acompletion(**call_kwargs)
        except litellm.RateLimitError as exc:
            raise RateLimitError(
                "litellm",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except litellm.APIError as exc:
            raise ProviderError("litellm", str(exc)) from exc

        tool_calls_acc: dict[int, dict[str, Any]] = {}

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta
                finish = choice.finish_reason

                if delta and getattr(delta, "content", None):
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

                if delta and getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": getattr(tc_delta, "id", "") or "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_calls_acc[idx]
                        if getattr(tc_delta, "id", None):
                            acc["id"] = tc_delta.id
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                acc["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                acc["arguments"] += fn.arguments

                if finish:
                    for idx in sorted(tool_calls_acc.keys()):
                        acc = tool_calls_acc[idx]
                        try:
                            args = _json.loads(acc["arguments"]) if acc["arguments"] else {}
                        except Exception:
                            args = {}
                        yield ChatStreamEvent(
                            type="tool_start",
                            tool_call=ChatToolCall(id=acc["id"], name=acc["name"], arguments=args),
                        )
                    tool_calls_acc.clear()
                    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
                    yield ChatStreamEvent(type="stop", stop_reason=stop_reason)
        except litellm.RateLimitError as exc:
            raise RateLimitError(
                "litellm",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except Exception as exc:
            raise ProviderError("litellm", f"{type(exc).__name__}: {exc}") from exc
