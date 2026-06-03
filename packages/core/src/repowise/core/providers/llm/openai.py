"""OpenAI provider for repowise.

Supports all OpenAI Chat Completions models (GPT-4o, o1, o3, etc.).
Also works as a base for any OpenAI-compatible API endpoint via the
`base_url` parameter.

Recommended models (as of 2026):
    - gpt-5.4-nano   — fastest, cheapest ($0.20/$1.25 per MTok) [default]
    - gpt-5.4-mini   — balanced speed and quality ($0.75/$4.50 per MTok)
    - gpt-5.4        — highest quality ($2.50/$15 per MTok)
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
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
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
_QWEN_THINKING_MODEL_MARKERS = ("qwen", "qwq")
_OPENAI_TEXT_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4")
_OPENAI_NON_TEXT_MARKERS = (
    "audio",
    "babbage",
    "dall-e",
    "davinci",
    "embedding",
    "image",
    "moderation",
    "sora",
    "tts",
    "transcribe",
    "whisper",
)


def _model_leaf(model: str) -> str:
    return model.rsplit("/", 1)[-1].lower()


def _supports_openai_reasoning_effort(model: str) -> bool:
    leaf = _model_leaf(model)
    return leaf.startswith(("gpt-5", "o1", "o3", "o4"))


def _supports_chat_template_thinking_toggle(model: str) -> bool:
    leaf = _model_leaf(model)
    return any(marker in leaf for marker in _QWEN_THINKING_MODEL_MARKERS)


def _openai_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if _supports_chat_template_thinking_toggle(model):
        return ("off", "none")
    if not _supports_openai_reasoning_effort(model):
        return ()

    leaf = _model_leaf(model)
    if "codex-max" in leaf:
        return ("none", "medium", "high", "xhigh")
    if leaf.startswith("gpt-5.1"):
        return ("none", "low", "medium", "high")
    if leaf.startswith("gpt-5-pro"):
        return ("high",)
    if leaf.startswith("gpt-5"):
        return ("minimal", "low", "medium", "high")
    return ("low", "medium", "high")


def _resolve_openai_reasoning_mode(reasoning: ReasoningMode, *, model: str) -> ReasoningMode:
    """Validate OpenAI-compatible reasoning support before retry handling."""
    return ensure_reasoning_supported(
        "openai",
        model,
        normalize_reasoning(reasoning),
        _openai_supported_reasoning_modes(model),
        detail=(
            "OpenAIProvider maps explicit efforts to OpenAI reasoning_effort "
            "for known reasoning model ids, and maps off/none to Qwen/QwQ "
            "chat_template_kwargs for OpenAI-compatible endpoints."
        ),
    )


def _openai_reasoning_kwargs(reasoning: ReasoningMode, *, model: str) -> dict[str, Any]:
    """Translate a validated repowise reasoning intent to OpenAI kwargs."""
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    if _supports_chat_template_thinking_toggle(model) and mode in ("off", "none"):
        return {
            "extra_body": {
                "chat_template_kwargs": {
                    "enable_thinking": False,
                },
            },
        }
    if mode == "off":
        return {}
    if mode in ("none", "minimal", "low", "medium", "high", "xhigh"):
        return {"reasoning_effort": mode}
    return {}


def _is_openai_text_model(model_id: str) -> bool:
    leaf = _model_leaf(model_id)
    if any(marker in leaf for marker in _OPENAI_NON_TEXT_MARKERS):
        return False
    return leaf.startswith(_OPENAI_TEXT_MODEL_PREFIXES)


def _openai_option(
    model_id: str,
    *,
    fallback_model: str,
) -> ProviderModelOption:
    reasoning_modes = ("auto", *_openai_supported_reasoning_modes(model_id))
    notes = (
        "reasoning levels inferred; OpenAI /models does not advertise them"
        if len(reasoning_modes) > 1
        else ""
    )
    return ProviderModelOption(
        model=model_id,
        label=model_id,
        reasoning_modes=reasoning_modes,
        recommended=model_id == fallback_model,
        source="api",
        notes=notes,
    )


def _openai_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=("auto", *_openai_supported_reasoning_modes(fallback_model)),
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

    model_ids = sorted(
        {
            model["id"]
            for model in data
            if isinstance(model, dict)
            and isinstance(model.get("id"), str)
            and _is_openai_text_model(model["id"])
        }
    )
    if not model_ids:
        return (fallback,)

    return tuple(_openai_option(model_id, fallback_model=fallback_model) for model_id in model_ids)


class OpenAIProvider(BaseProvider):
    """OpenAI Chat Completions provider.

    Args:
        api_key:   OpenAI API key. Falls back to OPENAI_API_KEY env var.
        model:     Model identifier. Defaults to gpt-4o.
        base_url:  Optional custom base URL for OpenAI-compatible endpoints.
        rate_limiter: Optional RateLimiter instance.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5.4-nano",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise ProviderError(
                "openai",
                "No API key provided. Pass api_key= or set OPENAI_API_KEY.",
            )
        resolved_base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._api_key = resolved_key
        self._base_url = resolved_base_url or "https://api.openai.com/v1"
        self._client = AsyncOpenAI(api_key=resolved_key, base_url=resolved_base_url)
        self._model = model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_openai_supported_reasoning_modes(self._model))

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _openai_model_options(self._api_key, self._base_url, self._model)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple[CacheHint, ...] = (),
    ) -> GeneratedResponse:
        # OpenAI auto-caches stable prompt prefixes >= 1024 tokens; hints are
        # informational only, so we accept and discard them.
        del cache_hints
        reasoning_mode = _resolve_openai_reasoning_mode(reasoning, model=self._model)
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "openai.generate.start",
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
                "openai",
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
                "max_completion_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            kwargs.update(_openai_reasoning_kwargs(reasoning, model=self._model))
            response = await self._client.chat.completions.create(
                **kwargs,
            )
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openai", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc

        usage = response.usage
        cached = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
        result = GeneratedResponse(
            content=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=cached,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
                "cached_tokens": cached,
            },
        )
        log.debug(
            "openai.generate.done",
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
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError("openai", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc

        # Track in-progress tool calls (OpenAI streams them incrementally)
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
            raise RateLimitError("openai", str(exc), status_code=429) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc
