"""Embedder registry for repowise.

Mirrors the LLM provider registry pattern: lazy imports, built-in embedders,
and runtime registration for community embedders.

Built-in embedders:
    openai      -> OpenAIEmbedder      (text-embedding-3-small default)
    gemini      -> GeminiEmbedder      (gemini-embedding-001 default)
    ollama      -> OllamaEmbedder      (embeddinggemma default, local, no key)
    openrouter  -> OpenRouterEmbedder  (google/gemini-embedding-001 default)
    edenai      -> EdenAIEmbedder      (openai/text-embedding-3-small via Eden AI's EU gateway)
    fastembed   -> FastEmbedEmbedder   (BAAI/bge-small-en-v1.5, in-process ONNX, no key; extra local-embed)
    mock        -> KeylessEmbedder     (keyless runs and tests; no semantic signal)

Custom embedder registration:
    from repowise.core.providers.embedding import register_embedder

    register_embedder("voyage", lambda **kw: VoyageEmbedder(**kw))
    embedder = get_embedder("voyage", api_key="pa-...")
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from repowise.core.providers.embedding.base import Embedder

_BUILTIN_EMBEDDERS: dict[str, tuple[str, str]] = {
    "openai": ("repowise.core.providers.embedding.openai", "OpenAIEmbedder"),
    "gemini": ("repowise.core.providers.embedding.gemini", "GeminiEmbedder"),
    "ollama": ("repowise.core.providers.embedding.ollama", "OllamaEmbedder"),
    "openrouter": ("repowise.core.providers.embedding.openrouter", "OpenRouterEmbedder"),
    "edenai": ("repowise.core.providers.embedding.edenai", "EdenAIEmbedder"),
    "fastembed": ("repowise.core.providers.embedding.fastembed", "FastEmbedEmbedder"),
    "mock": ("repowise.core.providers.embedding.base", "KeylessEmbedder"),
}

#: What ``REPOWISE_EMBEDDER`` means when unset.
DEFAULT_EMBEDDER = "mock"

#: Built-ins whose vectors carry no signal (see ``KeylessEmbedder``).
KEYLESS_EMBEDDERS = frozenset(
    name for name, (_module, cls) in _BUILTIN_EMBEDDERS.items() if cls == "KeylessEmbedder"
)

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
        raise ValueError(f"Cannot register {name!r}: conflicts with a built-in embedder.")
    _custom_embedders[name] = factory
    from repowise.core.registry.plugins import note_registration

    note_registration("embedder", name)


def get_embedder(name: str, **kwargs: Any) -> Embedder:
    """Instantiate an embedder by name.

    Args:
        name:     Embedder identifier ('openai', 'gemini', 'fastembed', 'mock').
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
    if name not in _custom_embedders and name not in _BUILTIN_EMBEDDERS:
        # Deferred: a built-in name never pays plugin discovery.
        from repowise.core.registry.plugins import load_plugins

        load_plugins()  # a plugin package may register it
    if name in _custom_embedders:
        return _custom_embedders[name](**kwargs)

    if name not in _BUILTIN_EMBEDDERS:
        available = sorted(set(_BUILTIN_EMBEDDERS) | set(_custom_embedders))
        raise ValueError(f"Unknown embedder: {name!r}. Available embedders: {available}")

    module = import_builtin_embedder(name)
    cls = getattr(module, _BUILTIN_EMBEDDERS[name][1])
    return cls(**kwargs)


#: The package a built-in embedder's module needs, when it is not the name.
_MISSING_PACKAGE = {
    "openai": "openai",
    "gemini": "google-genai",
    "ollama": "httpx",
    "openrouter": "openai",  # openrouter uses the openai package
    "edenai": "openai",  # edenai uses the openai package
    "fastembed": "repowise[local-embed]",
}


def import_builtin_embedder(name: str) -> Any:
    """Import built-in embedder *name*'s module, or raise ImportError naming the install.

    Shared by :func:`get_embedder` and ``repowise doctor``, so both print the
    same install line.
    """
    try:
        return importlib.import_module(_BUILTIN_EMBEDDERS[name][0])
    except ImportError as exc:
        package = _MISSING_PACKAGE.get(name, name)
        # An extra needs quotes, or zsh reads the brackets as a glob.
        spec = f'"{package}"' if "[" in package else package
        raise ImportError(
            f"Embedder {name!r} requires the '{package}' package. "
            f"Install it with: pip install {spec}"
        ) from exc


def list_embedders() -> list[str]:
    """Return a sorted list of all available embedder names, plugins' included."""
    from repowise.core.registry.plugins import load_plugins

    load_plugins()
    return sorted(set(_BUILTIN_EMBEDDERS) | set(_custom_embedders))
