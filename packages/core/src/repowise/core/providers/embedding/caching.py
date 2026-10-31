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
import time
from collections import OrderedDict
from typing import Any

#: Entries kept per wrapped embedder. A 1536-dimension vector costs roughly
#: 40KB live as a Python list, so this bounds one embedder at a few megabytes.
MAX_ENTRIES = 256

#: Seconds a failed query embed keeps later query embeds from calling the
#: provider. One answer embeds the question in several stages, so a dead key
#: or a rate limit would otherwise be paid once per stage.
FAILURE_COOLDOWN_S = 60.0


class EmbedderCoolingDownError(RuntimeError):
    """A query embed skipped because the provider failed under a minute ago."""


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
        self._query_down_until = 0.0

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if len(texts) != 1:
            return await self._call(texts, kind)

        # Hashed rather than stored: a query reaches 30_000 characters upstream,
        # and holding the raw text would cost more than the vector it caches.
        # *kind* is part of the key: with an asymmetric prefix configured the
        # same text embeds differently as a query and as a document.
        key = hashlib.sha256(f"{kind}:{texts[0]}".encode()).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            # Copy: a caller that mutates its vector must not edit the entry.
            return [list(cached)]

        vectors = await self._call(texts, kind)
        if len(vectors) == 1:
            self._cache[key] = list(vectors[0])
            while len(self._cache) > MAX_ENTRIES:
                self._cache.popitem(last=False)
        return vectors

    async def _call(self, texts: list[str], kind: str) -> list[list[float]]:
        """The provider's embed; a query one fails fast while a recent one failed.

        Only queries: a document batch is an index write that should surface
        its own error. Ceiling: a query the caller times out and cancels does
        not count as a failure; the SDK timeout raising later does.
        """
        if kind != "query":
            return await self._inner.embed(texts, kind=kind)
        if time.monotonic() < self._query_down_until:
            raise EmbedderCoolingDownError("the embedder failed recently; query embeds are paused")
        try:
            return await self._inner.embed(texts, kind=kind)
        except Exception as exc:
            if _endpoint_failure(exc):
                self._query_down_until = time.monotonic() + FAILURE_COOLDOWN_S
            raise


#: Client-error statuses that still say the endpoint is unusable: a bad key,
#: no access, a timeout, a rate limit or spent quota.
_ENDPOINT_CLIENT_ERRORS = frozenset({401, 403, 408, 429})


def _endpoint_failure(exc: Exception) -> bool:
    """Whether *exc* says the endpoint is down, not that this one query was refused.

    A rejected query (an oversized or malformed one, a 400) must not pause the
    queries after it. An error with no status (a refused connection, a
    timeout) counts as the endpoint's.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        return True
    return not 400 <= status < 500 or status in _ENDPOINT_CLIENT_ERRORS
