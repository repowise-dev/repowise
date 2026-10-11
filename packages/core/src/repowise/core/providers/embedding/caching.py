"""Memoize query embeddings so a repeated search does not pay the provider twice.

Every ``search_codebase`` call embeds its query text through the configured
provider — a network round trip, measured at ~215ms against OpenAI, on a path
where the vector lookup itself costs ~35ms. Agents repeat queries within a
session, and each repeat paid that round trip again.

Wrap the embedder rather than caching inside one provider or one store: the
query paths reach the embedder through three different store methods
(``search``, ``search_many``, ``embed_texts``), and there are five providers
with the same shape, so this is the one layer common to all of them.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any

#: Entries kept per wrapped embedder. A 1536-dimension vector costs roughly
#: 40KB live as a Python list, so this bounds one embedder at a few megabytes.
MAX_ENTRIES = 256


class CachingEmbedder:
    """An :class:`~repowise.core.providers.embedding.base.Embedder` that memoizes single texts.

    A cached vector is a pure function of (embedder configuration, text), so a
    hit is exactly as correct as a fresh call and never goes stale. The cache
    lives on the instance, so the configuration behind it is fixed for the life
    of every entry and the text alone identifies a vector.

    The gate is batch size, not caller: only single-text calls are cached. That
    is a proxy rather than a rule — a single-document write is cached too, and
    harmlessly, since the vector is the same either way. What it reliably keeps
    out is the bulk path, where hundreds of corpus strings that are never
    queried again would evict the handful of real queries this exists for.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if len(texts) != 1:
            return await self._inner.embed(texts)

        # Hashed rather than stored: a query reaches 30_000 characters upstream,
        # and holding the raw text would cost more than the vector it caches.
        key = hashlib.sha256(texts[0].encode()).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            # Copy: a caller that mutates its vector must not edit the entry.
            return [list(cached)]

        vectors = await self._inner.embed(texts)
        if len(vectors) == 1:
            self._cache[key] = list(vectors[0])
            while len(self._cache) > MAX_ENTRIES:
                self._cache.popitem(last=False)
        return vectors
