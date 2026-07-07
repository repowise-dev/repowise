"""Eden AI provider for repowise.

Routes requests to 700+ models (Mistral, GPT, Claude, Gemini, Cohere, DeepSeek,
Llama, etc.) through a single API key via Eden AI's OpenAI-compatible gateway at
https://api.edenai.run/v3. Eden AI is EU-headquartered and offers an EU endpoint
(https://api.eu.edenai.run/v3) for data residency / GDPR-sensitive workloads.

No additional pip install required — uses the ``openai`` package with a custom
base_url, following the same pattern as OpenRouterProvider / DeepSeekProvider.

Models use ``vendor/model`` format:
    - mistral/mistral-small-latest  — fast, economical EU model [default]
    - openai/gpt-4o-mini            — OpenAI small model
    - anthropic/claude-haiku-4-5    — Anthropic Claude Haiku
    - google/gemini-2.5-flash       — Google Gemini Flash

Set the EU endpoint for data residency:
    export EDENAI_BASE_URL="https://api.eu.edenai.run/v3"
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


def _edenai_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=("auto", *_edenai_supported_reasoning_modes(fallback_model)),
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
        reasoning_modes = ("auto", *_edenai_supported_reasoning_modes(model_id))
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=reasoning_modes,
                recommended=model_id == fallback_model,
                source="api",
                notes=(
                    "reasoning_effort forwarded to the underlying OpenAI model"
                    if len(reasoning_modes) > 1
                    else ""
                ),
            )
        )

    if not options:
        return (fallback,)

    options.sort(key=lambda option: option.model)
    return tuple(options)


class EdenAIProvider(BaseProvider):
    """Eden AI provider — access 700+ models via a single OpenAI-compatible key.

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
            kwargs.update(_edenai_reasoning_kwargs(reasoning))
            response = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "edenai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("edenai", str(exc), status_code=exc.status_code) from exc

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
            "edenai.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            # Await the cost record inline rather than spawning a detached task —
            # a fire-and-forget create_task can still be flushing its aiosqlite
            # write when the event loop is torn down, surfacing as a noisy
            # "Event loop is closed" traceback. record() swallows its own
            # persistence errors, so generation is unaffected.
            with contextlib.suppress(Exception):
                await self._cost_tracker.record(
                    model=self._model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    operation="doc_generation",
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
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "edenai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("edenai", str(exc), status_code=exc.status_code) from exc

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
                "edenai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("edenai", str(exc), status_code=exc.status_code) from exc
