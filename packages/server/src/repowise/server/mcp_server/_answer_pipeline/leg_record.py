"""Which retrieval legs ran for the current question, and how each ended."""

from __future__ import annotations

import contextvars

# Which retrieval legs actually ran for the current question (finding A18).
#
# Every leg here is best-effort by design: a slow vector store must not be able
# to block an answer. The defect was that the fallback was *silent*. An embed
# that times out returns no vector, retrieval quietly continues lexical-only,
# and every health signal still reports green — ``embedder_live`` included,
# because a configured embedder is live whether or not this particular call
# beat the clock. Five queries in one bake-off run were answered without their
# vector leg and nothing in the response, the logs' structured fields, or the
# per-cell record said so.
#
# So the leg outcome travels with the answer. ``embedder_live`` says the
# embedder exists; this says whether it was used. Those are different claims
# and only one of them is about the answer the caller is holding.
_LEG_RECORD: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "repowise_retrieval_legs", default=None
)


def _record_leg(leg: str, outcome: str) -> None:
    """Note how one retrieval leg ended, if anyone is collecting."""
    record = _LEG_RECORD.get()
    if record is not None:
        record[leg] = outcome


def begin_leg_record() -> dict[str, str]:
    """Start collecting leg outcomes for one question. Returns the record.

    The legs run under ``asyncio.gather``, which copies the context into each
    task. Copying rebinds names, not objects, so the tasks all mutate this one
    dict and the caller sees what they wrote.
    """
    record: dict[str, str] = {}
    _LEG_RECORD.set(record)
    return record


def retrieval_legs() -> dict[str, str]:
    """How each retrieval leg ended for the question just answered.

    ``{"fts": "ok", "vector": "timeout", "symbol": "ok"}`` and so on, plus an
    ``embed`` key when embedding the question is what failed. Empty before any
    retrieval has run.
    """
    return dict(_LEG_RECORD.get() or {})


def degraded_legs(legs: dict[str, str]) -> list[str]:
    """The legs that *broke*, named. Empty when retrieval was whole.

    ``keyless`` is not degradation and is deliberately excluded. A keyless index
    has no semantic vectors by construction, so its vector leg is permanently
    absent rather than transiently broken, and naming it here would put a
    failure marker on every answer the mode ever produces. The configuration
    itself is reported once per response by ``_meta`` instead.
    """
    return sorted(leg for leg, outcome in legs.items() if outcome not in ("ok", "keyless"))
