"""Embedder registry for repowise.

Mirrors the LLM provider registry pattern: lazy imports, built-in embedders,
and runtime registration for community embedders.

Built-in embedders:
    openai  → OpenAIEmbedder  (text-embedding-3-small default)
    gemini  → GeminiEmbedder  (gemini-embedding-001 default)
    mock    → MockEmbedder    (testing only, zero dependencies)

Custom embedder registration:
    from repowise.core.providers.embedding import register_embedder

    register_embedder("voyage", lambda **kw: VoyageEmbedder(**kw))
    embedder = get_embedder("voyage", api_key="pa-...")
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from repowise.core.providers.embedding.base import Embedder

_BUILTIN_EMBEDDERS: dict[str, tuple[str, str]] = {
    "openai": ("repowise.core.providers.embedding.openai", "OpenAIEmbedder"),
    "gemini":     ("repowise.core.providers.embedding.gemini",     "GeminiEmbedder"),
    "openrouter": ("repowise.core.providers.embedding.openrouter", "OpenRouterEmbedder"),
    "mock":       ("repowise.core.providers.embedding.base",       "MockEmbedder"),
}

_custom_embedders: dict[str, Callable[..., Embedder]] = {}


def register_embedder(name: str, factory: Callable[..., Embedder]) -> None:
    """Register a custom embedder factory.

    Args:
        name:    Short identifier (e.g., 'voyage', 'cohere').
                 Must not conflict with built-in names.
        factory: Callable that accepts **kwargs and returns an Embedder.

    Raises:
        ValueError: If name conflicts with a built-in embedder.
    """
    if name in _BUILTIN_EMBEDDERS:
        raise ValueError(
            f"Cannot register {name!r}: conflicts with a built-in embedder."
        )
    _custom_embedders[name] = factory


def get_embedder(name: str, **kwargs: Any) -> Embedder:
    """Instantiate an embedder by name.

    Args:
        name:     Embedder identifier ('openai', 'gemini', 'mock').
        **kwargs: Constructor arguments (e.g., api_key, model).

    Returns:
        A configured Embedder instance.

    Raises:
        ValueError: If the embedder name is not registered.
        ImportError: If the embedder's optional dependency is not installed.

    Example:
        embedder = get_embedder("openai", api_key="sk-...", model="text-embedding-3-large")
        vectors = await embedder.embed(["Hello world"])
    """
    if name in _custom_embedders:
        return _custom_embedders[name](**kwargs)

    if name not in _BUILTIN_EMBEDDERS:
        available = sorted(set(_BUILTIN_EMBEDDERS) | set(_custom_embedders))
        raise ValueError(
            f"Unknown embedder: {name!r}. Available embedders: {available}"
        )

    module_path, class_name = _BUILTIN_EMBEDDERS[name]
    _missing = {
        "openai": "openai",
        "gemini": "google-genai",
        "openrouter": "openai",  # openrouter uses the openai package
    }
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        package = _missing.get(name, name)
        raise ImportError(
            f"Embedder {name!r} requires the '{package}' package. "
            f"Install it with: pip install {package}"
        ) from exc

    cls = getattr(module, class_name)
    return cls(**kwargs)


def list_embedders() -> list[str]:
    """Return a sorted list of all available embedder names."""
    return sorted(set(_BUILTIN_EMBEDDERS) | set(_custom_embedders))
