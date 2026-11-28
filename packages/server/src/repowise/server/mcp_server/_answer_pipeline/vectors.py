"""The question's embedding, computed once per call, and the shared vector read."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.server.mcp_server._answer_pipeline.leg_record import _record_leg
from repowise.server.mcp_server._helpers import _EMBED_TIMEOUT_ENV, embed_timeout_s

_log = logging.getLogger("repowise.mcp.answer")

# One embedding per (store, question), shared by every stage that needs it.
# The key holds the store in its value so a recycled id cannot match. A few
# entries so concurrent questions do not evict each other.
_QUESTION_VECTOR_CACHE_MAX = 4
_QUESTION_VECTORS: OrderedDict[tuple[int, str], tuple[Any, list[float]]] = OrderedDict()


async def question_vector(ctx: Any, question: str) -> list[float] | None:
    """The question's embedding, cached per store and question.

    None when there is no store or embedder, or the embed failed; callers then
    search by text, which costs an embed but is still correct.
    """
    store = getattr(ctx, "vector_store", None)
    if store is None or not question:
        return None
    if not store_has_semantic_vectors(store):
        return None

    key = (id(store), question)
    cached = _QUESTION_VECTORS.get(key)
    if cached is not None:
        _QUESTION_VECTORS.move_to_end(key)
        return cached[1]

    timeout_s = embed_timeout_s()
    try:
        vectors = await asyncio.wait_for(
            store.embed_texts([question], kind="query"), timeout=timeout_s
        )
    except TimeoutError:
        # Recorded so a lexical-only answer is visible as one.
        _record_leg("embed", "timeout")
        _log.warning(
            "get_answer could not embed the question within %.1fs; retrieval "
            "continues without a question vector. Raise it with %s=<seconds>.",
            timeout_s,
            _EMBED_TIMEOUT_ENV,
        )
        return None
    except Exception:
        _record_leg("embed", "error")
        _log.warning(
            "get_answer could not embed the question up front; each retrieval "
            "stage will embed it again",
            exc_info=True,
        )
        return None
    if not vectors:
        # Backend without its own embedder: each stage embeds the text itself.
        _log.debug("Vector store cannot embed directly; per-stage embedding stands")
        return None

    vector = [float(v) for v in vectors[0]]
    _QUESTION_VECTORS[key] = (store, vector)
    while len(_QUESTION_VECTORS) > _QUESTION_VECTOR_CACHE_MAX:
        _QUESTION_VECTORS.popitem(last=False)
    return vector


async def vector_search(
    store: Any, question: str, limit: int, *, vector: list[float] | None
) -> list[Any]:
    """Nearest pages to *question*, by *vector* when the backend supports it.

    [] on a keyless store; checked here because every vector read in the
    pipeline goes through this, and the text fallback would embed.
    """
    if not store_has_semantic_vectors(store):
        return []
    if vector is not None:
        by_vector = await store.search_by_vector(vector, limit=limit)
        if by_vector is not None:
            return by_vector
    return await store.search(question, limit=limit)
