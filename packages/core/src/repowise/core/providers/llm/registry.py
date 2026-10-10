"""Provider registry for repowise.

Provides a single entry point for instantiating any LLM provider by name.
Supports built-in providers and runtime registration of custom providers,
enabling community-contributed providers without forking repowise.

Built-in providers are declared once, in :mod:`repowise.core.providers.llm.specs`.

Custom provider registration:
    from repowise.core.providers import register_provider
    from my_package import MyProvider

    register_provider("my_provider", lambda **kw: MyProvider(**kw))

    # Then use it like any built-in:
    provider = get_provider("my_provider", model="my-model")
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from repowise.core.providers.llm.base import BaseProvider
from repowise.core.providers.llm.specs import PROVIDER_SPECS
from repowise.core.rate_limiter import RateLimitConfig, RateLimiter

logger = logging.getLogger(__name__)

# Every table below is derived from the provider specs (see specs.py), so a
# provider is declared once. The names stay importable because the CLI, the
# MCP server and drift tests read them.
_BUILTIN_PROVIDERS: dict[str, tuple[str, str]] = {
    name: (spec.module_path, spec.class_name) for name, spec in PROVIDER_SPECS.items()
}

# Which env var carries a provider's API key; keyless providers are absent.
# Resolution (the CLI's resolve_provider and validate_provider_config, the MCP
# server's get_answer) reads this rather than a private copy; the copies are
# what drifted into #1119.
PROVIDER_API_KEY_ENVS: dict[str, tuple[str, ...]] = {
    name: spec.api_key_envs for name, spec in PROVIDER_SPECS.items() if spec.api_key_envs
}

# Which env var points a provider at a non-default endpoint.
PROVIDER_BASE_URL_ENVS: dict[str, tuple[str, ...]] = {
    name: spec.base_url_envs for name, spec in PROVIDER_SPECS.items() if spec.base_url_envs
}

# Providers that can run with no API key. Resolution must never reject one for
# a "missing" key, nor fall through to a different provider for want of one.
KEYLESS_PROVIDERS = frozenset(name for name, spec in PROVIDER_SPECS.items() if spec.keyless)

# Providers that shell out with the repo as their working directory. Omitting
# the path runs the CLI in whatever cwd the host process had, which for an MCP
# server launched by an editor is not the user's repo.
REPO_PATH_PROVIDERS = frozenset(
    name for name, spec in PROVIDER_SPECS.items() if spec.needs_repo_cwd
)

# Order to try providers in when nothing was configured. Shared by the CLI and
# the MCP server so one repo resolves to the same model from either entry point.
PROVIDER_AUTODETECT_ORDER: tuple[str, ...] = tuple(
    spec.name
    for spec in sorted(
        (s for s in PROVIDER_SPECS.values() if s.autodetect_rank is not None),
        key=lambda s: s.autodetect_rank,
    )
)

# Default rate limit per provider. Absent means no limiter is attached.
PROVIDER_DEFAULTS: dict[str, RateLimitConfig] = {
    name: spec.rate_limit for name, spec in PROVIDER_SPECS.items() if spec.rate_limit
}

# An env var set to "" or whitespace means "not set". CI systems and agent
# harnesses declare empty vars routinely (REPOWISE_PROVIDER: "" in a workflow
# matrix), and treating one as a real value resolves a provider that cannot
# possibly authenticate.
EnvLookup = Callable[[str], "str | None"]


def repo_env_lookup(repo_path: Any = None) -> EnvLookup:
    """An :data:`EnvLookup` that also sees *repo_path*'s ``.repowise/.env``.

    The process environment still wins. Reading the file instead of merging it
    into ``os.environ`` is what keeps one repo's keys out of another's
    resolution in a workspace server, which is why ``load_repo_env`` exists.

    Without this, a reporting caller sees only ``os.environ`` and answers
    "no provider" for a repo whose key ``repowise init`` wrote to
    ``.repowise/.env`` — the file it writes to by default.
    """
    if repo_path is None:
        return os.environ.get
    try:
        from repowise.core.repo_config import RepoConfigError, load_repo_env

        overlay = load_repo_env(repo_path)
    except RepoConfigError:
        # A broken .env must surface rather than read as "no key" (#852), the
        # same way the CLI and server readers report it.
        logger.warning("Repo .env unreadable for %s; using the environment only", repo_path)
        return os.environ.get
    except Exception:
        return os.environ.get
    if not overlay:
        return os.environ.get
    return lambda key: os.environ.get(key) or overlay.get(key) or None


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
    spec = PROVIDER_SPECS.get(name)
    return spec.required_envs if spec else ()


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

    if name not in PROVIDER_SPECS:
        available = sorted(set(_BUILTIN_PROVIDERS) | set(_custom_providers))
        raise ValueError(f"Unknown provider: {name!r}. Available providers: {available}")

    spec = PROVIDER_SPECS[name]
    # A spec without a rate limit (mock, the agent CLIs) never gets one.
    if with_rate_limiter and spec.rate_limit is not None and "rate_limiter" not in kwargs:
        kwargs["rate_limiter"] = RateLimiter(rate_limit_config or spec.rate_limit)

    try:
        module = importlib.import_module(spec.module_path)
    except ImportError as exc:
        if not spec.package:
            raise
        package = spec.package
        raise ImportError(
            f"Provider {name!r} requires the '{package}' package. "
            f"Install it with: pip install {package}"
        ) from exc

    cls: type[BaseProvider] = getattr(module, spec.class_name)
    return cls(**kwargs)


def list_providers() -> list[str]:
    """Return a sorted list of all available provider names.

    Includes both built-in and runtime-registered custom providers.
    """
    return sorted(set(_BUILTIN_PROVIDERS) | set(_custom_providers))


def provider_available_for_repo(repo_path: Path | str) -> bool:
    """Whether a provider would resolve for *repo_path*, constructing nothing.

    Mirrors the CLI's resolution order: an explicit choice is checked for
    usability, auto-detection asks only whether credentials name a provider.
    Reporting-only, so it answers False rather than raising on a broken config.

    Resolution reads the repo's own ``.repowise/.env`` as well as the process
    environment. Asking ``os.environ`` alone made this disagree with the
    pipeline, which gates the capture lanes on a real client: a repo with its
    key in ``.repowise/.env`` was told its ``pr``, ``comment`` and
    ``git_archaeology`` lanes had no provider while those lanes were running.
    """
    try:
        from repowise.core.repo_config import load_repo_config

        getenv = repo_env_lookup(repo_path)
        configured = (getenv("REPOWISE_PROVIDER") or "").strip()
        if not configured:
            configured = str(load_repo_config(repo_path).get("provider") or "").strip()
        if configured:
            return provider_is_usable(configured, getenv)
        return any(
            provider_credentials_present(name, getenv) for name in PROVIDER_AUTODETECT_ORDER
        )
    except Exception:
        return False
