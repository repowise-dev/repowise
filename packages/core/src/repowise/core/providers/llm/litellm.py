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

import os
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

import structlog
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
    normalize_stop_reason,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    rate_limit_error_from,
    record_generation_cost,
    remember_temperature_rejection,
    temperature_kwargs,
)
from repowise.core.providers.llm.openai_compat import iter_chat_stream_events
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_NON_TEXT_MODEL_MODES = {
    "embedding",
    "image_generation",
    "image_edit",
    "audio_transcription",
    "audio_speech",
    "rerank",
    "video_generation",
    "search",
    "ocr",
    "moderation",
    "realtime",
    "vector_store",
}


def _litellm_reasoning_modes_from_metadata(metadata: object) -> tuple[ReasoningMode, ...]:
    """Map LiteLLM's model metadata to the exact portable effort choices."""

    if not isinstance(metadata, dict):
        return ()

    explicit = metadata.get("reasoning_effort_levels")
    effort_flags = (
        "supports_none_reasoning_effort",
        "supports_minimal_reasoning_effort",
        "supports_low_reasoning_effort",
        "supports_xhigh_reasoning_effort",
        "supports_max_reasoning_effort",
    )
    if (
        metadata.get("supports_reasoning") is not True
        and not isinstance(explicit, list)
        and not any(metadata.get(flag) is True for flag in effort_flags)
    ):
        return ()

    if isinstance(explicit, list):
        return tuple(
            mode
            for mode in ("none", "minimal", "low", "medium", "high", "xhigh", "max")
            if mode in explicit and not (mode == "none" and metadata.get("thinking_always_on"))
        )

    modes: list[ReasoningMode] = []
    if metadata.get("supports_none_reasoning_effort") is True and not metadata.get(
        "thinking_always_on"
    ):
        modes.append("none")
    if metadata.get("supports_minimal_reasoning_effort") is True:
        modes.append("minimal")
    if metadata.get("supports_low_reasoning_effort") is not False:
        modes.append("low")
    modes.extend(("medium", "high"))
    if metadata.get("supports_xhigh_reasoning_effort") is True:
        modes.append("xhigh")
    if metadata.get("supports_max_reasoning_effort") is True:
        modes.append("max")
    return tuple(modes)


def _litellm_metadata_decides_reasoning(metadata: object) -> bool:
    if not isinstance(metadata, dict):
        return False
    if isinstance(metadata.get("reasoning_effort_levels"), list):
        return True
    fields = (
        "supports_reasoning",
        "supports_none_reasoning_effort",
        "supports_minimal_reasoning_effort",
        "supports_low_reasoning_effort",
        "supports_xhigh_reasoning_effort",
        "supports_max_reasoning_effort",
    )
    return any(metadata.get(field) is not None for field in fields)


def _litellm_catalog_reasoning_modes(
    model: str,
    catalog: Mapping[str, object],
    catalog_keys: Mapping[str, str],
) -> tuple[ReasoningMode, ...] | None:
    """Resolve direct or provider-prefixed metadata from the loaded catalog."""
    if model not in catalog:
        return None

    metadata = catalog[model]
    modes = _litellm_reasoning_modes_from_metadata(metadata)
    if modes or _litellm_metadata_decides_reasoning(metadata):
        return modes

    if isinstance(metadata, dict):
        provider = metadata.get("litellm_provider")
        prefix = f"{provider}/" if isinstance(provider, str) else ""
        if prefix and model.startswith(prefix):
            bare_key = catalog_keys.get(model.removeprefix(prefix).casefold())
            bare_metadata = catalog.get(bare_key) if bare_key is not None else None
            bare_modes = _litellm_reasoning_modes_from_metadata(bare_metadata)
            if bare_modes or _litellm_metadata_decides_reasoning(bare_metadata):
                return bare_modes

    return ()


def _litellm_supported_reasoning_modes_from_sources(
    litellm: Any,
    model: str,
    catalog: Mapping[str, object],
    catalog_keys: Mapping[str, str],
) -> tuple[ReasoningMode, ...]:
    catalog_modes = _litellm_catalog_reasoning_modes(model, catalog, catalog_keys)
    if catalog_modes is not None:
        return catalog_modes

    provider, separator, provider_model = model.partition("/")
    if not separator:
        return ()
    try:
        metadata: object = litellm.get_model_info(
            provider_model,
            custom_llm_provider=provider or None,
        )
    except Exception:
        metadata = {}
    modes = _litellm_reasoning_modes_from_metadata(metadata)
    if modes:
        return modes
    if _litellm_metadata_decides_reasoning(metadata):
        return ()
    try:
        if not bool(
            litellm.supports_reasoning(
                model=provider_model,
                custom_llm_provider=provider or None,
            )
        ):
            return ()
    except Exception:
        return ()

    return _litellm_reasoning_modes_from_metadata({"supports_reasoning": True})


def _litellm_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    try:
        import litellm  # type: ignore[import-untyped]

        raw_catalog = getattr(litellm, "model_cost", {}) or {}
        catalog = raw_catalog if isinstance(raw_catalog, Mapping) else {}
        catalog_keys = {key.casefold(): key for key in catalog if isinstance(key, str)}
        return _litellm_supported_reasoning_modes_from_sources(
            litellm,
            model,
            catalog,
            catalog_keys,
        )
    except Exception:
        return ()


def _litellm_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, object]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    return {"reasoning_effort": mode}


def _litellm_model_options(fallback_model: str) -> tuple[ProviderModelOption, ...]:
    try:
        import litellm  # type: ignore[import-untyped]

        raw_catalog = getattr(litellm, "model_cost", {}) or {}
        catalog = raw_catalog if isinstance(raw_catalog, Mapping) else {}
        catalog_keys = {key.casefold(): key for key in catalog if isinstance(key, str)}
        model_ids = sorted(
            {
                model
                for model in getattr(litellm, "model_list", []) or []
                if isinstance(model, str)
                and model
                and (
                    not isinstance(catalog.get(model), dict)
                    or catalog[model].get("mode") not in _NON_TEXT_MODEL_MODES
                )
            }
        )
    except Exception:
        return (fallback_model_option(fallback_model),)

    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=(
            "auto",
            *_litellm_supported_reasoning_modes_from_sources(
                litellm,
                fallback_model,
                catalog,
                catalog_keys,
            ),
        ),
    )

    if not model_ids:
        return (fallback,)

    options: list[ProviderModelOption] = []
    for model_id in model_ids:
        model_modes = _litellm_supported_reasoning_modes_from_sources(
            litellm,
            model_id,
            catalog,
            catalog_keys,
        )
        reasoning_modes = ("auto", *model_modes)
        notes = ""
        if model_modes:
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

    # The backend behind the proxy is unknown and routinely a self-hosted or
    # local model, so budget for the slow case rather than assume a fast one.
    interactive_timeout_s: float = 120.0

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
            "max_tokens": max_tokens,
            **temperature_kwargs(self._model, temperature),
        }
        self._add_endpoint_kwargs(call_kwargs)
        call_kwargs.update(_litellm_reasoning_kwargs(reasoning))

        try:
            response = await self._acompletion(litellm, call_kwargs)
        except litellm.RateLimitError as exc:
            raise rate_limit_error_from("litellm", exc) from exc
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

        await record_generation_cost(self._cost_tracker, model=self._model, result=result)
        return result

    def _add_endpoint_kwargs(self, call_kwargs: dict[str, Any]) -> None:
        """Route the call with the configured key and base URL, when set."""
        if self._api_key:
            call_kwargs["api_key"] = self._api_key
        if self._api_base:
            call_kwargs["api_base"] = self._api_base

    async def _acompletion(self, litellm: Any, call_kwargs: dict[str, Any]) -> Any:
        """Complete, retrying once without a rejected ``temperature``.

        LiteLLM proxies arbitrary vendors, same as OpenRouter: the models that
        reject ``temperature`` cannot be enumerated up front, so learn from the
        rejection and retry once without it.
        """
        try:
            return await litellm.acompletion(**call_kwargs)
        except litellm.APIError as exc:
            if "temperature" not in call_kwargs or not is_temperature_rejection(exc):
                raise
            remember_temperature_rejection(self._model)
            log.debug("litellm.temperature.unsupported", model=self._model)
            call_kwargs.pop("temperature")
            return await litellm.acompletion(**call_kwargs)

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
        import litellm  # type: ignore[import-untyped]

        litellm.set_verbose = False
        litellm.suppress_debug_info = True

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        call_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "stream": True,
            **temperature_kwargs(self._model, temperature),
        }
        if tools:
            call_kwargs["tools"] = tools
        self._add_endpoint_kwargs(call_kwargs)

        try:
            stream = await litellm.acompletion(**call_kwargs)
        except litellm.RateLimitError as exc:
            raise rate_limit_error_from("litellm", exc) from exc
        except litellm.APIError as exc:
            raise ProviderError("litellm", str(exc)) from exc

        try:
            async for event in iter_chat_stream_events(stream, emit_usage=False):
                yield event
        except litellm.RateLimitError as exc:
            raise rate_limit_error_from("litellm", exc) from exc
        except Exception as exc:
            raise ProviderError("litellm", f"{type(exc).__name__}: {exc}") from exc
