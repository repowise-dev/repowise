"""repowise provider package.

Sub-packages:
    llm/       — LLM providers (Anthropic, OpenAI, OpenRouter, Gemini, Ollama, LiteLLM)
    embedding/ — Embedding providers (OpenAI, Gemini, Mock)

Preferred entry points:

    from repowise.core.providers.llm import get_provider
    from repowise.core.providers.embedding import get_embedder

    provider = get_provider("openai", api_key="sk-...", model="gpt-5.4-nano")
    response = await provider.generate(system_prompt="...", user_prompt="...")

    embedder = get_embedder("openai", api_key="sk-...")
    vectors = await embedder.embed(["text to embed"])

Backward-compatible imports still work:
    from repowise.core.providers import get_provider  # → llm.registry
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
from repowise.core.providers.embedding import get_embedder, list_embedders, register_embedder

__all__ = [
    # LLM
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
    # Embedding
    "get_embedder",
    "list_embedders",
    "register_embedder",
]
