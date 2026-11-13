"""Which retrieval legs ran for the current question, and how each ended."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

# How each leg ended for the current question. Legs fail soft, so without
# this a lexical-only answer looks like a whole one; ``embedder_live`` says an
# embedder exists, this says whether it was used.
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

    ``asyncio.gather`` copies the context, not the dict, so every leg task
    writes into this one record.
    """
    record: dict[str, str] = {}
    _LEG_RECORD.set(record)
    return record


def retrieval_legs() -> dict[str, str]:
    """Leg outcomes for the last question, e.g. ``{"fts": "ok", "vector": "timeout"}``.

    Holds an ``embed`` key when embedding the question failed.
    """
    return dict(_LEG_RECORD.get() or {})


def degraded_legs(legs: dict[str, str]) -> list[str]:
    """The legs that broke, sorted. ``keyless`` is configuration, not breakage."""
    return sorted(leg for leg, outcome in legs.items() if outcome not in ("ok", "keyless"))


# Vector legs still running after their caller moved on. Held so the loop
# keeps them alive: each one finishes into the query-embedding cache.
_LATE_LEGS: set[asyncio.Future] = set()


async def vector_leg_within(leg: asyncio.Future, grace_s: float) -> Any:
    """The vector leg's result if it lands within *grace_s*, else ``[]``.

    Called once the other legs are in, so full-text hits are never held up by
    a cold store or a rate-limited embedder. A late leg is left to finish: its
    embedding is cached, so asking again gets the semantic hits. The skip is
    recorded as the ``vector`` leg's outcome.
    """
    try:
        return await asyncio.wait_for(asyncio.shield(leg), timeout=grace_s)
    except TimeoutError:
        _LATE_LEGS.add(leg)
        leg.add_done_callback(_LATE_LEGS.discard)
        _record_leg("vector", "skipped")
        return []
