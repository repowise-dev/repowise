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
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from repowise.core.providers.embedding.base import Embedder

_log = logging.getLogger(__name__)

_BUILTIN_EMBEDDERS: dict[str, tuple[str, str]] = {
    "openai": ("repowise.core.providers.embedding.openai", "OpenAIEmbedder"),
    "gemini": ("repowise.core.providers.embedding.gemini", "GeminiEmbedder"),
    "ollama": ("repowise.core.providers.embedding.ollama", "OllamaEmbedder"),
    "openrouter": ("repowise.core.providers.embedding.openrouter", "OpenRouterEmbedder"),
    "mock": ("repowise.core.providers.embedding.base", "MockEmbedder"),
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
        raise ValueError(f"Cannot register {name!r}: conflicts with a built-in embedder.")
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
        raise ValueError(f"Unknown embedder: {name!r}. Available embedders: {available}")

    module_path, class_name = _BUILTIN_EMBEDDERS[name]
    _missing = {
        "openai": "openai",
        "gemini": "google-genai",
        "ollama": "httpx",
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


def resolve_embedding_model(embedder_name: str, repo_path: Path | None = None) -> str | None:
    """The embedding model *embedder_name* should build with, or None for its default.

    Process env wins, so a shell override still beats persisted intent —
    ``OLLAMA_EMBEDDING_MODEL`` ahead of the generic ``REPOWISE_EMBEDDING_MODEL``
    for the ollama backend, matching that constructor's own fallback chain.
    Below env sits the ``embedding_model`` pinned in ``.repowise/config.yaml``.
    The pin used to be write-only — recorded at init as the model the store was
    embedded with, read by nothing — so editing it changed nothing, and a shell
    without the env vars silently fell to the embedder's default, which can be
    a different vector width than the table it queries.

    Without *repo_path* the resolution is env-only: callers that measure what
    the environment alone would resolve (embedder-drift detection) depend on
    the pin not answering for itself.

    Shared by the CLI build path and the MCP server so the same repo cannot
    resolve two different ways depending on which surface asks.
    """
    import os

    model: str | None = None
    if embedder_name == "ollama":
        model = os.environ.get("OLLAMA_EMBEDDING_MODEL") or None
    model = model or os.environ.get("REPOWISE_EMBEDDING_MODEL") or None
    if model:
        return model
    if repo_path is None:
        return None

    config_path = Path(repo_path) / ".repowise" / "config.yaml"
    try:
        if config_path.is_file():
            import yaml

            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                pinned = data.get("embedding_model")
                if pinned:
                    return str(pinned)
    except Exception:
        _log.debug("Failed to read embedding_model from %s", config_path, exc_info=True)
    return None
