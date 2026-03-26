"""repowise provider package.

All LLM providers implement BaseProvider. Use get_provider() from the registry
to instantiate a provider by name — this is the preferred entry point.

    from repowise.core.providers import get_provider

    provider = get_provider("anthropic", api_key="sk-...", model="claude-sonnet-4-6")
    response = await provider.generate(system_prompt="...", user_prompt="...")
"""

from repowise.core.providers.base import (
    BaseProvider,
    ChatProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)
from repowise.core.providers.registry import get_provider, list_providers, register_provider

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
