"""repowise LLM provider sub-package.

All LLM providers implement BaseProvider. Use get_provider() from the registry
to instantiate a provider by name — this is the preferred entry point.

    from repowise.core.providers.llm import get_provider

    provider = get_provider("anthropic", api_key="sk-...", model="claude-sonnet-4-6")
    response = await provider.generate(system_prompt="...", user_prompt="...")

Built-in providers:
    anthropic  — claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5
    openai     — gpt-5.6-luna, gpt-6-luna, gpt-6-sol
    gemini     — gemini-3.5-flash-lite, gemini-4-argon, gemini-3.8-flash
    openrouter — 200+ models via OpenRouter (anthropic/claude-sonnet-4.6, etc.)
    deepseek   — deepseek-flash, deepseek-v4-pro via api.deepseek.com
    kimi       — kimi-for-coding, kimi-for-coding-highspeed, and K2.x models
    edenai     — many vendors (mistral/gpt/claude/gemini) via Eden AI's EU gateway
    ollama     — local inference (llama3.2, codellama, etc.)
    litellm    — 100+ providers via LiteLLM proxy
    codex_cli  — local authenticated Codex CLI via codex exec
    claude_cli — local authenticated Claude Code CLI via claude -p
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
