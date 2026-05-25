"""Ollama provider for repowise.

Ollama enables fully offline, local LLM inference. It exposes an OpenAI-compatible
API endpoint, so this provider uses the OpenAI client internally.

No API key required for local deployments. This makes repowise usable in:
    - Air-gapped environments
    - High-security codebases that cannot send code to cloud APIs
    - Cost-sensitive projects

Popular models (pull with `ollama pull <model>`):
    - llama3.2          — good general-purpose, 3B/11B variants
    - codellama         — code-focused, good for doc generation
    - deepseek-coder-v2 — strong on code understanding
    - qwen2.5-coder     — excellent multilingual code model

Usage:
    provider = OllamaProvider(model="codellama", base_url="http://localhost:11434")
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

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
    ProviderModelOption,
    ensure_reasoning_supported,
    fallback_model_option,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 8.0  # Ollama can be slow on first load, allow more wait time

_DEFAULT_BASE_URL = "http://localhost:11434"


def _normalize_base_url(url: str) -> str:
    """Ensure base_url ends with /v1 for OpenAI SDK compatibility."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _ollama_model_options(
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    fallback = fallback_model_option(fallback_model)
    try:
        import httpx

        response = httpx.get(
            f"{base_url.rstrip('/')}/api/tags",
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json().get("models", [])
    except Exception:
        return (fallback,)

    if not isinstance(data, list):
        return (fallback,)

    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        model_id = raw.get("name") or raw.get("model")
        if not isinstance(model_id, str) or not model_id:
            continue
        details = raw.get("details")
        notes = ""
        if isinstance(details, dict):
            family = details.get("family")
            params = details.get("parameter_size")
            parts = [part for part in (family, params) if isinstance(part, str)]
            notes = ", ".join(parts) or notes
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=("auto",),
                recommended=model_id == fallback_model,
                source="local",
                notes=notes,
            )
        )

    if not options:
        return (fallback,)

    options.sort(key=lambda option: option.model)
    return tuple(options)


class OllamaProvider(BaseProvider):
    """Ollama provider for local, offline LLM inference.

    Uses Ollama's OpenAI-compatible endpoint. No API key required.

    Args:
        model:        Ollama model name (e.g., 'llama3.2', 'codellama').
                      Must be pulled first: `ollama pull <model>`
        base_url:     Ollama server URL. Defaults to http://localhost:11434.
                      The /v1 suffix is appended automatically if missing.
        rate_limiter: Optional RateLimiter (useful when running multiple
                      concurrent requests against a resource-constrained machine).
    """

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        resolved_base_url = base_url or os.environ.get("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL
        self._base_url = resolved_base_url.rstrip("/")
        self._client = AsyncOpenAI(
            api_key="ollama", base_url=_normalize_base_url(resolved_base_url)
        )
        self._model = model
        self._rate_limiter = rate_limiter

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _ollama_model_options(self._base_url, self._model)

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
        ensure_reasoning_supported("ollama", self._model, reasoning)
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "ollama.generate.start",
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
            )
        except RetryError as exc:
            raise ProviderError(
                "ollama",
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
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("ollama", str(exc), status_code=exc.status_code) from exc

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
            "ollama.generate.done",
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
        """Stream chat via Ollama's OpenAI-compatible endpoint."""
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
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("ollama", str(exc), status_code=exc.status_code) from exc

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
                            tool_call=ChatToolCall(id=acc["id"], name=acc["name"], arguments=args),
                        )
                    tool_calls_acc.clear()
                    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
                    yield ChatStreamEvent(type="stop", stop_reason=stop_reason)
        except _OpenAIAPIStatusError as exc:
            raise ProviderError("ollama", str(exc), status_code=exc.status_code) from exc
