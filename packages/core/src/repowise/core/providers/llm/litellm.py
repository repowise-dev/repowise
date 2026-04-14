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

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
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
    RateLimitError,
)

from repowise.core.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 4.0


class LiteLLMProvider(BaseProvider):
    """LiteLLM proxy provider — 100+ LLMs through a single interface.

    Args:
        model:        LiteLLM model string (e.g., "groq/llama-3.1-70b-versatile").
                      When using api_base (local proxy), just use the model name
                      (e.g., "zai.glm-5") - the provider will auto-add "openai/" prefix.
        api_key:      API key for the target provider. Some providers read from
                      environment variables (e.g., GROQ_API_KEY, TOGETHER_API_KEY).
                      For local proxies without auth, a dummy key is used.
        api_base:     Optional custom API base URL for self-hosted LiteLLM proxy.
                      When set, the model is treated as OpenAI-compatible.
        rate_limiter: Optional RateLimiter instance.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: "CostTracker | None" = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker

        # When using a custom api_base (proxy), treat model as OpenAI-compatible.
        # LiteLLM requires "openai/" prefix to route to custom endpoints.
        if api_base and not model.startswith("openai/"):
            self._litellm_model = f"openai/{model}"
        else:
            self._litellm_model = model

    @property
    def provider_name(self) -> str:
        return "litellm"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
    ) -> GeneratedResponse:
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        try:
            return await self._generate_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id,
            )
        except RetryError as exc:
            raise ProviderError(
                "litellm",
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
    ) -> GeneratedResponse:
        # Import litellm lazily — it's a large package and only needed at call time
        import litellm  # type: ignore[import-untyped]

        # Suppress LiteLLM's verbose feedback/debug output
        litellm.set_verbose = False
        litellm.suppress_debug_info = True

        call_kwargs: dict[str, object] = {
            "model": self._litellm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._api_key:
            call_kwargs["api_key"] = self._api_key
        if self._api_base:
            call_kwargs["api_base"] = self._api_base
            if not self._api_key:
                call_kwargs["api_key"] = "sk-dummy"  # LiteLLM requires a non-empty key even for unauthenticated local proxies (OpenAI SDK requirement)

        try:
            response = await litellm.acompletion(**call_kwargs)
        except litellm.RateLimitError as exc:
            raise RateLimitError("litellm", str(exc), status_code=429) from exc
        except litellm.APIError as exc:
            raise ProviderError("litellm", str(exc)) from exc
        except Exception as exc:
            log.error("litellm.generate.error", model=self._model, error=str(exc))
            raise ProviderError("litellm", f"{type(exc).__name__}: {exc}") from exc

        usage = response.usage
        result = GeneratedResponse(
            content=response.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            cached_tokens=0,
            usage=dict(usage) if usage else {},
        )
        log.debug(
            "litellm.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            import asyncio

            try:
                asyncio.get_event_loop().create_task(
                    self._cost_tracker.record(
                        model=self._model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        operation="doc_generation",
                        file_path=None,
                    )
                )
            except RuntimeError:
                pass  # No running event loop — skip async record

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

        import litellm  # type: ignore[import-untyped]

        litellm.set_verbose = False
        litellm.suppress_debug_info = True

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        call_kwargs: dict[str, Any] = {
            "model": self._litellm_model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            call_kwargs["tools"] = tools
        if self._api_key:
            call_kwargs["api_key"] = self._api_key
        if self._api_base:
            call_kwargs["api_base"] = self._api_base
            if not self._api_key:
                call_kwargs["api_key"] = "sk-dummy"  # LiteLLM requires a non-empty key even for unauthenticated local proxies (OpenAI SDK requirement)

        try:
            stream = await litellm.acompletion(**call_kwargs)
        except litellm.RateLimitError as exc:
            raise RateLimitError("litellm", str(exc), status_code=429) from exc
        except litellm.APIError as exc:
            raise ProviderError("litellm", str(exc)) from exc

        tool_calls_acc: dict[int, dict[str, Any]] = {}

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta
                finish = choice.finish_reason

                if delta and getattr(delta, "content", None):
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

                if delta and getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": getattr(tc_delta, "id", "") or "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_calls_acc[idx]
                        if getattr(tc_delta, "id", None):
                            acc["id"] = tc_delta.id
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                acc["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                acc["arguments"] += fn.arguments

                if finish:
                    for idx in sorted(tool_calls_acc.keys()):
                        acc = tool_calls_acc[idx]
                        try:
                            args = _json.loads(acc["arguments"]) if acc["arguments"] else {}
                        except Exception:
                            args = {}
                        yield ChatStreamEvent(
                            type="tool_start",
                            tool_call=ChatToolCall(id=acc["id"], name=acc["name"], arguments=args),
                        )
                    tool_calls_acc.clear()
                    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
                    yield ChatStreamEvent(type="stop", stop_reason=stop_reason)
        except litellm.RateLimitError as exc:
            raise RateLimitError("litellm", str(exc), status_code=429) from exc
        except Exception as exc:
            raise ProviderError("litellm", f"{type(exc).__name__}: {exc}") from exc
