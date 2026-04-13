"""Z.AI (Zhipu AI) provider for repowise.

Z.AI provides competitive models (GLM-5 family) at accessible pricing through
two API plans:

    - coding: Subscription-based resource package (default)
    - general: Pay-as-you-go

Key features:
    - OpenAI-compatible API
    - GLM-5 family reasoning models with thinking disabled by default
    - Plan selection via constructor or ZAI_PLAN environment variable

Models:
    - glm-5-turbo
    - glm-5.1
    - glm-5
    - glm-4.7

Reasoning models (GLM-5 family) have thinking enabled by default, which consumes
85-95% of output tokens on chain-of-thought. This provider disables thinking by
default for efficient structured output generation.

Reference: https://open.bigmodel.cn/dev/api
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal

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
from repowise.core.rate_limiter import RateLimiter, ZAI_TIER_DEFAULTS

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 5
_MIN_WAIT = 1.0
_MAX_WAIT = 30.0

# Z.AI API endpoints by plan
_PLAN_BASE_URLS: dict[str, str] = {
    "coding": "https://api.z.ai/api/coding/paas/v4",
    "general": "https://api.z.ai/api/paas/v4",
}

# Default model for Z.AI
_DEFAULT_MODEL = "glm-5.1"

# Type for plan parameter
PlanType = Literal["coding", "general"]


class ZAIProvider(BaseProvider):
    """Z.AI (Zhipu AI) chat provider.

    Uses the OpenAI-compatible API with thinking disabled by default
    for efficient structured output generation.

    Args:
        model: Z.AI model name (e.g., 'glm-5.1', 'glm-5-turbo', 'glm-4.7').
               Defaults to 'glm-5.1'.
        api_key: API key for authentication. Reads from ZAI_API_KEY env var
                 if not provided.
        plan: API plan to use. 'coding' for subscription-based resource
              package, 'general' for pay-as-you-go. Defaults to 'coding'.
              Can also be set via ZAI_PLAN environment variable.
        base_url: Override API base URL. If provided, takes precedence
                  over plan selection.
        thinking: Thinking mode for GLM-5 family. 'disabled' by default
                  to avoid reasoning token overhead. Set to 'enabled' for
                  complex reasoning tasks.
        tier: Z.AI subscription tier for rate limiting. One of 'lite',
              'pro', 'max'. When set, overrides the default rate limiter
              with tier-appropriate limits. Can also be set via ZAI_TIER
              environment variable.
        rate_limiter: Optional RateLimiter instance. If not provided and
                      tier is set, a tier-appropriate limiter is created.
                      If neither is provided, the registry attaches a
                      conservative default.
        cost_tracker: Optional CostTracker for usage tracking.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        plan: PlanType = "coding",
        base_url: str | None = None,
        thinking: str = "disabled",
        tier: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: "CostTracker | None" = None,  # noqa: UP037
    ) -> None:
        self._model = model
        self._plan = plan
        self._thinking = thinking
        self._tier = tier
        self._cost_tracker = cost_tracker

        # Resolve rate limiter: tier > explicit instance > none (registry attaches default)
        # When tier is set, it takes precedence -- it's a specific Z.AI signal that
        # overrides the generic registry default.
        if tier is not None:
            tier_key = tier.lower()
            tier_config = ZAI_TIER_DEFAULTS.get(tier_key)
            if tier_config is None:
                valid = ", ".join(sorted(ZAI_TIER_DEFAULTS))
                msg = f"Unknown Z.AI tier {tier!r}. Valid tiers: {valid}"
                raise ValueError(msg)
            self._rate_limiter = RateLimiter(tier_config)
            log.info("zai.tier_rate_limiter", tier=tier_key, rpm=tier_config.requests_per_minute)
        elif rate_limiter is not None:
            self._rate_limiter = rate_limiter
        else:
            self._rate_limiter = None  # None — registry will attach default

        # Resolve base URL: explicit base_url > plan lookup
        effective_base_url = base_url or _PLAN_BASE_URLS.get(plan, _PLAN_BASE_URLS["coding"])

        # Normalize base URL for OpenAI SDK
        effective_base_url = effective_base_url.rstrip("/")
        if not effective_base_url.endswith("/v1"):
            effective_base_url += "/v1"

        # Store normalized base_url
        self._base_url = effective_base_url

        # Initialize OpenAI client
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=effective_base_url,
        )

    @property
    def provider_name(self) -> str:
        return "zai"

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
            "zai.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            thinking=self._thinking,
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
                "zai",
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

        # Disable thinking for GLM-5 family by default
        # This prevents reasoning tokens from consuming output budget
        if self._thinking == "disabled":
            call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        try:
            response = await self._client.chat.completions.create(**call_kwargs)
        except _OpenAIAPIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimitError("zai", str(exc), status_code=429) from exc
            raise ProviderError("zai", str(exc), status_code=exc.status_code) from exc
        except Exception as exc:
            log.error("zai.generate.error", model=self._model, error=str(exc))
            raise ProviderError("zai", f"{type(exc).__name__}: {exc}") from exc

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
            "zai.generate.done",
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
        """Stream chat via Z.AI's OpenAI-compatible endpoint."""
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

        # Disable thinking for GLM-5 family by default
        if self._thinking == "disabled":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except _OpenAIAPIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimitError("zai", str(exc), status_code=429) from exc
            raise ProviderError("zai", str(exc), status_code=exc.status_code) from exc

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
                raise RateLimitError("zai", str(exc), status_code=429) from exc
            raise ProviderError("zai", str(exc), status_code=exc.status_code) from exc