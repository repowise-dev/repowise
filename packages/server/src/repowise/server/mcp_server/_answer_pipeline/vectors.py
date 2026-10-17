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

# Budget for embedding the question is resolved live by embed_timeout_s()
# (see _helpers.py) — REPOWISE_EMBED_TIMEOUT_S, falling back to the 8s a warm
# hosted endpoint needs. The searches that used to embed inline were bounded
# at that same 8s including the embed, so the round-trip keeps that ceiling
# now that it happens on its own.

# Answering one question used to embed its text up to three times: once for the
# main fetch, once for the concept lookup on a subsystem-shaped question, once
# for the neighbourhood re-rank on a flow-shaped one. Each is a network
# round-trip to the embedding provider, billed, for a vector that cannot differ
# between them.
#
# Keyed by ``(id(store), question)`` and holding the store in the value, so an
# id can never be recycled onto a different store while its entries are live.
# Capacity is a handful of entries rather than one: concurrent calls with
# different questions would otherwise evict each other and re-embed. An entry is
# a pure function of (embedder, text), so a hit across calls is as correct as a
# hit within one.
_QUESTION_VECTOR_CACHE_MAX = 4
_QUESTION_VECTORS: OrderedDict[tuple[int, str], tuple[Any, list[float]]] = OrderedDict()


async def question_vector(ctx: Any, question: str) -> list[float] | None:
    """The question's embedding, computed at most once per store and question.

    Returns None when there is no store, the backend holds no embedder of its
    own, or the embed failed — callers then fall back to the store's own
    ``search(text)``, which embeds inline. That fallback is a cost regression,
    never a correctness one, so it warns rather than raising.
    """
    store = getattr(ctx, "vector_store", None)
    if store is None or not question:
        return None
    if not store_has_semantic_vectors(store):
        # Nothing will consume it, so do not pay to compute it.
        return None

    key = (id(store), question)
    cached = _QUESTION_VECTORS.get(key)
    if cached is not None:
        _QUESTION_VECTORS.move_to_end(key)
        return cached[1]

    timeout_s = embed_timeout_s()
    try:
        vectors = await asyncio.wait_for(store.embed_texts([question]), timeout=timeout_s)
    except TimeoutError:
        # The A18 case, and the one worth naming separately: the embedder is
        # configured, reachable and healthy, and simply did not answer inside
        # the budget. Nothing downstream fails, so without this the answer is
        # lexical-only and indistinguishable from one that was not.
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
        # Backend without an embedder of its own. Not an error — but it means
        # every stage pays for its own round-trip, so it is worth seeing.
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
    """Nearest pages to *question*, reusing *vector* when the backend allows it.

    Every backend that can search by raw vector is spared a second embedding of
    text it has already embedded; one that cannot returns None from
    ``search_by_vector`` and is asked to search the text as before.

    Returns nothing on a keyless store. Guarded here rather than only at the
    callers because this is the shared floor under every vector read in the
    answer pipeline, and the text fallback on the last line would otherwise
    route a keyless store straight back into the embedder this is avoiding.
    """
    if not store_has_semantic_vectors(store):
        return []
    if vector is not None:
        by_vector = await store.search_by_vector(vector, limit=limit)
        if by_vector is not None:
            return by_vector
    return await store.search(question, limit=limit)
