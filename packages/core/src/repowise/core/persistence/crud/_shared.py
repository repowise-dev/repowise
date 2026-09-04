"""Shared internals for the ``crud`` package.

Holds the datetime parser, batch-size constant, the job-status whitelist, and
the generic keyed SELECT-once-then-INSERT/UPDATE loop
(:func:`_batch_upsert_keyed`) that the per-domain batch upserts delegate to.
Private to the package.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_VALID_JOB_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "cancelled", "paused"}
)

_BATCH_SIZE = 500  # max rows per INSERT to stay under SQLite's parameter limit

# A running/pending GenerationJob's `updated_at` is a liveness heartbeat:
# update_job_status() stamps it on every write, and JobProgressCallback calls
# it roughly every 1-5s during real work (throttled, batched every 5 items)
# and unconditionally at every phase boundary. A row that hasn't been touched
# in this long is presumed abandoned by a crashed or killed process rather
# than genuinely still working between writes — long enough that one slow
# phase item (a large LLM call retried under rate-limit backoff) can't cross
# it, short enough that a real crash doesn't block new syncs on the repo for
# long. Both the startup stale-job sweep and the active-job concurrency guard
# key off this instead of trusting `status` alone (issue: a second server
# process's restart, in a multi-worker deployment sharing one DB, must not be
# able to invalidate a job another live process is still actually running).
JOB_HEARTBEAT_STALE_AFTER = timedelta(minutes=5)


def job_heartbeat_cutoff(*, now: datetime | None = None) -> datetime:
    """Timestamp before which a running/pending job's `updated_at` means the
    process that owned it is presumed dead, not merely between writes."""
    return (now or datetime.now(UTC)) - JOB_HEARTBEAT_STALE_AFTER


def _finding_file_path(finding: Any) -> str | None:
    """Read ``file_path`` from a dataclass-like finding or a plain dict."""
    if isinstance(finding, dict):
        return finding.get("file_path")
    return getattr(finding, "file_path", None)


def _parse_dt(ts: str) -> datetime:
    """Parse an ISO-8601 UTC string to a timezone-aware datetime."""
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


_GATE_LOAD_CHUNK = 400  # keys per IN (...) reload — under SQLite's parameter limit

# Above this share of the table, reload the rows in one unfiltered SELECT rather
# than as chunked IN (...) lookups. Measured on a 21,696-row membership snapshot
# where 91% of the rows moved: 54 chunked lookups cost 1.64s against 1.10s for
# the single scan they replaced, so a gate that skips almost nothing was paying
# for the privilege.
_GATE_FULL_LOAD_FRACTION = 0.5


@dataclass(frozen=True)
class UpsertGate:
    """Declares how to tell "this item is already what the row says".

    Opt-in for the callers whose payload is a full recomputation of a wide
    table: the graph node / metric / membership snapshots rewrite every row
    every update, and on a steady-state update almost none of them moved.
    With a gate, :func:`_batch_upsert_keyed` reads the comparison columns as
    plain tuples first (no ORM hydration), and hydrates + touches only the
    rows that actually differ.

    - *columns* are the comparison columns, keyed positionally; the key column
      is not one of them.
    - *item_values_fn* returns exactly the fields ``update_fn`` would write for
      that item. A field it returns that is not in *columns* makes the item
      unconditionally "changed": the gate can never suppress a write it cannot
      prove is redundant, so a payload that grows a field degrades to the
      ungated behaviour instead of silently dropping it.
    - *float_columns* are compared with ``float_atol`` absolute tolerance. See
      :func:`_gate_unchanged` for why that is not the same thing as sloppiness.
    - *prefetched* lets a caller that already scans the table for its own
      reasons hand the comparison data over, so the gate costs no extra read.
      Keys are the row keys; values are tuples ordered like *columns*. It must
      describe the table as the upsert will find it: supplying it is the one way
      to reach the gate without issuing a statement, so nothing autoflushes, and
      a map read before a pending change to the same table would skip exactly
      the rows that change was about. Build it from a SELECT in the same call,
      after any write the caller makes to that table, and exclude any key the
      caller then deletes.
    """

    key_column: Any
    columns: tuple[str, ...]
    item_values_fn: Callable[[Any], dict[str, Any]]
    float_columns: frozenset[str] = field(default_factory=frozenset)
    float_atol: float = 0.0
    prefetched: dict[Any, tuple] | None = None


def _gate_unchanged(gate: UpsertGate, index: dict[str, int], item: Any, stored: tuple) -> bool:
    """True when writing *item* over *stored* would not move any column.

    Float columns compare with an absolute tolerance because the centrality
    kernels are not bit-stable across processes: PageRank sums in graph
    iteration order, which the resolvers do not fix, so an unchanged repo
    re-persists every file node with a value that moved in the 17th decimal.
    Exact equality therefore skips almost nothing on the table where the
    skipping is worth the most.

    The tolerance does not accumulate. The comparison is always against the
    *stored* value, never against the previous run's fresh value, so a real drift
    that creeps by less than the tolerance per run still crosses it once the
    total distance from the stored number exceeds ``float_atol`` — at which
    point the row is written and the anchor resets. The stored value is
    therefore never further than ``float_atol`` from the truth.
    """
    for name, new in gate.item_values_fn(item).items():
        pos = index.get(name)
        if pos is None:
            return False
        old = stored[pos]
        if old == new:
            continue
        if name in gate.float_columns and old is not None and new is not None:
            try:
                if abs(float(old) - float(new)) <= gate.float_atol:
                    continue
            except (TypeError, ValueError):
                return False
        return False
    return True


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
    gate: UpsertGate | None = None,
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

    *gate*, when given, skips items whose stored row already matches (see
    :class:`UpsertGate`). Only the surviving items are hydrated as ORM rows.
    It is disabled for a batch with duplicate keys, where "already matches"
    would have to reason about the whole run of items sharing that key.
    """
    materialized = list(items)
    if not materialized:
        if batch_size is None:
            await session.flush()
        return

    gated_keys: list[Any] | None = None
    stored_count = 0
    if gate is not None:
        keys = [item_key_fn(item) for item in materialized]
        if len(set(keys)) == len(keys):
            index = {name: pos for pos, name in enumerate(gate.columns)}
            if gate.prefetched is not None:
                stored = gate.prefetched
            else:
                stored_rows = (
                    await session.execute(
                        select(gate.key_column, *[getattr(model, c) for c in gate.columns]).where(
                            *prefilter
                        )
                    )
                ).all()
                stored = {row[0]: row[1:] for row in stored_rows}
            stored_count = len(stored)
            surviving: list[Any] = []
            gated_keys = []
            for item, key in zip(materialized, keys, strict=True):
                current = stored.get(key)
                if current is None:
                    surviving.append(item)  # new row — nothing to compare against
                    continue
                if _gate_unchanged(gate, index, item, current):
                    continue
                surviving.append(item)
                gated_keys.append(key)
            materialized = surviving
            if not materialized:
                await session.flush()
                return

    existing_rows: list[Any] = []
    if gated_keys is None or len(gated_keys) >= stored_count * _GATE_FULL_LOAD_FRACTION:
        existing_rows = list(
            (await session.execute(select(model).where(*prefilter))).scalars().all()
        )
    else:
        # Only the rows this batch will actually write. Hydrating the whole
        # table as ORM objects is the other half of the cost the gate removes.
        for i in range(0, len(gated_keys), _GATE_LOAD_CHUNK):
            chunk_keys = gated_keys[i : i + _GATE_LOAD_CHUNK]
            existing_rows.extend(
                (
                    await session.execute(
                        select(model).where(*prefilter, gate.key_column.in_(chunk_keys))  # type: ignore[union-attr]
                    )
                )
                .scalars()
                .all()
            )
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
