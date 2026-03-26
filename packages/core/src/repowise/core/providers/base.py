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
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


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
                       Currently only Anthropic reports this natively.
        usage:         Provider-specific usage dict (stored as-is for auditing).
    """

    content: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    usage: dict[str, Any] = field(default_factory=dict)

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
    """Raised when rate limits are permanently exhausted after retries.

    This is a sub-class of ProviderError. Callers can catch either,
    but RateLimitError signals that backing off longer won't help —
    the operator needs to review rate limits or reduce concurrency.
    """


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


ToolExecutor = Any  # Callable[[str, dict], Awaitable[dict]] — but kept as Any to avoid import cycles


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
