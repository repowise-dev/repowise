"""repowise embedding provider sub-package.

All embedders implement the Embedder protocol. Use get_embedder() from the
registry to instantiate an embedder by name.

    from repowise.core.providers.embedding import get_embedder

    embedder = get_embedder("openai", api_key="sk-...")
    vectors = await embedder.embed(["text to embed"])

Built-in embedders:
    openai             — text-embedding-3-small (1536d), text-embedding-3-large (3072d)
    openai_compatible  — OpenAI-compatible local servers (Ollama, LocalAI, etc.)
    gemini             — gemini-embedding-001 (768d, up to 3072d)
    mock               — deterministic 8d vectors (zero deps, testing only)
"""

from repowise.core.providers.embedding.base import Embedder, MockEmbedder
from repowise.core.providers.embedding.registry import get_embedder, list_embedders, register_embedder

__all__ = [
    "Embedder",
    "MockEmbedder",
    "get_embedder",
    "list_embedders",
    "register_embedder",
]
