"""Embedder selection + construction shared across CLI commands."""

from __future__ import annotations

import os
from typing import Any


def _embedder_kwargs(embedder_name: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    model = os.environ.get("REPOWISE_EMBEDDING_MODEL")
    if embedder_name == "ollama":
        model = os.environ.get("OLLAMA_EMBEDDING_MODEL") or model
        base_url = os.environ.get("OLLAMA_BASE_URL")
        dimensions = os.environ.get("OLLAMA_EMBEDDING_DIMS") or os.environ.get(
            "REPOWISE_EMBEDDING_DIMS"
        )
        timeout = os.environ.get("OLLAMA_EMBEDDING_TIMEOUT") or os.environ.get(
            "REPOWISE_EMBEDDING_TIMEOUT"
        )
        if base_url:
            kwargs["base_url"] = base_url
        if dimensions:
            kwargs["dimensions"] = int(dimensions)
        if timeout:
            kwargs["timeout"] = float(timeout)
    elif embedder_name == "gemini":
        dimensions = os.environ.get("REPOWISE_EMBEDDING_DIMS")
        if dimensions:
            kwargs["output_dimensionality"] = int(dimensions)
    if model:
        kwargs["model"] = model
    return kwargs


def resolve_embedding_model(embedder_name: str) -> str | None:
    """Return the configured embedding model for *embedder_name*, if any.

    Mirrors the precedence in :func:`_embedder_kwargs` so the value persisted
    to ``config.yaml`` at init time is exactly what the embedder would build
    with. Returns ``None`` when no model is configured (the embedder then uses
    its own default), which keeps ``config.yaml`` free of empty keys.
    """
    if embedder_name == "ollama":
        return os.environ.get("OLLAMA_EMBEDDING_MODEL") or os.environ.get(
            "REPOWISE_EMBEDDING_MODEL"
        )
    return os.environ.get("REPOWISE_EMBEDDING_MODEL")


def resolve_embedder(embedder_flag: str | None) -> str:
    """Auto-detect embedder from env vars, or use the flag value."""
    if embedder_flag:
        return embedder_flag
    configured = os.environ.get("REPOWISE_EMBEDDER", "").strip().lower()
    if configured:
        return configured
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("EDENAI_API_KEY"):
        return "edenai"
    if os.environ.get("OLLAMA_EMBEDDING_MODEL"):
        return "ollama"
    return "mock"


def build_embedder(embedder_name_resolved: str) -> Any:
    """Construct the configured embedder, falling back to MockEmbedder.

    Shared by the generation flows and the decision semantic-dedup wiring so
    the same backend selection logic isn't duplicated. Real providers fall
    back to the deterministic mock when their SDK/credentials are unavailable.
    """
    from repowise.core.providers.embedding.base import MockEmbedder
    from repowise.core.providers.embedding.registry import get_embedder

    if embedder_name_resolved == "mock":
        return MockEmbedder()
    try:
        return get_embedder(embedder_name_resolved, **_embedder_kwargs(embedder_name_resolved))
    except Exception:
        return MockEmbedder()
