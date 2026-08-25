"""Provider registry for repowise.

Provides a single entry point for instantiating any LLM provider by name.
Supports built-in providers and runtime registration of custom providers,
enabling community-contributed providers without forking repowise.

Built-in providers:
    - anthropic   → AnthropicProvider
    - openai      → OpenAIProvider
    - openrouter  → OpenRouterProvider
    - deepseek    → DeepSeekProvider
    - kimi        → KimiProvider
    - edenai      → EdenAIProvider
    - ollama      → OllamaProvider
    - litellm     → LiteLLMProvider
    - codex_cli   → CodexCliProvider
    - claude_cli  → ClaudeCliProvider
    - opencode    → OpenCodeProvider
    - mock        → MockProvider (testing only)

Custom provider registration:
    from repowise.core.providers import register_provider
    from my_package import MyProvider

    register_provider("my_provider", lambda **kw: MyProvider(**kw))

    # Then use it like any built-in:
    provider = get_provider("my_provider", model="my-model")
"""

from __future__ import annotations

import importlib
import os
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
    "openrouter": ("repowise.core.providers.llm.openrouter", "OpenRouterProvider"),
    "gemini": ("repowise.core.providers.llm.gemini", "GeminiProvider"),
    "ollama": ("repowise.core.providers.llm.ollama", "OllamaProvider"),
    "litellm": ("repowise.core.providers.llm.litellm", "LiteLLMProvider"),
    "deepseek": ("repowise.core.providers.llm.deepseek", "DeepSeekProvider"),
    "kimi": ("repowise.core.providers.llm.kimi", "KimiProvider"),
    "edenai": ("repowise.core.providers.llm.edenai", "EdenAIProvider"),
    "codex_cli": ("repowise.core.providers.llm.codex_cli", "CodexCliProvider"),
    "claude_cli": ("repowise.core.providers.llm.claude_cli", "ClaudeCliProvider"),
    "opencode": ("repowise.core.providers.llm.opencode", "OpenCodeProvider"),
    "mock": ("repowise.core.providers.llm.mock", "MockProvider"),
}

# Which env var carries a provider's API key. One entry per built-in provider
# that authenticates with a key; keyless providers are absent by construction.
# The three resolution paths (the CLI's resolve_provider, its
# validate_provider_config, and the MCP server's get_answer) read this instead
# of each keeping the copy they used to, which is how the MCP one drifted into
# #1119. Other modules still carry their own provider->env lists for display
# and env forwarding; the ones that must agree with resolution are held to this
# table by drift tests rather than by anybody remembering.
PROVIDER_API_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),  # either one
    "deepseek": ("DEEPSEEK_API_KEY",),
    "kimi": ("KIMI_API_KEY",),
    "edenai": ("EDENAI_API_KEY",),
    "litellm": ("LITELLM_API_KEY",),
}

# Which env var points a provider at a non-default endpoint. Proxies and
# self-hosted deployments of the OpenAI-compatible providers live here too.
PROVIDER_BASE_URL_ENVS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_BASE_URL",),
    "openai": ("OPENAI_BASE_URL",),
    "gemini": ("GEMINI_BASE_URL",),
    "deepseek": ("DEEPSEEK_BASE_URL",),
    "kimi": ("KIMI_BASE_URL",),
    "edenai": ("EDENAI_BASE_URL",),
    "ollama": ("OLLAMA_BASE_URL",),
    "litellm": ("LITELLM_BASE_URL", "LITELLM_API_BASE"),
}

# Providers that can run with no API key at all: they authenticate out of band
# (an already-logged-in coding-agent CLI) or need no auth (a local model, a
# proxy the user secured elsewhere). Resolution must never reject one of these
# for a "missing" key, and must never fall through to a different provider
# because it could not find one.
KEYLESS_PROVIDERS = frozenset({"codex_cli", "claude_cli", "opencode", "ollama", "litellm", "mock"})

# Providers that shell out to a CLI and therefore need to be told which repo
# they are reasoning about: they pass it as the subprocess working directory.
# Omitting it silently runs the CLI against whatever cwd the host process had,
# which for an MCP server launched by an editor is not the user's repo.
REPO_PATH_PROVIDERS = frozenset({"codex_cli", "opencode"})

# Order to try providers in when nothing was configured and we are guessing
# from whatever credentials the environment happens to carry. This is the CLI's
# long-standing order, adopted verbatim as the shared one: the MCP server used
# to auto-detect in a slightly different order (and skipped openrouter
# entirely), so the same repo could resolve to different models depending on
# which entry point asked.
PROVIDER_AUTODETECT_ORDER: tuple[str, ...] = (
    "anthropic",
    "openai",
    "openrouter",
    "ollama",
    "gemini",
    "deepseek",
    "kimi",
    # A provider adding itself appends: autodetect order is de facto precedence,
    # and an unrelated EDENAI_API_KEY in the environment must not silently take
    # over from a provider the user was already resolving to.
    "edenai",
)

# An env var set to "" or whitespace means "not set". CI systems and agent
# harnesses declare empty vars routinely (REPOWISE_PROVIDER: "" in a workflow
# matrix), and treating one as a real value resolves a provider that cannot
# possibly authenticate.
EnvLookup = Callable[[str], "str | None"]


def _clean(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def _first_env(names: tuple[str, ...], getenv: EnvLookup) -> str | None:
    """First non-empty value among ``names``, in order."""
    for name in names:
        value = _clean(getenv(name))
        if value:
            return value
    return None


def provider_required_envs(name: str) -> tuple[str, ...]:
    """Env vars without which ``name`` cannot reach a model at all.

    An API key for the remote-API providers; the endpoint for ollama, which
    needs no key but does need somewhere to send the request. Empty for
    providers that are self-sufficient (the agent CLIs, mock).
    """
    if name in PROVIDER_API_KEY_ENVS:
        return PROVIDER_API_KEY_ENVS[name]
    if name == "ollama":
        return PROVIDER_BASE_URL_ENVS["ollama"]
    return ()


def provider_credentials_present(name: str, getenv: EnvLookup = os.environ.get) -> bool:
    """Whether the environment names credentials for ``name`` specifically.

    The question auto-detection asks: did the user put something in the
    environment that points at *this* provider. False for a provider that
    needs no env var at all, which is the correct answer here. "Runs without
    configuration" is not a reason to pick it over what the user configured.
    """
    required = provider_required_envs(name)
    return bool(required) and _first_env(required, getenv) is not None


def provider_is_usable(name: str, getenv: EnvLookup = os.environ.get) -> bool:
    """Whether ``name`` has everything it needs to reach a model.

    The question explicit selection asks. True for the keyless providers
    unconditionally, because they authenticate out of band: there is no env
    var to check and so no grounds to reject them. Unknown
    (runtime-registered) providers are also True, since we know nothing about
    their requirements and must not veto them.
    """
    if name in KEYLESS_PROVIDERS:
        return True
    return not provider_required_envs(name) or provider_credentials_present(name, getenv)


def provider_kwargs(
    name: str,
    *,
    model: str | None = None,
    repo_path: Any = None,
    getenv: EnvLookup = os.environ.get,
) -> dict[str, Any]:
    """Constructor kwargs for ``name``, assembled from the environment.

    The single place that knows how a provider name maps onto ``api_key`` /
    ``base_url`` / ``repo_path`` constructor arguments. ``getenv`` is injected
    so a caller with its own precedence (the MCP server overlays
    ``.repowise/.env`` under the process env) reuses this mapping instead of
    growing a parallel copy. The copies are what drifted into #1119.
    """
    kwargs: dict[str, Any] = {}
    if model:
        kwargs["model"] = model
    api_key = _first_env(PROVIDER_API_KEY_ENVS.get(name, ()), getenv)
    if api_key:
        kwargs["api_key"] = api_key
    base_url = _first_env(PROVIDER_BASE_URL_ENVS.get(name, ()), getenv)
    if base_url:
        kwargs["base_url"] = base_url
    if repo_path is not None and name in REPO_PATH_PROVIDERS:
        kwargs["repo_path"] = repo_path
    return kwargs


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
        raise ValueError(f"Unknown provider: {name!r}. Available providers: {available}")

    # Attach rate limiter (skip for mock — tests should run without limits)
    if with_rate_limiter and name not in ("mock", "codex_cli", "opencode"):
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
            "openrouter": "openai",  # openrouter uses the openai package
            "deepseek": "openai",  # deepseek uses the openai package
            "kimi": "openai",  # kimi uses the openai package
            "edenai": "openai",  # edenai uses the openai package
            "litellm": "litellm",
            "codex_cli": "@openai/codex",
            "claude_cli": "@anthropic-ai/claude-code",
            "opencode": "opencode",
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
