"""MiniMax provider for repowise.

MiniMax provides the M2.x family of models (M2.7, M2.5, M2.1, M2) with both
pay-as-you-go and token plan billing.

Key features:
    - OpenAI-compatible API
    - M2.x models with built-in reasoning (interleaved thinking)
    - reasoning_split parameter to separate thinking from content
    - Token plan tiers with published rate limits (Starter/Plus/Max/Ultra)

Models:
    - MiniMax-M2.7 (recommended)
    - MiniMax-M2.7-highspeed (faster inference)
    - MiniMax-M2.5 (code generation)
    - MiniMax-M2.5-highspeed
    - MiniMax-M2.1 (multi-language)
    - MiniMax-M2.1-highspeed
    - MiniMax-M2 (agentic/reasoning)

M2.x models include interleaved thinking by default. The reasoning_split
parameter separates thinking content into a dedicated field, keeping the
main content clean for structured output generation.

Rate limits are per-token-plan-tier on a 5-hour rolling window:
    - Starter:  1,500 requests/5hrs (~5 RPM)
    - Plus:     4,500 requests/5hrs (~15 RPM)
    - Max:     15,000 requests/5hrs (~50 RPM)
    - Ultra:   30,000 requests/5hrs (~100 RPM)

Reference: https://platform.minimax.io/docs
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from openai import APIStatusError as _OpenAIAPIStatusError
from openai import AsyncOpenAI
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
from repowise.core.rate_limiter import RateLimitConfig, RateLimiter

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 5
_MIN_WAIT = 2.0
_MAX_WAIT = 30.0

# MiniMax API base URL (already includes /v1 for OpenAI-compatible endpoint)
_BASE_URL = "https://api.minimax.io/v1"

# Default model for MiniMax
_DEFAULT_MODEL = "MiniMax-M2.7"


class MiniMaxProvider(BaseProvider):
    """MiniMax chat provider.

    Uses the OpenAI-compatible API with reasoning_split enabled by default
    for clean structured output generation.

    Args:
        model: MiniMax model name (e.g., 'MiniMax-M2.7', 'MiniMax-M2.7-highspeed').
               Defaults to 'MiniMax-M2.7'.
        api_key: API key for authentication. Reads from MINIMAX_API_KEY env var
                 if not provided.
        base_url: Override API base URL. Defaults to 'https://api.minimax.io/v1'.
        reasoning_split: When True (default), separates thinking content into a
                         dedicated field instead of mixing it into the response.
                         Disable only if you want raw interleaved output.
        tier: MiniMax token plan tier for rate limiting. One of 'starter',
              'plus', 'max', 'ultra'. When set, overrides the default rate
              limiter with tier-appropriate limits. Can also be set via
              MINIMAX_TIER environment variable.
        rate_limiter: Optional RateLimiter instance. If not provided and tier
                      is set, a tier-appropriate limiter is created. If neither
                      is provided, the registry attaches a conservative default.
        cost_tracker: Optional CostTracker for usage tracking.
    """

    # MiniMax token plan rate limits.
    # Derived from published limits (requests per 5-hour rolling window):
    #   - Starter:  1,500 req/5hrs (~5 RPM)
    #   - Plus:     4,500 req/5hrs (~15 RPM)
    #   - Max:     15,000 req/5hrs (~50 RPM)
    #   - Ultra:   30,000 req/5hrs (~100 RPM)
    # Highspeed variants share the same rate limits as their base plan --
    # the difference is model selection (faster inference), not quota.
    # Ref: https://platform.minimax.io/docs/token-plan/intro
    RATE_LIMIT_TIERS: dict[str, RateLimitConfig] = {
        "starter": RateLimitConfig(requests_per_minute=5, tokens_per_minute=25_000),
        "plus": RateLimitConfig(requests_per_minute=15, tokens_per_minute=75_000),
        "max": RateLimitConfig(requests_per_minute=50, tokens_per_minute=250_000),
        "ultra": RateLimitConfig(requests_per_minute=100, tokens_per_minute=500_000),
    }

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_split: bool = True,
        tier: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: "CostTracker | None" = None,  # noqa: UP037
    ) -> None:
        self._model = model
        self._reasoning_split = reasoning_split
        self._tier = tier
        self._cost_tracker = cost_tracker

        # Resolve rate limiter: tier > explicit instance > none (registry attaches default)
        self._rate_limiter = self.resolve_rate_limiter(
            tier=tier,
            tiers=self.RATE_LIMIT_TIERS,
            rate_limiter=rate_limiter,
        )
        if tier is not None and self._rate_limiter is not None:
            log.info(
                "minimax.tier_rate_limiter",
                tier=tier.lower(),
                rpm=self._rate_limiter.config.requests_per_minute,
            )

        # Resolve base URL
        effective_base_url = base_url or _BASE_URL

        # Store normalized base_url
        self._base_url = effective_base_url

        # Initialize OpenAI client
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=effective_base_url,
        )

    @property
    def provider_name(self) -> str:
        return "minimax"

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

        log.debug(
            "minimax.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            reasoning_split=self._reasoning_split,
            request_id=request_id,
        )

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
                "minimax",
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
        # Build request kwargs
        call_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Enable reasoning_split by default to separate thinking from content
        if self._reasoning_split:
            call_kwargs["extra_body"] = {"reasoning_split": True}

        try:
            response = await self._client.chat.completions.create(**call_kwargs)
        except _OpenAIAPIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimitError("minimax", str(exc), status_code=429) from exc
            raise ProviderError("minimax", str(exc), status_code=exc.status_code) from exc
        except Exception as exc:
            log.error("minimax.generate.error", model=self._model, error=str(exc))
            raise ProviderError("minimax", f"{type(exc).__name__}: {exc}") from exc

        usage = response.usage
        result = GeneratedResponse(
            content=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=0,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
        )

        log.debug(
            "minimax.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            import asyncio

            try:  # noqa: SIM105
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
                pass  # No running event loop -- skip async record

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
        """Stream chat via MiniMax's OpenAI-compatible endpoint."""
        import json as _json

        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        if tools:
            kwargs["tools"] = tools

        # Enable reasoning_split by default
        if self._reasoning_split:
            kwargs["extra_body"] = {"reasoning_split": True}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIAPIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimitError("minimax", str(exc), status_code=429) from exc
            raise ProviderError("minimax", str(exc), status_code=exc.status_code) from exc

        tool_calls_acc: dict[int, dict[str, Any]] = {}

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta
                finish = choice.finish_reason

                if delta and delta.content:
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": tc_delta.id or "", "name": "", "arguments": ""}
                        acc = tool_calls_acc[idx]
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                acc["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                acc["arguments"] += tc_delta.function.arguments

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
        except _OpenAIAPIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimitError("minimax", str(exc), status_code=429) from exc
            raise ProviderError("minimax", str(exc), status_code=exc.status_code) from exc
