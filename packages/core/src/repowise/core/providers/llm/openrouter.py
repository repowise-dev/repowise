"""OpenRouter provider for repowise.

Routes requests to 200+ models (Claude, GPT, Gemini, Llama, Mistral, etc.)
through a single API key via an OpenAI-compatible endpoint.

No additional pip install required — uses the ``openai`` package.

Popular models:
    - google/gemini-3.5-flash-lite  — fast + cheap (default)
    - openai/gpt-5.6-luna           — OpenAI budget tier
    - anthropic/claude-haiku-4-5    — Anthropic budget tier
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import AsyncOpenAI
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    ensure_reasoning_supported,
    fallback_model_option,
    is_temperature_rejection,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    record_generation_cost,
    remember_temperature_rejection,
    temperature_kwargs,
)
from repowise.core.providers.llm.openai_compat import (
    completion_to_response,
    fetch_model_list,
    stream_openai_chat,
    translate_openai_errors,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_OPENROUTER_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
_OPENROUTER_REASONING_MODELS_BY_BASE: dict[str, set[str]] = {}


def _openrouter_supports_reasoning_effort(
    model: str,
    *,
    base_url: str | None = None,
) -> bool:
    if base_url:
        cached_models = _OPENROUTER_REASONING_MODELS_BY_BASE.get(base_url.rstrip("/"))
        if cached_models is not None and model in cached_models:
            return True
    return False


def _openrouter_supported_reasoning_modes(
    model: str,
    *,
    base_url: str | None = None,
) -> tuple[ReasoningMode, ...]:
    if not _openrouter_supports_reasoning_effort(model, base_url=base_url):
        return ()
    return _OPENROUTER_REASONING_MODES


def _resolve_openrouter_reasoning_mode(
    reasoning: ReasoningMode, *, model: str, base_url: str | None = None
) -> ReasoningMode:
    """Validate OpenRouter reasoning support before retry handling."""
    return ensure_reasoning_supported(
        "openrouter",
        model,
        normalize_reasoning(reasoning),
        _openrouter_supported_reasoning_modes(model, base_url=base_url),
        detail=(
            "OpenRouter reasoning support is taken from models whose /models "
            "entry advertises the reasoning parameter."
        ),
    )


def _openrouter_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    """Translate a validated repowise reasoning intent to OpenRouter kwargs."""
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    effort = "none" if mode in ("off", "none") else mode
    return {
        "extra_body": {
            "reasoning": {
                "effort": effort,
            }
        }
    }


def _openrouter_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=(
            "auto",
            *_openrouter_supported_reasoning_modes(fallback_model, base_url=base_url),
        ),
    )
    data = fetch_model_list(api_key, base_url)
    if data is None:
        return (fallback,)

    base_key = base_url.rstrip("/")
    reasoning_models: set[str] = set()
    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        model_id = raw["id"]
        supported_parameters = raw.get("supported_parameters")
        has_reasoning = (
            isinstance(supported_parameters, list) and "reasoning" in supported_parameters
        )
        if has_reasoning:
            reasoning_models.add(model_id)
        display_name = raw.get("name")
        reasoning_modes = (
            _OPENROUTER_REASONING_MODES
            if has_reasoning
            else _openrouter_supported_reasoning_modes(model_id)
        )
        options.append(
            ProviderModelOption(
                model=model_id,
                label=display_name if isinstance(display_name, str) else model_id,
                reasoning_modes=("auto", *reasoning_modes),
                recommended=model_id == fallback_model,
                source="api",
                notes=("reasoning parameter advertised by /models" if has_reasoning else ""),
            )
        )

    if reasoning_models:
        _OPENROUTER_REASONING_MODELS_BY_BASE[base_key] = reasoning_models

    if not options:
        return (fallback,)

    options.sort(key=lambda option: option.model)
    return tuple(options)


class OpenRouterProvider(BaseProvider):
    """OpenRouter provider — access 200+ models via a single API key.

    Uses the OpenAI-compatible endpoint at ``https://openrouter.ai/api/v1``.

    Args:
        api_key:      OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
        model:        Model identifier (vendor/model format). Defaults to google/gemini-3.5-flash-lite.
        base_url:     Override the OpenRouter API URL (rarely needed).
        rate_limiter: Optional RateLimiter instance.
        http_referer: Optional site URL for OpenRouter rankings/leaderboards.
        app_title:    App name shown on OpenRouter dashboard. Defaults to "repowise".
        cost_tracker: Accepted for registry compatibility but not used — OpenRouter
                      proxies 200+ models with varying prices, so repowise's fallback
                      pricing would be misleading. Check the OpenRouter dashboard.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "google/gemini-3.5-flash-lite",
        base_url: str = "https://openrouter.ai/api/v1",
        rate_limiter: RateLimiter | None = None,
        http_referer: str | None = None,
        app_title: str = "repowise",
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "openrouter",
                "No API key provided. Pass api_key= or set OPENROUTER_API_KEY.",
            )

        headers: dict[str, str] = {}
        if http_referer:
            headers["HTTP-Referer"] = http_referer
        if app_title:
            headers["X-Title"] = app_title

        self._api_key = resolved_key
        self._base_url = base_url.rstrip("/")
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url,
            default_headers=headers or None,
        )
        self._model = model
        self._rate_limiter = rate_limiter

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return (
            "auto",
            *_openrouter_supported_reasoning_modes(
                self._model,
                base_url=self._base_url,
            ),
        )

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _openrouter_model_options(self._api_key, self._base_url, self._model)

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
        if normalize_reasoning(reasoning) != "auto" and not _openrouter_supported_reasoning_modes(
            self._model,
            base_url=self._base_url,
        ):
            self.available_model_options()
        reasoning_mode = _resolve_openrouter_reasoning_mode(
            reasoning, model=self._model, base_url=self._base_url
        )
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "openrouter.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            request_id=request_id,
        )

        try:
            result = await self._generate_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id,
                reasoning=reasoning_mode,
            )
        except RetryError as exc:
            raise ProviderError(
                "openrouter",
                f"All retries exhausted: {exc}",
            ) from exc

        # Persist spend like the other providers do — without this, any repo
        # generating docs through OpenRouter records zero cost and the Costs
        # page shows $0. The tracker is attached externally by the orchestrator,
        # so it may be absent (guard with getattr); record() swallows its own
        # persistence errors, so generation is unaffected.
        await record_generation_cost(
            getattr(self, "_cost_tracker", None), model=self._model, result=result
        )
        return result

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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **temperature_kwargs(self._model, temperature),
        }
        kwargs.update(_openrouter_reasoning_kwargs(reasoning))
        with translate_openai_errors("openrouter"):
            response = await self._create_completion(kwargs)

        result = completion_to_response(response)
        log.debug(
            "openrouter.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        return result

    async def _create_completion(self, kwargs: dict[str, Any]) -> Any:
        """Create a completion, retrying once without a rejected ``temperature``.

        OpenRouter fronts every vendor, so the set of models that reject
        ``temperature`` is not knowable ahead of time. Drop the parameter and
        retry once; the model is remembered so the rest of the run skips it.
        """
        try:
            return await self._client.chat.completions.create(**kwargs)
        except _OpenAIAPIStatusError as exc:
            if "temperature" not in kwargs or not is_temperature_rejection(exc):
                raise
            remember_temperature_rejection(self._model)
            log.debug("openrouter.temperature.unsupported", model=self._model)
            kwargs.pop("temperature")
            return await self._client.chat.completions.create(**kwargs)

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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": full_messages,
            "stream": True,
            **temperature_kwargs(self._model, temperature),
        }
        if tools:
            kwargs["tools"] = tools

        async for event in stream_openai_chat(self._client, "openrouter", kwargs):
            yield event
