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

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from openai import AsyncOpenAI
from tenacity import RetryError, retry

from repowise.core.providers.llm.base import (
    LOCAL_CONTEXT_WINDOW,
    BaseProvider,
    ChatStreamEvent,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    RateLimitError,
    SdkClientOwner,
    ensure_reasoning_supported,
    fallback_model_option,
    normalize_stop_reason,
    parse_retry_after,
    provider_retry_stop,
    provider_retry_wait,
    provider_should_retry,
    record_generation_cost,
)
from repowise.core.providers.llm.openai_compat import stream_openai_chat
from repowise.core.providers.llm.specs import PROVIDER_SPECS
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_OLLAMA_REASONING_MODES: tuple[ReasoningMode, ...] = ("off", "low", "medium", "high")
# Room kept for the answer when sizing num_ctx. A page answer runs 1-3k tokens
# once thinking is off; reserving the whole max_tokens (16k) doubled the window
# for every prompt, a two-line one included. A thinking call keeps max_tokens.
_ANSWER_RESERVE = 4096
# Page prompts measured 3.4 characters per token on qwen3.5 (code and prose).
# Three leaves headroom; a denser prompt than that grows the window instead.
_CHARS_PER_TOKEN = 3
# Streamed, so the read timeout bounds silence between chunks, not the whole
# page. It still has to cover prompt evaluation, which streams nothing.
_STREAM_TIMEOUT = httpx.Timeout(1200.0, connect=10.0)


def _normalize_base_url(url: str) -> str:
    """Ensure base_url ends with /v1 for OpenAI SDK compatibility."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _positive_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


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


@dataclass(frozen=True)
class _ModelFacts:
    trained: int | None = None  # context length the model was trained with
    num_ctx: int | None = None  # a window set in the model's Modelfile
    thinks: bool = False


def _read_model_facts(base_url: str, model: str) -> _ModelFacts:
    try:
        response = httpx.post(f"{base_url}/api/show", json={"model": model}, timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return _ModelFacts()
    if not isinstance(data, dict):
        return _ModelFacts()
    info = data.get("model_info") or {}
    trained = next(
        (v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)),
        None,
    )
    return _ModelFacts(
        trained,
        _modelfile_num_ctx(str(data.get("parameters") or "")),
        "thinking" in (data.get("capabilities") or ()),
    )


def _modelfile_num_ctx(parameters: str) -> int | None:
    """``num_ctx`` from /api/show's ``parameters``, one ``name value`` pair per line."""
    for line in parameters.splitlines():
        key, _, value = line.strip().partition(" ")
        if key == "num_ctx" and value.strip().isdigit():
            return int(value.strip())
    return None


class OllamaProvider(SdkClientOwner, BaseProvider):
    """Ollama provider for local, offline LLM inference.

    Generation uses Ollama's native chat API; streaming chat uses the
    OpenAI-compatible endpoint. No API key required.

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
        model: str = PROVIDER_SPECS["ollama"].default_model,
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        resolved_base_url = base_url or os.environ.get("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL
        self._base_url = resolved_base_url.rstrip("/")
        self._open_client(
            lambda: AsyncOpenAI(api_key="ollama", base_url=_normalize_base_url(resolved_base_url))
        )
        self._native_url = self._base_url.removesuffix("/v1")
        self._model = model
        self._rate_limiter = rate_limiter
        self._fixed_num_ctx = _positive_int_env("REPOWISE_OLLAMA_NUM_CTX")
        self._num_ctx = 0
        self._facts: _ModelFacts | None = None
        self._parallel = _positive_int_env("OLLAMA_NUM_PARALLEL") or 1
        self._slots: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto", *_OLLAMA_REASONING_MODES)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _ollama_model_options(self._native_url, self._model)

    def context_window(self) -> int:
        """REPOWISE_OLLAMA_NUM_CTX, else the model's own num_ctx, else a small local window.

        Never more than the context length the model was trained with.
        """
        if self._fixed_num_ctx:
            return self._fixed_num_ctx
        facts = self._model_facts()
        window = facts.num_ctx or LOCAL_CONTEXT_WINDOW
        return min(window, facts.trained) if facts.trained else window

    def _model_facts(self) -> _ModelFacts:
        """What /api/show says about the model, read once; empty when the server can't say."""
        if self._facts is not None:
            return self._facts
        facts = _read_model_facts(self._native_url, self._model)
        if facts != _ModelFacts():  # a server still starting is asked again next call
            self._facts = facts
        return facts

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
                "Ollama maps reasoning='off' to think=false and low/medium/high "
                "to think=true on its chat API."
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
        # The native endpoint, because the OpenAI-compatible one ignores
        # num_ctx and silently cuts every prompt to the server's default window.
        facts = await asyncio.to_thread(self._model_facts)  # off the loop: a blocking read
        think: bool | None = None
        if reasoning != "auto":
            think = reasoning != "off"
        elif facts.thinks:
            # Thinking multiplies a local page's time several times over (one
            # short outline call thought for 16k tokens on qwen3.5:4b) for no
            # better prose, so ``auto`` turns it off; low/medium/high turn it on.
            think = False
        # A thinking answer can run to max_tokens, so it gets the room for it.
        answer = max_tokens if think else min(max_tokens, _ANSWER_RESERVE)
        options = {
            "num_predict": max_tokens,
            "temperature": temperature,
            "num_ctx": self._context_window(system_prompt, user_prompt, answer),
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
            "options": options,
        }
        if think is not None:
            payload["think"] = think

        content, final = await self._stream_native_chat(payload)
        input_tokens = int(final.get("prompt_eval_count") or 0)
        if input_tokens >= options["num_ctx"] - answer:
            # Ollama keeps the prompt's tail when it overflows num_ctx, and
            # the instructions sit at its head: the page is likely off-brief.
            log.warning(
                "ollama.prompt_filled_window",
                input_tokens=input_tokens,
                num_ctx=options["num_ctx"],
                hint="set REPOWISE_OLLAMA_NUM_CTX higher",
            )
        output_tokens = int(final.get("eval_count") or 0)
        stop_reason, provider_stop_reason = normalize_stop_reason(final.get("done_reason"))
        result = GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
            usage={"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
        )
        log.debug(
            "ollama.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            num_ctx=options["num_ctx"],
            request_id=request_id,
        )
        return result

    async def _stream_native_chat(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """POST to /api/chat and return the streamed text and the final chunk."""
        parts: list[str] = []
        final: dict[str, Any] = {}
        try:
            async with (
                self._request_slots(),
                httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as http,
                http.stream("POST", f"{self._native_url}/api/chat", json=payload) as resp,
            ):
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    with contextlib.suppress(ValueError, KeyError, TypeError):
                        body = str(json.loads(body)["error"])
                    if resp.status_code == 429:
                        raise RateLimitError(
                            "ollama",
                            body,
                            status_code=429,
                            retry_after=parse_retry_after(resp.headers),
                        )
                    raise ProviderError("ollama", body, status_code=resp.status_code)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise ProviderError("ollama", str(chunk["error"]))
                    parts.append((chunk.get("message") or {}).get("content") or "")
                    if chunk.get("done"):
                        final = chunk
        except httpx.HTTPError as exc:
            raise ProviderError("ollama", f"{type(exc).__name__}: {exc}") from exc
        except ValueError as exc:
            raise ProviderError("ollama", f"unreadable response: {exc}") from exc
        return "".join(parts), final

    def _context_window(self, system_prompt: str, user_prompt: str, answer: int) -> int:
        """num_ctx for this request: the model's window, doubled while the prompt overflows it.

        Page material is sized to :meth:`context_window`, so a page prompt fits
        it; a prompt built without a budget, or a thinking answer, grows it,
        never past what the model was trained with. Ollama reloads the model whenever num_ctx
        changes, so the window never shrinks between calls.
        """
        if self._fixed_num_ctx:
            return self._fixed_num_ctx
        chars = len(system_prompt) + len(user_prompt)
        needed = chars // _CHARS_PER_TOKEN + answer
        window = self.context_window()
        while window < needed:
            window *= 2
        trained = self._model_facts().trained
        if trained:
            window = min(window, trained)
        self._num_ctx = max(self._num_ctx, window)
        return self._num_ctx

    def _request_slots(self) -> asyncio.Semaphore:
        """Cap in-flight requests at what the server runs at once.

        Requests past OLLAMA_NUM_PARALLEL queue inside the server, where the
        wait counts against their timeout. Queueing here instead keeps the
        timeout about generation. One semaphore per event loop.
        """
        loop = asyncio.get_running_loop()
        if self._slots is None or self._slots[0] is not loop:
            self._slots = (loop, asyncio.Semaphore(self._parallel))
        return self._slots[1]

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
