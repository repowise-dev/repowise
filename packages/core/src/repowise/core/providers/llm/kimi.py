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
    - kimi-k3          — K3 with graded reasoning effort
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

_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
_KIMI_K2_REASONING_MODES: tuple[ReasoningMode, ...] = ("off",)
_KIMI_K3_REASONING_MODES: tuple[ReasoningMode, ...] = ("low", "high", "max")


def _kimi_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if model.startswith(("kimi-k2.5", "kimi-k2.6")):
        return _KIMI_K2_REASONING_MODES
    if model.startswith("kimi-k3"):
        return _KIMI_K3_REASONING_MODES
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
            "from the documented K2 and K3 model-family contracts."
        ),
    )


def _kimi_reasoning_kwargs(model: str, reasoning: ReasoningMode) -> dict[str, Any]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    if model.startswith("kimi-k3"):
        return {"reasoning_effort": mode}
    return {"extra_body": {"thinking": {"type": "disabled"}}}


def _kimi_temperature(
    model: str,
    reasoning: ReasoningMode,
    requested: float,
) -> float:
    if model.startswith("kimi-for-coding"):
        # The Kimi coding endpoint rejects any temperature other than 1 for
        # these models ("invalid temperature: only 1 is allowed for this
        # model"), so the requested value cannot be honoured here.
        return 1.0
    if model.startswith(("kimi-k2.5", "kimi-k2.6")):
        return 0.6 if normalize_reasoning(reasoning) in ("off", "none") else 1.0
    return requested


def _kimi_notes(model_id: str, reasoning_modes: tuple[ReasoningMode, ...]) -> str:
    if model_id.startswith("kimi-k3"):
        return "K3 supports low, high, and max reasoning effort"
    return "thinking can be disabled for Kimi K2" if len(reasoning_modes) > 1 else ""


def _kimi_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    return listed_model_options(
        api_key,
        base_url,
        fallback_model,
        reasoning_modes_for=_kimi_supported_reasoning_modes,
        notes_for=_kimi_notes,
    )


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
        kwargs.update(_kimi_reasoning_kwargs(self._model, reasoning))
        with translate_openai_errors("kimi", include_api_error=False):
            response = await self._client.chat.completions.create(**kwargs)

        result = completion_to_response(response)
        log.debug(
            "kimi.generate.done",
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
            "temperature": _kimi_temperature(self._model, "auto", temperature),
            "top_p": 0.95,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        async for event in stream_openai_chat(self._client, "kimi", kwargs, include_api_error=False):
            yield event
