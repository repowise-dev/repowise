"""Provider registry for repowise.

Provides a single entry point for instantiating any LLM provider by name.
Supports built-in providers and runtime registration of custom providers,
enabling community-contributed providers without forking repowise.

Built-in providers:
    - anthropic  → AnthropicProvider
    - openai     → OpenAIProvider
    - gemini     → GeminiProvider
    - ollama     → OllamaProvider
    - litellm    → LiteLLMProvider
    - zai        → ZAIProvider
    - minimax    → MiniMaxProvider
    - mock       → MockProvider (testing only)

Custom provider registration:
    from repowise.core.providers import register_provider
    from my_package import MyProvider

    register_provider("my_provider", lambda **kw: MyProvider(**kw))

    # Then use it like any built-in:
    provider = get_provider("my_provider", model="my-model")
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from repowise.core.providers.llm.base import BaseProvider
from repowise.core.rate_limiter import PROVIDER_DEFAULTS, RateLimitConfig, RateLimiter

# Map of provider name → (module_path, class_name)
# Providers are imported lazily to avoid requiring all dependencies at import time.
# This means `pip install repowise-core` without anthropic installed still works —
# you just can't use the anthropic provider.
_BUILTIN_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("repowise.core.providers.llm.anthropic", "AnthropicProvider"),
    "openai": ("repowise.core.providers.llm.openai", "OpenAIProvider"),
    "gemini": ("repowise.core.providers.llm.gemini", "GeminiProvider"),
    "ollama": ("repowise.core.providers.llm.ollama", "OllamaProvider"),
    "litellm": ("repowise.core.providers.llm.litellm", "LiteLLMProvider"),
    "zai": ("repowise.core.providers.llm.zai", "ZAIProvider"),
    "minimax": ("repowise.core.providers.llm.minimax", "MiniMaxProvider"),
    "mock": ("repowise.core.providers.llm.mock", "MockProvider"),
}

# Runtime-registered custom providers (factory callables)
_custom_providers: dict[str, Callable[..., BaseProvider]] = {}


def register_provider(name: str, factory: Callable[..., BaseProvider]) -> None:
    """Register a custom provider factory under a given name.

    This is the extension point for community providers. The factory receives
    all keyword arguments passed to get_provider() and must return a BaseProvider.

    Args:
        name:    Short identifier for the provider (e.g., 'my_provider').
                 Must not conflict with built-in names.
        factory: Callable that accepts **kwargs and returns a BaseProvider instance.

    Raises:
        ValueError: If `name` conflicts with a built-in provider name.

    Example:
        register_provider("bedrock", lambda model, **kw: BedrockProvider(model=model))
        provider = get_provider("bedrock", model="claude-sonnet-4-6")
    """
    if name in _BUILTIN_PROVIDERS:
        raise ValueError(
            f"Cannot register {name!r}: conflicts with a built-in provider. "
            "Choose a different name."
        )
    _custom_providers[name] = factory


def get_provider(
    name: str,
    with_rate_limiter: bool = True,
    rate_limit_config: RateLimitConfig | None = None,
    **kwargs: Any,
) -> BaseProvider:
    """Instantiate a provider by name.

    Providers are imported lazily — only the requested provider's dependencies
    need to be installed.

    Args:
        name:              Provider identifier ('anthropic', 'openai', etc.).
        with_rate_limiter: Attach a RateLimiter to the provider. Default True.
                           Set False for mock/test providers or when managing
                           concurrency externally via asyncio.Semaphore.
        rate_limit_config: Custom rate limit config. If None, uses the provider's
                           default from PROVIDER_DEFAULTS.
        **kwargs:          Constructor arguments for the provider
                           (e.g., api_key, model, base_url).

    Returns:
        A configured BaseProvider instance, ready for use.

    Raises:
        ValueError: If the provider name is not registered.
        ImportError: If the provider's optional dependency is not installed.

    Example:
        provider = get_provider(
            "anthropic",
            api_key="sk-ant-...",
            model="claude-opus-4-6",
        )
        response = await provider.generate(system_prompt="...", user_prompt="...")
    """
    if name in _custom_providers:
        return _custom_providers[name](**kwargs)

    if name not in _BUILTIN_PROVIDERS:
        available = sorted(set(_BUILTIN_PROVIDERS) | set(_custom_providers))
        raise ValueError(
            f"Unknown provider: {name!r}. "
            f"Available providers: {available}"
        )

    # Attach rate limiter (skip for mock — tests should run without limits)
    if with_rate_limiter and name != "mock":
        config = rate_limit_config or PROVIDER_DEFAULTS.get(name)
        if config and "rate_limiter" not in kwargs:
            kwargs["rate_limiter"] = RateLimiter(config)

    module_path, class_name = _BUILTIN_PROVIDERS[name]
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        # Give a helpful error message naming the missing package
        _missing = {
            "anthropic": "anthropic",
            "openai": "openai",
            "gemini": "google-genai",
            "ollama": "openai",  # ollama uses the openai package
            "litellm": "litellm",
            "zai": "openai",  # zai uses the openai package (OpenAI-compatible API)
            "minimax": "openai",  # minimax uses the openai package (OpenAI-compatible API)
        }
        package = _missing.get(name, name)
        raise ImportError(
            f"Provider {name!r} requires the '{package}' package. "
            f"Install it with: pip install {package}"
        ) from exc

    cls: type[BaseProvider] = getattr(module, class_name)
    return cls(**kwargs)


def list_providers() -> list[str]:
    """Return a sorted list of all available provider names.

    Includes both built-in and runtime-registered custom providers.
    """
    return sorted(set(_BUILTIN_PROVIDERS) | set(_custom_providers))
