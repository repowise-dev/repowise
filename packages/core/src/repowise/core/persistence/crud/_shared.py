"""Shared internals for the ``crud`` package.

Holds the datetime parser, batch-size constant, the job-status whitelist, and
the generic SELECT-then-INSERT/UPDATE loop (:func:`_batch_upsert`) that the
per-domain batch upserts delegate to. Private to the package.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_VALID_JOB_STATUSES = frozenset({"pending", "running", "completed", "failed", "paused"})

_BATCH_SIZE = 500  # max rows per INSERT to stay under SQLite's parameter limit


def _parse_dt(ts: str) -> datetime:
    """Parse an ISO-8601 UTC string to a timezone-aware datetime."""
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _batch_upsert(
    session: AsyncSession,
    model: type[Any],
    items: Iterable[Any],
    *,
    key_fn: Callable[[Any], tuple[Any, ...]],
    update_fn: Callable[[Any, Any], None],
    insert_fn: Callable[[Any], Any],
    batch_size: int | None = None,
) -> None:
    """Generic SELECT-then-INSERT/UPDATE loop shared by the batch upserts.

    For each item, look up the existing row with the filter expressions from
    ``key_fn(item)``; if found, mutate it via ``update_fn(existing, item)``,
    otherwise ``session.add(insert_fn(item))``.

    Flushes once after the whole sequence, or once per ``batch_size`` chunk when
    *batch_size* is given (matching the SELECT-then-write semantics the callers
    relied on before this was extracted — including no flush at all on an empty
    batched sequence).
    """
    if batch_size is None:
        chunks: list[list[Any]] = [list(items)]
    else:
        materialized = list(items)
        chunks = [materialized[i : i + batch_size] for i in range(0, len(materialized), batch_size)]
    for chunk in chunks:
        for item in chunk:
            existing = (
                await session.execute(select(model).where(*key_fn(item)))
            ).scalar_one_or_none()
            if existing is not None:
                update_fn(existing, item)
            else:
                session.add(insert_fn(item))
        await session.flush()
