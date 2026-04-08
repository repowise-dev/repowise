"""repowise LLM provider sub-package.

All LLM providers implement BaseProvider. Use get_provider() from the registry
to instantiate a provider by name — this is the preferred entry point.

    from repowise.core.providers.llm import get_provider

    provider = get_provider("anthropic", api_key="sk-...", model="claude-sonnet-4-6")
    response = await provider.generate(system_prompt="...", user_prompt="...")

Built-in providers:
    anthropic  — claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5
    minimax    — MiniMax-M2.7, MiniMax-M2.7-highspeed (Anthropic-compatible API)
    openai     — gpt-5.4-nano, gpt-5.4-mini, gpt-5.4
    gemini     — gemini-3.1-flash-lite-preview, gemini-3-flash-preview, gemini-3.1-pro-preview
    ollama     — local inference (llama3.2, codellama, etc.)
    litellm    — 100+ providers via LiteLLM proxy
    mock       — deterministic test provider
"""

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)
from repowise.core.providers.llm.registry import get_provider, list_providers, register_provider

__all__ = [
    "BaseProvider",
    "ChatProvider",
    "ChatStreamEvent",
    "ChatToolCall",
    "GeneratedResponse",
    "ProviderError",
    "RateLimitError",
    "get_provider",
    "list_providers",
    "register_provider",
]
