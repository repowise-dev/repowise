"""OpenAI-compatible embedding support for local/self-hosted embedding servers.

Supports any OpenAI-compatible embedding API endpoint (Ollama, LocalAI, vLLM, etc.)
by wrapping the OpenAIEmbedder with flexible environment variable fallbacks.

Environment Variables (in priority order):
    OPENAI_COMPATIBLE_BASE_URL   → base_url for the compatible server
    OPENAI_BASE_URL             → fallback base_url
    OPENAI_COMPATIBLE_API_KEY   → API key for the compatible server
    OPENAI_API_KEY              → fallback API key
    (defaults to empty string/placeholder for local servers without auth)

Usage:
    # Ollama example
    embedder = OpenAICompatibleEmbedder(
        base_url="http://localhost:11434/v1",
        model="nomic-embed-text"
    )
    vectors = await embedder.embed(["Hello world"])

    # LocalAI example
    embedder = OpenAICompatibleEmbedder(
        base_url="http://localhost:8080/v1",
        model="all-minilm"
    )
"""

from __future__ import annotations

import os

from repowise.core.providers.embedding.openai import OpenAIEmbedder


class OpenAICompatibleEmbedder(OpenAIEmbedder):
    """OpenAI-compatible embedding adapter for local/self-hosted servers.

    Extends OpenAIEmbedder with flexible fallback logic for base_url and api_key,
    allowing use with local servers (Ollama, LocalAI, etc.) that may not require
    authentication.

    Args:
        api_key:  API key. Falls back to OPENAI_COMPATIBLE_API_KEY, OPENAI_API_KEY,
                  or empty string (uses "none" placeholder for OpenAI SDK).
        model:    Embedding model name. Default: "text-embedding-3-small".
        timeout:  Request timeout in seconds. Default: 10.0.
        base_url: API endpoint URL. Falls back to OPENAI_COMPATIBLE_BASE_URL,
                  OPENAI_BASE_URL, or None (uses OpenAI's default).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        timeout: float = 10.0,
        base_url: str | None = None,
    ) -> None:
        # Resolve base_url with fallback chain
        resolved_base_url = (
            base_url
            or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )

        # Resolve api_key with fallback chain
        resolved_api_key = (
            api_key
            or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )

        # If api_key ends up empty, use "none" placeholder to satisfy OpenAI SDK
        if not resolved_api_key:
            resolved_api_key = "none"

        # Delegate to parent OpenAIEmbedder
        # We bypass the parent's ValueError check by always providing a key
        super().__init__(
            api_key=resolved_api_key,
            model=model,
            timeout=timeout,
            base_url=resolved_base_url,
        )

    @property
    def dimensions(self) -> int:
        """Return default dimension of 1536.

        For OpenAI-compatible servers, we rely on the server's actual output
        dimension rather than maintaining a model-to-dimension mapping.
        """
        return 1536
