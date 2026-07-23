"""Base provider interface and response types for repowise's LLM abstraction layer.

repowise is model-agnostic by design. Any LLM — cloud or local — that implements
BaseProvider can be used for documentation generation without changing any other code.

Adding a new provider:
    1. Create a new module in this package (e.g., providers/my_provider.py)
    2. Subclass BaseProvider and implement generate(), provider_name, model_name
    3. Register it in registry.py (or call register_provider() at runtime)
    4. Add tests in tests/providers/
    See CONTRIBUTING.md for a step-by-step walkthrough.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from repowise.core.reasoning import ReasoningMode, normalize_reasoning

CacheSegment = Literal["system", "user_prefix"]
ModelOptionSource = Literal["api", "local", "fallback"]


def normalize_stop_reason(reason: object) -> tuple[str | None, str | None]:
    """Return ``(normalized, provider-native)`` completion stop reasons.

    Non-streaming adapters use the same neutral values as ``ChatStreamEvent``.
    Unknown provider values are retained verbatim so failures remain
    diagnosable without teaching the provider-neutral layer every vendor enum.
    """
    if reason is None:
        return None, None

    raw_reason = reason if isinstance(reason, str) else getattr(reason, "value", None)
    if not isinstance(raw_reason, str) or not raw_reason.strip():
        return None, None

    provider_stop_reason = raw_reason.strip()
    normalized_key = provider_stop_reason.lower()
    aliases = {
        "stop": "end_turn",
        "end_turn": "end_turn",
        "stop_sequence": "end_turn",
        "length": "max_tokens",
        "max_tokens": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "tool_use": "tool_use",
    }
    return aliases.get(normalized_key), provider_stop_reason


@dataclass(frozen=True)
class ProviderModelOption:
    """A model choice exposed by a provider for interactive configuration."""

    model: str
    label: str | None = None
    reasoning_modes: tuple[ReasoningMode, ...] = ("auto",)
    recommended: bool = False
    source: ModelOptionSource = "fallback"
    notes: str = ""


def fallback_model_option(
    model: str,
    *,
    label: str | None = None,
    reasoning_modes: tuple[ReasoningMode, ...] = ("auto",),
    notes: str = "model list unavailable; using configured model",
) -> ProviderModelOption:
    """Return the single fallback option used when live discovery fails."""

    return ProviderModelOption(
        model=model,
        label=label or model,
        reasoning_modes=reasoning_modes,
        recommended=True,
        source="fallback",
        notes=notes,
    )


@dataclass(frozen=True)
class CacheHint:
    """Caller-provided hint that a prompt segment is reusable across calls.

    Providers that support server-side prompt caching (Anthropic) use these
    hints to mark cache breakpoints. Providers without an explicit caching
    primitive (OpenAI auto-caches stable prefixes, Ollama is local) ignore
    them — the contract is advisory, never required.

    Attributes:
        segment: Which part of the prompt the hint applies to.
                 - ``system``: the system_prompt argument.
                 - ``user_prefix``: a leading portion of the user_prompt;
                   ``prefix_chars`` specifies how many chars are stable.
        prefix_chars: For ``user_prefix`` hints, the number of leading
                      characters that are reusable. Ignored for ``system``.
    """

    segment: CacheSegment
    prefix_chars: int = 0


@dataclass
class GeneratedResponse:
    """Unified response shape returned by every provider.

    All token counts use the provider's own counting method. For cross-provider
    cost comparison, use the cost_usd fields in GenerationJob (computed from
    known per-token prices), not raw token counts.

    Attributes:
        content:       The generated text content (markdown).
        input_tokens:  Tokens consumed by the prompt (system + user).
        output_tokens: Tokens produced in the response.
        cached_tokens: Tokens served from the provider's prompt cache (if any).
                       Normalised across providers by the adapter.
        usage:         Provider-specific usage dict (stored as-is for auditing).
        decisions:     Optional structured side-channel: candidate architectural
                       decisions the model surfaced while writing the page
                       (Phase-2 LLM-docs harvest). Populated by the generator
                       from a trailing JSON block (or native structured output)
                       and stripped from ``content`` before storage; ``None``
                       when nothing was harvested. Each item is a raw dict
                       carrying at least ``title`` + ``source_quote``; the
                       generator gates them against the file source before use.
        stop_reason:   Provider-neutral completion reason. Uses the same values
                       as streaming chat (notably ``end_turn``, ``max_tokens``,
                       and ``tool_use``).
        provider_stop_reason:
                       Provider-native completion reason retained for diagnosis.
    """

    content: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict] | None = None
    stop_reason: str | None = None
    provider_stop_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output, excluding cache hits)."""
        return self.input_tokens + self.output_tokens


class BaseProvider(ABC):
    """Abstract base class that all LLM providers must implement.

    repowise is model-agnostic. Any LLM that implements this interface
    can be used for documentation generation. The rate limiter is injected
    at construction time and called transparently inside generate().

    Implementors must:
    - Be async (generate() must be a coroutine)
    - Return GeneratedResponse with correct token counts
    - Raise ProviderError on non-recoverable API errors
    - Raise RateLimitError on 429 responses after retries are exhausted
    """

    @abstractmethod
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
        """Generate a response from the LLM.

        Args:
            system_prompt: System-level instructions (role, output format, rules).
            user_prompt:   User-level content — typically a rendered Jinja2 template
                           containing the code context and documentation request.
            max_tokens:    Maximum tokens in the completion. Providers may enforce
                           lower limits; the provider should clip, not raise.
            temperature:   Sampling temperature. 0.0 is fully deterministic.
                           repowise uses 0.3 for consistent doc style.
            request_id:    Optional trace ID for logging and debugging.
            reasoning:     Provider-level reasoning intent. ``auto`` preserves
                           provider defaults; explicit modes are translated by
                           providers that support them.
            cache_hints:   Optional hints that one or more prompt segments are
                           reusable across calls. Providers with an explicit
                           caching primitive (Anthropic) use them; others
                           ignore them safely.

        Returns:
            GeneratedResponse with content and token usage.

        Raises:
            ProviderError:   On API errors after all retries are exhausted.
            RateLimitError:  If rate limits cannot be resolved (permanent 429).
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short, stable identifier for this provider.

        Used in logs, database records, and config files.
        Examples: 'anthropic', 'openai', 'ollama', 'litellm', 'mock'.
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The specific model identifier being used.

        Examples: 'claude-sonnet-4-6', 'gpt-4o', 'llama3.2'.
        Stored on every generated page for attribution and reproducibility.
        """
        ...

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        """Return reasoning modes supported by this provider/model.

        ``auto`` is always present because it means "preserve provider defaults".
        Providers with model-specific support should override this method and
        return the exact explicit modes they can translate before an API call.
        """

        return ("auto",)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        """Return model options available for interactive provider setup."""

        return (
            ProviderModelOption(
                model=self.model_name,
                label=self.model_name,
                reasoning_modes=self.supported_reasoning_modes(),
                recommended=True,
                source="fallback",
            ),
        )


class ProviderError(Exception):
    """Raised when a provider returns an unrecoverable error.

    Attributes:
        provider:    The provider that raised the error ('anthropic', etc.)
        status_code: HTTP status code if available (e.g., 500, 503).
    """

    def __init__(
        self,
        provider: str,
        message: str,
        status_code: int | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class RateLimitError(ProviderError):
    """Raised on a 429 response from the provider.

    This is a sub-class of ProviderError. Callers can catch either.
    Rate-limit 429s are *recoverable* — the shared retry policy
    (``provider_retry_*`` below) backs off patiently (respecting the
    provider's ``retry-after`` when known) before giving up.

    Attributes:
        retry_after: Seconds the provider asked us to wait before retrying,
                     parsed from the ``retry-after`` header (or provider
                     equivalent). ``None`` when the provider didn't say.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        status_code: int | None = 429,
        retry_after: float | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(provider, message, status_code=status_code)


def parse_retry_after(headers: Any) -> float | None:
    """Best-effort parse of a ``retry-after`` header value in seconds.

    Accepts any mapping-like object with ``.get`` (httpx.Headers, dict).
    Returns ``None`` when absent or unparseable. HTTP-date form is ignored —
    providers we call send delta-seconds.
    """
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is None:
            return None
        seconds = float(raw)
        return seconds if 0 < seconds <= 600 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shared tenacity retry policy for all HTTP providers
#
# Philosophy: the client-side RateLimiter is only a flood guard with very
# generous defaults; the provider's own 429s are the real throttle signal.
# 429s therefore get a patient, retry-after-aware backoff (they resolve
# within the provider's rate window), while transient 5xx/network errors get
# a short exponential retry, and non-retryable 4xx (400/401/403/404/422)
# fail immediately instead of burning three pointless attempts.
# ---------------------------------------------------------------------------

_TRANSIENT_ATTEMPTS = 3  # 5xx / network / unknown errors
_RATE_LIMIT_ATTEMPTS = 6  # 429s — waits 2+4+8+16+30 ≈ one full rate window
_RATE_LIMIT_MAX_WAIT = 30.0  # per-attempt cap; cumulative covers the window
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 405, 413, 422})

# Multiplier applied to every computed wait. Production leaves this at 1.0;
# tests shrink it (e.g. 0.01) so persistent-429 retry paths stay fast without
# changing the retry shape. Read at call time, so monkeypatching works.
_WAIT_SCALE = 1.0


def _retry_exc(retry_state: Any) -> BaseException | None:
    outcome = retry_state.outcome
    return outcome.exception() if outcome is not None else None


def provider_should_retry(retry_state: Any) -> bool:
    """tenacity ``retry`` predicate: retry 429s and transient errors only."""
    exc = _retry_exc(retry_state)
    if not isinstance(exc, ProviderError):
        return False
    if isinstance(exc, RateLimitError):
        return True
    return exc.status_code not in _NON_RETRYABLE_STATUS


def provider_retry_stop(retry_state: Any) -> bool:
    """tenacity ``stop`` predicate: more attempts for 429s than for 5xx."""
    limit = (
        _RATE_LIMIT_ATTEMPTS
        if isinstance(_retry_exc(retry_state), RateLimitError)
        else _TRANSIENT_ATTEMPTS
    )
    return retry_state.attempt_number >= limit


def provider_retry_wait(retry_state: Any) -> float:
    """tenacity ``wait`` callable.

    429 with retry-after → honour it (+ jitter). 429 without → exponential
    jitter, per-attempt cap 30s, cumulatively covering a full per-minute rate
    window. Everything else → short exponential jitter (1-4s), matching the
    old policy.
    """
    import random

    exc = _retry_exc(retry_state)
    attempt = retry_state.attempt_number
    if isinstance(exc, RateLimitError):
        if exc.retry_after is not None:
            wait = min(exc.retry_after + random.uniform(0, 1), 65.0)
        else:
            wait = min(2.0**attempt + random.uniform(0, 1), _RATE_LIMIT_MAX_WAIT)
        return wait * _WAIT_SCALE
    return (min(2.0 ** (attempt - 1), 4.0) * random.uniform(0.5, 1.0) + 0.5) * _WAIT_SCALE


def ensure_reasoning_supported(
    provider: str,
    model: str,
    reasoning: ReasoningMode,
    supported_modes: tuple[ReasoningMode, ...] = (),
    *,
    detail: str | None = None,
) -> ReasoningMode:
    """Return normalized reasoning mode or fail before issuing an API call."""
    mode = normalize_reasoning(reasoning)
    if mode == "auto" or mode in supported_modes:
        return mode

    supported = ", ".join(dict.fromkeys(("auto", *supported_modes)))
    message = (
        f"reasoning={mode!r} is not supported by provider {provider!r} "
        f"for model {model!r}. Supported reasoning modes: {supported}."
    )
    if detail:
        message = f"{message} {detail}"
    raise ProviderError(provider, message)


# ---------------------------------------------------------------------------
# Chat streaming types and protocol (opt-in for providers that support it)
# ---------------------------------------------------------------------------


@dataclass
class ChatToolCall:
    """A tool call requested by the LLM during a chat turn."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatStreamEvent:
    """A single event yielded by stream_chat().

    The ``type`` field determines which other fields are populated:
    - ``text_delta``: incremental text token(s) in ``text``
    - ``tool_start``: a completed tool call block in ``tool_call``
    - ``tool_result``: tool execution result (from internal loops) in ``tool_call`` + ``tool_result_data``
    - ``usage``: token counts in ``input_tokens`` / ``output_tokens``
    - ``stop``: end of generation (may follow tool_start if stop_reason is tool_use)
    """

    type: str  # text_delta | tool_start | tool_result | usage | stop
    text: str | None = None
    tool_call: ChatToolCall | None = None
    tool_result_data: dict[str, Any] | None = None  # populated for tool_result events
    stop_reason: str | None = None  # end_turn | tool_use | max_tokens
    input_tokens: int = 0
    output_tokens: int = 0


ToolExecutor = (
    Any  # Callable[[str, dict], Awaitable[dict]] — but kept as Any to avoid import cycles
)


@runtime_checkable
class ChatProvider(Protocol):
    """Optional protocol for providers that support streaming chat with tool use.

    Providers opt in by implementing stream_chat(). The existing BaseProvider
    and its generate() method remain completely untouched.

    Messages use OpenAI-format dicts. Each provider's stream_chat()
    converts to its native format internally.
    """

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        request_id: str | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Stream a multi-turn chat response with tool use support.

        Args:
            messages:       OpenAI-format message list (role + content + tool_calls).
            tools:          OpenAI-format tool definitions for function calling.
            system_prompt:  System instructions for the agent.
            max_tokens:     Max completion tokens.
            temperature:    Sampling temperature.
            request_id:     Optional trace ID.
            tool_executor:  Optional async callable(name, args) -> dict. If provided,
                            providers that need internal tool-call looping (e.g. Gemini
                            for thought_signature preservation) will execute tools
                            internally and yield tool_start/tool_result events. Providers
                            that don't need it (OpenAI, Anthropic) ignore this parameter
                            and let the caller handle the loop.

        Yields:
            ChatStreamEvent objects as tokens and tool calls arrive.
        """
        ...
