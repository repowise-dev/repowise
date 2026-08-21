"""Azure OpenAI provider for repowise.

BYOK for Azure OpenAI in your tenant — the endpoint the commercial docs
promise at ``docs/business/COMMERCIAL.md:319``. The existing ``OPENAI_BASE_URL``
workaround fails at runtime (Bearer vs api-key header, missing api-version,
deployment vs model).

Supports:
- API-key auth (``AZURE_OPENAI_API_KEY`` + ``AZURE_OPENAI_ENDPOINT``)
- Entra ID (``azure_ad_token_provider`` callback when no key)
- api_version via ``AZURE_OPENAI_API_VERSION`` (default ``2024-12-01-preview``)
  and per-deployment model id (deployment name == model id on Azure)
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
    CacheHint,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    RateLimitError,
    fallback_model_option,
    normalize_stop_reason,
    parse_retry_after,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_DEFAULT_API_VERSION = "2024-12-01-preview"
_DEFAULT_MODEL = "gpt-5.6-luna"


def _resolve_azure_client(
    *,
    api_key: str | None,
    azure_endpoint: str,
    api_version: str,
    azure_ad_token_provider: Any | None = None,
) -> Any:
    """Return an async Azure OpenAI client.

    Prefers ``AsyncAzureOpenAI`` when available (correct api-key header + api-version
    query). Falls back to ``AsyncOpenAI`` with ``base_url`` set to the Azure
    endpoint and ``api-key`` header for environments where the azure extra is not
    installed.
    """
    try:
        from openai import AsyncAzureOpenAI  # type: ignore[attr-defined]

        kwargs: dict[str, Any] = {
            "azure_endpoint": azure_endpoint,
            "api_version": api_version,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if azure_ad_token_provider is not None:
            kwargs["azure_ad_token_provider"] = azure_ad_token_provider
        return AsyncAzureOpenAI(**kwargs)
    except (ImportError, AttributeError):
        # Fallback — construct the Azure URL manually. Azure expects:
        # https://{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...
        # The SDK handles this when base_url is the endpoint root and the model is the deployment.
        headers = {}
        # Azure uses api-key header, not Bearer.
        if api_key:
            headers["api-key"] = api_key
        return AsyncOpenAI(
            api_key=api_key or "azure-ad-token",
            base_url=azure_endpoint.rstrip("/"),
            default_headers=headers or None,
            default_query={"api-version": api_version} if api_version else None,
        )


def _azure_model_options(
    api_key: str | None,
    azure_endpoint: str | None,
    api_version: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(fallback_model, reasoning_modes=("auto",))
    if not azure_endpoint:
        return (fallback,)
    # Listing deployments requires management-plane auth and is rarely granted
    # to data-plane keys, so we return the fallback only. A future enhancement
    # can call https://management.azure.com/.../deployments when a token is available.
    return (fallback,)


class AzureOpenAIProvider(BaseProvider):
    """Azure OpenAI provider (BYOK in your tenant).

    Args:
        api_key: Azure OpenAI API key. Falls back to ``AZURE_OPENAI_API_KEY``.
        azure_endpoint: Azure endpoint, e.g. ``https://my-res.openai.azure.com``.
            Falls back to ``AZURE_OPENAI_ENDPOINT`` or ``AZURE_OPENAI_BASE_URL``.
        api_version: API version, e.g. ``2024-12-01-preview``. Falls back to
            ``AZURE_OPENAI_API_VERSION``.
        model: Deployment name on Azure (== model id for pricing). Falls back to
            ``AZURE_OPENAI_DEPLOYMENT`` or ``gpt-5.6-luna``.
        azure_ad_token_provider: Optional Entra ID token provider callback.
        rate_limiter: Optional RateLimiter.
        cost_tracker: Optional CostTracker.
    """

    def __init__(
        self,
        api_key: str | None = None,
        azure_endpoint: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        model: str | None = None,
        azure_ad_token_provider: Any | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
        resolved_endpoint = (
            azure_endpoint
            or base_url
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_BASE_URL")
            or os.environ.get("AZURE_OPENAI_API_BASE")
        )
        resolved_version = (
            api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or _DEFAULT_API_VERSION
        )
        resolved_model = model or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or _DEFAULT_MODEL

        if not resolved_endpoint:
            raise ProviderError(
                "azure_openai",
                "No Azure endpoint provided. Pass azure_endpoint= or set AZURE_OPENAI_ENDPOINT.",
            )
        if not resolved_key and azure_ad_token_provider is None:
            # Allow keyless when Entra ID provider is supplied programmatically.
            raise ProviderError(
                "azure_openai",
                "No API key provided. Pass api_key=, set AZURE_OPENAI_API_KEY, or supply azure_ad_token_provider.",
            )

        # Normalize endpoint — ensure it is a full https URL.
        if not resolved_endpoint.startswith("http"):
            resolved_endpoint = f"https://{resolved_endpoint}"

        self._api_key = resolved_key
        self._azure_endpoint = resolved_endpoint
        self._api_version = resolved_version
        self._model = resolved_model
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker
        self._client = _resolve_azure_client(
            api_key=resolved_key,
            azure_endpoint=resolved_endpoint,
            api_version=resolved_version,
            azure_ad_token_provider=azure_ad_token_provider,
        )

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto",)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _azure_model_options(
            self._api_key, self._azure_endpoint, self._api_version, self._model
        )

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
        del cache_hints, reasoning
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "azure_openai.generate.start",
            model=self._model,
            endpoint=self._azure_endpoint,
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
            )
        except RetryError as exc:
            raise ProviderError(
                "azure_openai",
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
    ) -> GeneratedResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except _OpenAIRateLimitError as exc:
            raise RateLimitError(
                "azure_openai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("azure_openai", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "azure_openai", str(exc), status_code=getattr(exc, "status_code", None)
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
            "azure_openai.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            with contextlib.suppress(Exception):
                await self._cost_tracker.record(
                    model=self._model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    operation=self._cost_tracker.operation,
                    file_path=None,
                )

        return result

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
            raise RateLimitError(
                "azure_openai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("azure_openai", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "azure_openai", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc

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

                if delta and delta.content:
                    yield ChatStreamEvent(type="text_delta", text=delta.content)

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
                "azure_openai",
                str(exc),
                status_code=429,
                retry_after=parse_retry_after(
                    getattr(getattr(exc, "response", None), "headers", None)
                ),
            ) from exc
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("azure_openai", str(exc), status_code=exc.status_code) from exc
        except _OpenAIAPIError as exc:
            raise ProviderError(
                "azure_openai", str(exc), status_code=getattr(exc, "status_code", None)
            ) from exc
