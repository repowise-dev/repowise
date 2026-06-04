"""Shared internals for the ``crud`` package.

Holds the datetime parser, batch-size constant, the job-status whitelist, and
the generic keyed SELECT-once-then-INSERT/UPDATE loop
(:func:`_batch_upsert_keyed`) that the per-domain batch upserts delegate to.
Private to the package.
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


async def _batch_upsert_keyed(
    session: AsyncSession,
    model: type[Any],
    items: Iterable[Any],
    *,
    prefilter: tuple[Any, ...],
    item_key_fn: Callable[[Any], Any],
    row_key_fn: Callable[[Any], Any],
    update_fn: Callable[[Any, Any], None],
    insert_fn: Callable[[Any], Any],
    batch_size: int | None = None,
) -> None:
    """Generic batch upsert with ONE existence query for the whole batch.

    The previous generic loop issued a SELECT round-trip per item; on bulk writes
    (graph nodes/edges, symbols, git metadata) that is 10k-30k SELECTs per
    persist and dominated the whole persist step (measured: 9.3s for a
    13k-node ``persist_graph_nodes``). Here all candidate rows matching
    *prefilter* (typically ``repository_id == X``) are fetched once and
    matched in memory: ``item_key_fn(item)`` must equal ``row_key_fn(row)``
    exactly when the legacy ``key_fn`` filter would have found that row.

    Within-batch duplicate keys keep the legacy outcome: the first item
    inserts, later ones update the pending object (the per-item SELECT used
    to see the autoflushed insert).
    """
    materialized = list(items)
    if not materialized:
        if batch_size is None:
            await session.flush()
        return

    existing_rows = (await session.execute(select(model).where(*prefilter))).scalars().all()
    by_key: dict[Any, Any] = {row_key_fn(row): row for row in existing_rows}

    if batch_size is None:
        chunks: list[list[Any]] = [materialized]
    else:
        chunks = [materialized[i : i + batch_size] for i in range(0, len(materialized), batch_size)]
    for chunk in chunks:
        for item in chunk:
            key = item_key_fn(item)
            existing = by_key.get(key)
            if existing is not None:
                update_fn(existing, item)
            else:
                obj = insert_fn(item)
                session.add(obj)
                by_key[key] = obj
        await session.flush()
