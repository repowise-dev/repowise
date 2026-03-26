"""Embedder protocol and implementations for repowise vector search.

The Embedder protocol is structural (runtime_checkable) so any object with
an `embed()` method and a `dimensions` property satisfies it without
inheriting from a base class.

MockEmbedder is the built-in test implementation: deterministic, 8-dimensional,
zero external dependencies.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Structural protocol for text embedding models.

    All implementations must produce unit-length (L2-normalized) vectors so
    that cosine similarity equals the dot product — important for InMemoryVectorStore
    and pgvector's <=> operator (which uses cosine distance by default).
    """

    @property
    def dimensions(self) -> int:
        """Number of dimensions in the embedding vector."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of unit-length float vectors, one per input string.
            Each vector has exactly ``self.dimensions`` elements.
        """
        ...


class MockEmbedder:
    """Deterministic 8-dimensional embedder for testing.

    Uses the first 8 bytes of SHA-256(text) interpreted as 8 unsigned bytes,
    divided by 255.0, then L2-normalised to unit length.

    Properties:
    - Deterministic: same input always produces the same vector.
    - Different texts virtually always produce different vectors (hash collision
      probability negligible for test inputs).
    - All output vectors have unit L2 norm (cosine similarity = dot product).
    - Zero external dependencies.
    """

    dimensions: int = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            # Take first 8 bytes as raw floats via struct unpack
            # (4 bytes each → 2 float32 values would only give 2 dims)
            # Instead: use 8 unsigned bytes normalised to [0, 1]
            raw = [digest[i] / 255.0 for i in range(8)]
            norm = math.sqrt(sum(x * x for x in raw))
            if norm == 0.0:
                norm = 1.0
            results.append([x / norm for x in raw])
        return results
