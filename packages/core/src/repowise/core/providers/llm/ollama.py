"""Ollama provider for repowise.

Ollama enables fully offline, local LLM inference. It exposes an OpenAI-compatible
API endpoint, so this provider uses the OpenAI client internally.

No API key required for local deployments. This makes repowise usable in:
    - Air-gapped environments
    - High-security codebases that cannot send code to cloud APIs
    - Cost-sensitive projects

Popular models (pull with `ollama pull <model>`):
    - qwen3.5:4b        — good general-purpose small model (default)
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
from openai import AsyncOpenAI
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    ensure_reasoning_supported,
    fallback_model_option,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    record_generation_cost,
)
from repowise.core.providers.llm.openai_compat import (
    completion_to_response,
    stream_openai_chat,
    translate_openai_errors,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_OLLAMA_REASONING_MODES: tuple[ReasoningMode, ...] = ("off",)


def _normalize_base_url(url: str) -> str:
    """Ensure base_url ends with /v1 for OpenAI SDK compatibility."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _ollama_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    """Translate a validated repowise reasoning intent to Ollama kwargs."""
    if reasoning == "off":
        return {"reasoning_effort": "none"}
    return {}


def _ollama_model_options(
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    reasoning_modes = ("auto", *_OLLAMA_REASONING_MODES)
    fallback = fallback_model_option(
        fallback_model,
        reasoning_modes=reasoning_modes,
    )
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
                reasoning_modes=reasoning_modes,
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
        model:        Ollama model name (e.g., 'qwen3.5:4b', 'llama3.2').
                      Must be pulled first: `ollama pull <model>`
        base_url:     Ollama server URL. Defaults to http://localhost:11434.
                      The /v1 suffix is appended automatically if missing.
        rate_limiter: Optional RateLimiter (useful when running multiple
                      concurrent requests against a resource-constrained machine).
    """

    # Generation speed here is the user's own hardware, and a cold model pays
    # a load from disk on top. Two minutes covers a mid-size local model on a
    # laptop; the 30s default cancels one before it finishes warming up.
    interactive_timeout_s: float = 120.0

    def __init__(
        self,
        model: str = "qwen3.5:4b",
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

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_OLLAMA_REASONING_MODES)

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
        reasoning_mode = ensure_reasoning_supported(
            "ollama",
            self._model,
            reasoning,
            _OLLAMA_REASONING_MODES,
            detail=(
                "Ollama maps reasoning='off' to reasoning_effort='none' "
                "through its OpenAI-compatible chat completions API."
            ),
        )
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "ollama.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            request_id=request_id,
        )

        try:
            result = await self._generate_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id,
                reasoning=reasoning_mode,
            )
        except RetryError as exc:
            raise ProviderError(
                "ollama",
                f"All retries exhausted: {exc}",
            ) from exc

        # Record the call so a local index still shows accurate call/token
        # counts on the Costs page — priced at $0, since the ``ollama/`` prefix
        # marks it local (see ``is_local_model``). Recorded in the outer method
        # (not the @retry-wrapped inner one) so a retry can never double-count.
        # The tracker is attached externally by the orchestrator, so it may be
        # absent.
        await record_generation_cost(
            getattr(self, "_cost_tracker", None), model=f"ollama/{self._model}", result=result
        )
        return result

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
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        request_kwargs.update(_ollama_reasoning_kwargs(reasoning))
        with translate_openai_errors("ollama"):
            response = await self._client.chat.completions.create(**request_kwargs)

        result = completion_to_response(response, include_total_tokens=False)
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

        async for event in stream_openai_chat(self._client, "ollama", kwargs, emit_usage=False):
            yield event
