"""OpenRouter provider for repowise.

Routes requests to 200+ models (Claude, GPT, Gemini, Llama, Mistral, etc.)
through a single API key via an OpenAI-compatible endpoint.

No additional pip install required — uses the ``openai`` package.

Popular models:
    - anthropic/claude-sonnet-4.6  — Anthropic Claude Sonnet
    - google/gemini-3.1-flash-lite-preview      — Google Gemini Flash
    - meta-llama/llama-4-maverick  — Meta Llama open model
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import AsyncOpenAI
from openai import RateLimitError as _OpenAIRateLimitError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

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
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 4.0
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
        model:        Model identifier (vendor/model format). Defaults to anthropic/claude-sonnet-4.6.
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
        model: str = "anthropic/claude-sonnet-4.6",
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
                "openrouter",
                f"All {_MAX_RETRIES} retries exhausted: {exc}",
            ) from exc

    @retry(
        retry=retry_if_exception_type(ProviderError),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential_jitter(initial=_MIN_WAIT, max=_MAX_WAIT),
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
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            kwargs.update(_openrouter_reasoning_kwargs(reasoning))
            response = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openrouter", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openrouter", str(exc), status_code=exc.status_code) from exc

        usage = response.usage
        result = GeneratedResponse(
            content=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=0,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
        )
        log.debug(
            "openrouter.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
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
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openrouter", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openrouter", str(exc), status_code=exc.status_code) from exc

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
            raise RateLimitError("openrouter", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openrouter", str(exc), status_code=exc.status_code) from exc
