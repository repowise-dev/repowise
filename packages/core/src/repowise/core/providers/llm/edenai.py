"""Eden AI provider for repowise.

Routes requests to models from many vendors (Mistral, GPT, Claude, Gemini, Cohere,
DeepSeek, Llama) through a single API key via Eden AI's OpenAI-compatible gateway at
https://api.edenai.run/v3. Eden AI is EU-headquartered and offers an EU endpoint
(https://api.eu.edenai.run/v3) for data residency / GDPR-sensitive workloads.

No additional pip install required, it uses the ``openai`` package with a custom
base_url, following the same pattern as OpenRouterProvider / DeepSeekProvider.

Models use ``vendor/model`` format:
    - mistral/mistral-small-latest  fast, economical EU model [default]
    - openai/gpt-4o-mini            OpenAI small model
    - anthropic/claude-haiku-4-5    Anthropic Claude Haiku
    - google/gemini-2.5-flash       Google Gemini Flash

The live catalogue is public and needs no authentication at
https://api.edenai.run/v3/models.

Set the EU endpoint for data residency:
    export EDENAI_BASE_URL="https://api.eu.edenai.run/v3"
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

_DEFAULT_BASE_URL = "https://api.edenai.run/v3"


def _model_leaf(model: str) -> str:
    return model.rsplit("/", 1)[-1].lower()


def _supports_reasoning_effort(model: str) -> bool:
    """True for OpenAI reasoning models routed through Eden (vendor/model form)."""
    return _model_leaf(model).startswith(("gpt-5", "o1", "o3", "o4"))


def _edenai_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    """Reasoning efforts Eden forwards to the underlying OpenAI reasoning model.

    Eden accepts the OpenAI ``reasoning_effort`` parameter, so support is keyed on
    the OpenAI reasoning model families. Non-OpenAI or non-reasoning models expose
    only ``auto`` (provider default).
    """
    if not _supports_reasoning_effort(model):
        return ()
    leaf = _model_leaf(model)
    if leaf.startswith("gpt-5.1"):
        return ("none", "low", "medium", "high")
    if leaf.startswith("gpt-5-pro"):
        return ("high",)
    if leaf.startswith("gpt-5"):
        return ("minimal", "low", "medium", "high")
    return ("low", "medium", "high")


def _resolve_edenai_reasoning_mode(reasoning: ReasoningMode, *, model: str) -> ReasoningMode:
    """Validate reasoning support before issuing an API call."""
    return ensure_reasoning_supported(
        "edenai",
        model,
        normalize_reasoning(reasoning),
        _edenai_supported_reasoning_modes(model),
        detail=(
            "EdenAIProvider maps explicit efforts to the OpenAI reasoning_effort "
            "parameter for OpenAI reasoning model ids routed via Eden AI."
        ),
    )


def _edenai_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    """Translate a validated repowise reasoning intent to Eden AI kwargs."""
    mode = normalize_reasoning(reasoning)
    if mode in ("auto", "off"):
        return {}
    if mode in ("none", "minimal", "low", "medium", "high", "xhigh"):
        return {"reasoning_effort": mode}
    return {}


def _edenai_notes(model_id: str, reasoning_modes: tuple[ReasoningMode, ...]) -> str:
    if len(reasoning_modes) > 1:
        return "reasoning_effort forwarded to the underlying OpenAI model"
    return ""


def _edenai_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    return listed_model_options(
        api_key,
        base_url,
        fallback_model,
        reasoning_modes_for=_edenai_supported_reasoning_modes,
        notes_for=_edenai_notes,
        sort=True,
    )


class EdenAIProvider(BaseProvider):
    """Eden AI provider, reaching many vendors via a single OpenAI-compatible key.

    Args:
        api_key:      Eden AI API key. Falls back to EDENAI_API_KEY env var.
        model:        Model identifier in ``vendor/model`` form. Defaults to
                      ``mistral/mistral-small-latest``.
        base_url:     Override the Eden AI API URL. Falls back to EDENAI_BASE_URL,
                      then the global endpoint. Set the EU endpoint
                      (``https://api.eu.edenai.run/v3``) for data residency.
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mistral/mistral-small-latest",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("EDENAI_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "edenai",
                "No API key provided. Pass api_key= or set EDENAI_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("EDENAI_BASE_URL") or _DEFAULT_BASE_URL
        self._api_key = resolved_key
        self._base_url = resolved_base_url.rstrip("/")
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=self._base_url,
        )
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "edenai"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_edenai_supported_reasoning_modes(self._model))

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _edenai_model_options(self._api_key, self._base_url, self._model)

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
        reasoning_mode = _resolve_edenai_reasoning_mode(reasoning, model=self._model)
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "edenai.generate.start",
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
                "edenai",
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
        kwargs.update(_edenai_reasoning_kwargs(reasoning))
        with translate_openai_errors("edenai", include_api_error=False):
            response = await self._client.chat.completions.create(**kwargs)

        result = completion_to_response(response, include_stop_reason=False)
        log.debug(
            "edenai.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        await record_generation_cost(
            self._cost_tracker, model=self._model, result=result, operation="doc_generation"
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        async for event in stream_openai_chat(self._client, "edenai", kwargs, include_api_error=False):
            yield event
