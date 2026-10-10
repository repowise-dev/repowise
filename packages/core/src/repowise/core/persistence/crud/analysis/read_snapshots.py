"""Read views built at index or update time, served while their key holds.

A view the read path would otherwise rebuild in every new process (the Fix
first queue, the next-actions view) is written once by the writer that last
touched its stores, under a key naming what it was built from: the code
version, the analyzed commit, and the newest write and row count of every
store it reads. A reader recomputes the key, which costs one aggregate per
store, and serves the stored view only when the key matches; anything else
(a triage, a coverage ingest, an upgraded repowise, an older store without
the table) builds live, exactly as before. Readers never write.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import fields, is_dataclass
from functools import cache
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core import __version__

from ...models import ReadSnapshot

logger = logging.getLogger(__name__)

#: One store a view reads: its newest-write column (``None`` for an
#: append-only table, where the count alone moves) and the rows that count.
StorePart = tuple[Any, Any]


async def store_stamp(session: AsyncSession, parts: Sequence[StorePart]) -> tuple[Any, ...]:
    """The newest write and the row count of each store part, in order.

    A rewrite or a triage (the write column moves) or a deletion (the count
    moves) changes it.
    """
    stamps: list[Any] = []
    for column, where in parts:
        newest = func.max(column) if column is not None else None
        stmt = select(*(c for c in (newest, func.count()) if c is not None)).where(where)
        stamps.extend((await session.execute(stmt)).one())
    return tuple(stamps)


def snapshot_key(*parts: Any) -> str:
    """The stored form of a key: the code version first, then ``parts``.

    Ceiling: a development checkout keeps one version across commits, so a
    builder change there is served stale until the next update rewrites the
    row; a release changes the version.
    """
    return json.dumps([__version__, *parts], default=str)


async def read_snapshot(
    session: AsyncSession, repo_id: str, kind: str, key: Callable[[], Awaitable[str]]
) -> Any | None:
    """The stored payload for ``kind`` when it was built under ``await key()``.

    Any failure is a miss and the caller builds live: a store missing from an
    older index (the key cannot be read), or a store from before this table.
    The savepoint keeps that failure off the caller's session.
    """
    try:
        async with session.begin_nested():
            current = await key()
            row = (
                await session.execute(
                    select(ReadSnapshot.key, ReadSnapshot.payload_json).where(
                        ReadSnapshot.repository_id == repo_id, ReadSnapshot.kind == kind
                    )
                )
            ).first()
    except Exception as exc:  # build live instead
        logger.debug("read_snapshot %s unavailable: %s", kind, exc)
        return None
    if row is None or row.key != current:
        return None
    return json.loads(row.payload_json)


async def refresh_snapshot(
    session: AsyncSession,
    repo_id: str,
    kind: str,
    key: str,
    build: Callable[[], Awaitable[Any]],
) -> bool:
    """Store ``await build()`` (JSON-ready) as the ``kind`` row under ``key``,
    unless the row already holds that key. Returns whether it wrote."""
    row = await session.get(ReadSnapshot, (repo_id, kind))
    if row is not None and row.key == key:
        return False
    text = json.dumps(await build(), ensure_ascii=False)
    if row is None:
        session.add(ReadSnapshot(repository_id=repo_id, kind=kind, key=key, payload_json=text))
    else:
        row.key, row.payload_json = key, text
    await session.flush()
    return True


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def decode(tp: Any, value: Any) -> Any:
    """``value`` (from ``dataclasses.asdict`` through JSON) back as type ``tp``.

    Covers the shapes the stored views use: frozen dataclasses, homogeneous
    tuples, optionals, and plain JSON values (``dict`` / ``Mapping`` /
    ``Literal`` / ``Any``), which come back as they are.
    """
    if value is None:
        return None
    if is_dataclass(tp):
        hints = _hints(tp)
        return tp(**{f.name: decode(hints[f.name], value[f.name]) for f in fields(tp) if f.init})
    origin = get_origin(tp)
    if origin is tuple:
        item = get_args(tp)[0]
        return tuple(decode(item, v) for v in value)
    if origin in (Union, UnionType):
        options = [a for a in get_args(tp) if a is not type(None)]
        return decode(options[0], value) if len(options) == 1 else value
    return value


__all__ = [
    "StorePart",
    "decode",
    "read_snapshot",
    "refresh_snapshot",
    "snapshot_key",
    "store_stamp",
]
