"""OpenAI provider for repowise.

Supports all OpenAI Chat Completions models (GPT-4o, o1, o3, etc.).
Also works as a base for any OpenAI-compatible API endpoint via the
`base_url` parameter.

Recommended models (as of 2026):
    - gpt-5.6-luna   — fastest, cheapest ($0.20/$1.20 per MTok) [default]
    - gpt-5.4-mini   — balanced speed and quality ($0.75/$4.50 per MTok)
    - gpt-5.4        — highest quality ($2.50/$15 per MTok)
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog
from openai import APIError as _OpenAIAPIError
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import AsyncOpenAI
from openai import RateLimitError as _OpenAIRateLimitError
from tenacity import RetryError, retry

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
    if leaf.startswith("gpt-5.6"):
        # 5.6 dropped `minimal` and added `xhigh`. Must stay above the generic
        # `gpt-5` branch, which is a prefix of this one and would otherwise win
        # and offer `minimal`, which 5.6 rejects, and only on a live call.
        #
        # `max` is deliberately absent even though the model docs list it: the
        # API rejects it with `unsupported_value`, naming exactly the five
        # below. Verified live on both gpt-5.6-luna and gpt-5.6-sol
        # (2026-08-15). Family-level rather than per-model on the same
        # evidence: the two variants answered identically.
        return ("none", "low", "medium", "high", "xhigh")
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
    # `max` is listed for completeness over ReasoningMode, not because any
    # OpenAI model accepts it today. None does, so the validation gate above
    # rejects it first. It is here so that whenever one does, adding it to that
    # model's tuple is the only edit needed: the previous shape passed
    # validation and then dropped the effort silently, which reads as "max
    # worked" while the request carried no reasoning_effort at all.
    if mode in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
        return {"reasoning_effort": mode}
    return {}


def _openai_temperature(model: str, requested: float) -> float:
    """Clamp temperature for models that only accept the default value.

    OpenAI's reasoning-era models (GPT-5+, o1/o3/o4) reject any explicit
    ``temperature`` other than the default of ``1``; sending our usual low
    sampling temperature returns a 400 ``unsupported_value`` error. For those
    models we force ``1.0`` and pass the requested value through otherwise.
    """
    if _supports_openai_reasoning_effort(model):
        return 1.0
    return requested


def _openai_chat_tool_kwargs(model: str, *, has_tools: bool) -> dict[str, Any]:
    """Return Chat Completions overrides required by tool-enabled models.

    GPT-5.6 models default to a non-none reasoning effort. OpenAI recommends
    the Responses API for reasoning with tool-calling, while this provider's
    repository chat loop still uses Chat Completions. Keep the family's normal
    reasoning default for generation and only disable it when function tools
    are attached. Migrating the shared chat protocol is a separate change.
    """
    leaf = _model_leaf(model)
    if has_tools and (leaf == "gpt-5.6" or leaf.startswith("gpt-5.6-")):
        return {"reasoning_effort": "none"}
    return {}


def _is_openai_text_model(model_id: str, *, allow_namespaced: bool = False) -> bool:
    """Keep chat-capable ids, including arbitrary ids from custom gateways."""
    leaf = _model_leaf(model_id)
    if any(marker in leaf for marker in _OPENAI_NON_TEXT_MARKERS):
        return False
    return allow_namespaced or leaf.startswith(_OPENAI_TEXT_MODEL_PREFIXES)


def _is_loopback_url(base_url: str) -> bool:
    host = urlparse(base_url).hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
        return _discover_openai_model_options(api_key, base_url, fallback_model)
    except Exception:
        return (fallback,)


def _discover_openai_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    """Fetch model ids, raising when an endpoint cannot prove it is usable."""
    import httpx

    request_kwargs: dict[str, Any] = {
        "headers": {"Authorization": f"Bearer {api_key}"},
        "timeout": 5.0,
    }
    if _is_loopback_url(base_url):
        request_kwargs["trust_env"] = False
    response = httpx.get(f"{base_url.rstrip('/')}/models", **request_kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("the /models response does not contain a data list")
    data = payload["data"]

    model_ids = sorted(
        {
            model["id"]
            for model in data
            if isinstance(model, dict)
            and isinstance(model.get("id"), str)
            and _is_openai_text_model(
                model["id"],
                allow_namespaced=base_url.rstrip("/") != "https://api.openai.com/v1",
            )
        }
    )
    if not model_ids:
        raise ValueError("the /models response contains no text-generation models")

    return tuple(_openai_option(model_id, fallback_model=fallback_model) for model_id in model_ids)


class OpenAIProvider(BaseProvider):
    """OpenAI Chat Completions provider.

    Args:
        api_key:   OpenAI API key. Falls back to OPENAI_API_KEY env var.
        model:     Model identifier. Defaults to gpt-5.6-luna.
        base_url:  Optional custom base URL for OpenAI-compatible endpoints.
        rate_limiter: Optional RateLimiter instance.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5.6-luna",
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
        http_client = None
        if resolved_base_url and _is_loopback_url(resolved_base_url):
            import httpx

            http_client = httpx.AsyncClient(trust_env=False)
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
            http_client=http_client,
        )
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

    def discover_model_options(self) -> tuple[ProviderModelOption, ...]:
        """Return live `/models` options or raise an actionable endpoint error."""
        return _discover_openai_model_options(self._api_key, self._base_url, self._model)

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
                "max_completion_tokens": max_tokens,
                "temperature": _openai_temperature(self._model, temperature),
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
            raise RateLimitError(
                "openai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "openai", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc

        usage = response.usage
        cached = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
        choice = response.choices[0]
        stop_reason, provider_stop_reason = normalize_stop_reason(choice.finish_reason)
        result = GeneratedResponse(
            content=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=cached,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
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

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "temperature": _openai_temperature(self._model, temperature),
            "messages": full_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        kwargs.update(_openai_chat_tool_kwargs(self._model, has_tools=bool(tools)))

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "openai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "openai", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc

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
            raise RateLimitError(
                "openai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("openai", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "openai", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc
