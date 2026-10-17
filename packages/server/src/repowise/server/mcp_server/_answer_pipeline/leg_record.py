"""Which retrieval legs ran for the current question, and how each ended."""

from __future__ import annotations

import contextvars

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
