"""repowise LLM provider sub-package.

All LLM providers implement BaseProvider. Use get_provider() from the registry
to instantiate a provider by name — this is the preferred entry point.

    from repowise.core.providers.llm import get_provider

    provider = get_provider("anthropic", api_key="sk-...", model="claude-sonnet-4-6")
    response = await provider.generate(system_prompt="...", user_prompt="...")

Built-in providers:
    anthropic  — claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5
    openai     — gpt-5.4-nano, gpt-5.4-mini, gpt-5.4
    gemini     — gemini-3.1-flash-lite-preview, gemini-3-flash-preview, gemini-3.1-pro-preview
    openrouter — 200+ models via OpenRouter (anthropic/claude-sonnet-4.6, etc.)
    deepseek   — deepseek-v4-flash, deepseek-v4-pro via api.deepseek.com
    ollama     — local inference (llama3.2, codellama, etc.)
    litellm    — 100+ providers via LiteLLM proxy
    codex_cli  — local authenticated Codex CLI via codex exec
    opencode   — local opencode CLI via opencode run
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
