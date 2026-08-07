"""OrcaRouter provider for repowise.

Routes requests to 180+ models (Claude, GPT, Gemini, Qwen, DeepSeek, etc.)
through a single API key via an OpenAI-compatible endpoint at
https://api.orcarouter.ai/v1. OrcaRouter is a multi-vendor routing gateway in
the same spirit as OpenRouter — repowise's ``openrouter`` provider follows the
same shape.

No additional pip install required — uses the ``openai`` package.

Popular models:
    - google/gemini-3.5-flash-lite  — fast + cheap (default)
    - anthropic/claude-haiku-4.5    — Anthropic budget tier
    - openai/gpt-5.4-mini           — OpenAI budget tier
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

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
    RateLimitError,
    ensure_reasoning_supported,
    fallback_model_option,
    is_temperature_rejection,
    normalize_stop_reason,
    parse_retry_after,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    remember_temperature_rejection,
    temperature_kwargs,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"

_ORCAROUTER_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

# OrcaRouter's /models entries do not advertise a `supported_parameters` list
# (unlike OpenRouter), so reasoning support is decided by the model family.
# These leaf-name markers cover the families that accept OpenAI-style
# `reasoning_effort`; everything else is treated as reasoning-less.
_REASONING_MODEL_MARKERS: tuple[str, ...] = (
    "claude",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "gemini",
    "qwen",
    "deepseek",
    "kimi",
    "grok",
    "glm",
    "minimax",
)


def _orcarouter_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    leaf = model.rsplit("/", 1)[-1].lower()
    if any(marker in leaf for marker in _REASONING_MODEL_MARKERS):
        return _ORCAROUTER_REASONING_MODES
    return ()


def _resolve_orcarouter_reasoning_mode(
    reasoning: ReasoningMode,
    *,
    model: str,
) -> ReasoningMode:
    return ensure_reasoning_supported(
        "orcarouter",
        model,
        normalize_reasoning(reasoning),
        _orcarouter_supported_reasoning_modes(model),
        detail=(
            "OrcaRouter routes OpenAI-style reasoning_effort to the underlying "
            "vendor; it is enabled for the reasoning-capable model families."
        ),
    )


def _orcarouter_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    """Translate a validated repowise reasoning intent to OrcaRouter kwargs."""
    mode = normalize_reasoning(reasoning)
    if mode in ("auto", "off"):
        return {}
    effort = "xhigh" if mode in ("xhigh", "max") else mode
    return {"reasoning_effort": effort}


# Which /models `supported_endpoint_types` values describe a model repowise can
# generate docs with. OrcaRouter's gateway exposes video, image, and embedding
# models alongside chat models; they are filtered out so the picker never offers
# a model that cannot answer a prompt.
_CHAT_ENDPOINT_TYPES: frozenset[str] = frozenset(
    {"openai", "openai-response", "gemini", "anthropic"}
)


def _is_chat_model(raw: dict[str, Any]) -> bool:
    endpoint_types = raw.get("supported_endpoint_types")
    if not isinstance(endpoint_types, list):
        # Native OrcaRouter models (orcarouter/fusion, …) omit the field.
        return True
    return bool(_CHAT_ENDPOINT_TYPES.intersection(endpoint_types))


def _orcarouter_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=(
            "auto",
            *_orcarouter_supported_reasoning_modes(fallback_model),
        ),
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
        if not _is_chat_model(raw):
            continue
        model_id = raw["id"]
        reasoning_modes = ("auto", *_orcarouter_supported_reasoning_modes(model_id))
        options.append(
            ProviderModelOption(
                model=model_id,
                label=raw.get("name") if isinstance(raw.get("name"), str) else model_id,
                reasoning_modes=reasoning_modes,
                recommended=model_id == fallback_model,
                source="api",
                notes=(
                    "reasoning effort routed to the underlying vendor"
                    if len(reasoning_modes) > 1
                    else ""
                ),
            )
        )

    if not options:
        return (fallback,)

    options.sort(key=lambda option: option.model)
    return tuple(options)


class OrcaRouterProvider(BaseProvider):
    """OrcaRouter provider — access 180+ models via a single API key.

    Uses the OpenAI-compatible endpoint at ``https://api.orcarouter.ai/v1``.

    Args:
        api_key:      OrcaRouter API key. Falls back to ORCAROUTER_API_KEY env var.
        model:        Model identifier (vendor/model format). Defaults to
                      google/gemini-3.5-flash-lite.
        base_url:     Override the OrcaRouter API URL (rarely needed).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Accepted for registry parity but not used — OrcaRouter
                      proxies many models with varying prices, so repowise's
                      fallback pricing would be misleading. Check the
                      OrcaRouter dashboard.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "google/gemini-3.5-flash-lite",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("ORCAROUTER_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "orcarouter",
                "No API key provided. Pass api_key= or set ORCAROUTER_API_KEY.",
            )
        resolved_base_url = (
            base_url or os.environ.get("ORCAROUTER_BASE_URL") or _DEFAULT_BASE_URL
        )
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
        return "orcarouter"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_orcarouter_supported_reasoning_modes(self._model))

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _orcarouter_model_options(self._api_key, self._base_url, self._model)

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
        reasoning_mode = _resolve_orcarouter_reasoning_mode(
            reasoning, model=self._model
        )
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "orcarouter.generate.start",
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
                "orcarouter",
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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **temperature_kwargs(self._model, temperature),
        }
        kwargs.update(_orcarouter_reasoning_kwargs(reasoning))
        try:
            try:
                response = await self._client.chat.completions.create(**kwargs)
            except _OpenAIAPIStatusError as exc:
                # OrcaRouter fronts every vendor, so the set of models that
                # reject `temperature` is not knowable ahead of time. Drop the
                # parameter and retry once; the model is remembered so the rest
                # of the run skips it.
                if "temperature" not in kwargs or not is_temperature_rejection(exc):
                    raise
                remember_temperature_rejection(self._model)
                log.debug("orcarouter.temperature.unsupported", model=self._model)
                kwargs.pop("temperature")
                response = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "orcarouter",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("orcarouter", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "orcarouter", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc

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
            "orcarouter.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        # Persist spend like the other providers do — without this, any repo
        # generating docs through OrcaRouter records zero cost and the Costs
        # page shows $0. record() swallows its own persistence errors, so
        # generation is unaffected.
        tracker = self._cost_tracker
        if tracker is not None:
            with contextlib.suppress(Exception):
                await tracker.record(
                    model=self._model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    operation=tracker.operation,
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

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "orcarouter",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("orcarouter", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "orcarouter", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc

        # Track in-progress tool calls (OpenAI-compatible streaming)
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

                # Text content
                if delta and delta.content:
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

                # Tool call fragments
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
                    # Emit accumulated tool calls
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
                "orcarouter",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("orcarouter", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "orcarouter", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc
