"""Embedder selection + construction shared across CLI commands."""

from __future__ import annotations

import os
from typing import Any


def resolve_embedder(embedder_flag: str | None) -> str:
    """Auto-detect embedder from env vars, or use the flag value."""
    if embedder_flag:
        return embedder_flag
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "mock"


def build_embedder(embedder_name_resolved: str) -> Any:
    """Construct the configured embedder, falling back to MockEmbedder.

    Shared by the generation flows and the decision semantic-dedup wiring so
    the same backend selection logic isn't duplicated. Real providers fall
    back to the deterministic mock when their SDK/credentials are unavailable.
    """
    from repowise.core.providers.embedding.base import MockEmbedder

    if embedder_name_resolved == "gemini":
        try:
            from repowise.core.providers.embedding.gemini import GeminiEmbedder

            return GeminiEmbedder()
        except Exception:
            return MockEmbedder()
    if embedder_name_resolved == "openai":
        try:
            from repowise.core.providers.embedding.openai import OpenAIEmbedder

            return OpenAIEmbedder()
        except Exception:
            return MockEmbedder()
    return MockEmbedder()
