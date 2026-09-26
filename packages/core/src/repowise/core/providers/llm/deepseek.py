"""DeepSeek provider for repowise.

Access DeepSeek models (V4 Flash, V4 Pro) via the DeepSeek API at
https://api.deepseek.com. The API is fully OpenAI-compatible — this provider
uses the openai Python SDK with a custom base_url, following the same pattern
as OpenRouterProvider.

Models:
    - deepseek-v4-flash  — fast, economical (284B total / 13B active params) [default]
    - deepseek-v4-pro    — highest quality, full reasoning
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from openai import AsyncOpenAI
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    ensure_reasoning_supported,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    record_generation_cost,
)
from repowise.core.providers.llm.openai_compat import (
    completion_to_response,
    listed_model_options,
    stream_openai_chat,
    translate_openai_errors,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
_DEEPSEEK_REASONING_MODELS = frozenset(
    {
        "deepseek-flash",
        "deepseek-v4-pro",
        # Retired aliases remain accepted and are routed to V4.1 Flash.
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
    }
)


def _deepseek_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if model in _DEEPSEEK_REASONING_MODELS:
        return _DEEPSEEK_REASONING_MODES
    return ()


def _resolve_deepseek_reasoning_mode(
    reasoning: ReasoningMode,
    *,
    model: str,
) -> ReasoningMode:
    return ensure_reasoning_supported(
        "deepseek",
        model,
        normalize_reasoning(reasoning),
        _deepseek_supported_reasoning_modes(model),
        detail=(
            "DeepSeek /models lists IDs only; reasoning controls are enabled "
            "for the documented DeepSeek Flash and V4 model families."
        ),
    )


def _deepseek_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    if mode in ("off", "none"):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    effort = "low" if mode in ("minimal", "low") else "max" if mode == "max" else "high"
    return {
        "reasoning_effort": effort,
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def _deepseek_notes(model_id: str, reasoning_modes: tuple[ReasoningMode, ...]) -> str:
    if len(reasoning_modes) > 1:
        return "thinking toggle and graded effort documented by DeepSeek"
    return ""


def _deepseek_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    return listed_model_options(
        api_key,
        base_url,
        fallback_model,
        reasoning_modes_for=_deepseek_supported_reasoning_modes,
        notes_for=_deepseek_notes,
    )


class DeepSeekProvider(BaseProvider):
    """DeepSeek provider — access DeepSeek V4 models via OpenAI-compatible API.

    Args:
        api_key:      DeepSeek API key. Falls back to DEEPSEEK_API_KEY env var.
        model:        Model identifier. Defaults to deepseek-v4-flash.
        base_url:     Override the DeepSeek API URL (rarely needed).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "deepseek",
                "No API key provided. Pass api_key= or set DEEPSEEK_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL") or _DEFAULT_BASE_URL
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
        return "deepseek"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_deepseek_supported_reasoning_modes(self._model))

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _deepseek_model_options(self._api_key, self._base_url, self._model)

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
        reasoning_mode = _resolve_deepseek_reasoning_mode(reasoning, model=self._model)
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "deepseek.generate.start",
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
                "deepseek",
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        kwargs.update(_deepseek_reasoning_kwargs(reasoning))
        with translate_openai_errors("deepseek"):
            response = await self._client.chat.completions.create(**kwargs)

        result = completion_to_response(response)
        log.debug(
            "deepseek.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        await record_generation_cost(self._cost_tracker, model=self._model, result=result)
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
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
            # Thinking tool calls require reasoning_content from every prior
            # assistant turn. Repowise's shared chat history intentionally
            # does not retain hidden reasoning, so use DeepSeek's documented
            # non-thinking mode for this protocol instead of producing an
            # invalid follow-up request after the first tool call.
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if tools:
            kwargs["tools"] = tools

        async for event in stream_openai_chat(self._client, "deepseek", kwargs):
            yield event
