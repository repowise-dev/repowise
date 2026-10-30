"""Diff a file-scoped slice of ``graph_edges`` against a fresh edge set.

Private to the ``crud`` package; :func:`.graph.reconcile_edges_for_files` is the
only caller. Writing just the difference keeps a parser-change widen, which
reconciles every file, from deleting and reinserting the whole table.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from sqlalchemy import and_, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GraphEdge
from ._shared import _BATCH_SIZE, _insert_columns

EdgeKey = tuple[str, str, str]

# Columns that identify a row or only record when it was written.
_NON_CONTENT = frozenset(
    {"id", "repository_id", "created_at", "source_node_id", "target_node_id", "edge_type"}
)


@cache
def _content_defaults() -> tuple[tuple[str, object], ...]:
    """``(column, default)`` per content column, derived from the model.

    The default is what an INSERT writes for a None value; a column whose
    default is computed has no comparable value and is marked ``_UNCOMPARABLE``.
    """
    out = []
    for key, default, _server_only in _insert_columns(GraphEdge):
        if key in _NON_CONTENT:
            continue
        if default is None:
            out.append((key, None))
        else:
            out.append((key, default.arg if default.is_scalar else _UNCOMPARABLE))
    return tuple(out)


_UNCOMPARABLE = object()


def _content_columns() -> tuple[str, ...]:
    return tuple(key for key, _d in _content_defaults())


def _content(values: dict) -> tuple | None:
    """The content an INSERT of *values* would store, or None if unknowable."""
    out = []
    for key, default in _content_defaults():
        value = values.get(key)
        if value is None:
            if default is _UNCOMPARABLE:
                return None
            value = default
        out.append(value)
    return tuple(out)


def _key(values: dict) -> EdgeKey:
    return (values["source_node_id"], values["target_node_id"], values["edge_type"])


# Above this many owned node ids per stored edge row, one scan of the repo's
# edges beats chunked index probes. Measured on a 308k-edge store: a probe costs
# ~0.1 ms per id (source and target index), a scan ~3 us per row.
_SCAN_IDS_PER_ROW = 1 / 32


def _row_columns() -> list:
    content = [getattr(GraphEdge, c) for c in _content_columns()]
    return [
        GraphEdge.source_node_id,
        GraphEdge.target_node_id,
        GraphEdge.edge_type,
        GraphEdge.id,
        *content,
    ]


async def load_owned_edges(
    session: AsyncSession,
    repository_id: str,
    node_ids: list[str],
    target_owned_hints: frozenset[str],
    chunk: int,
) -> dict[EdgeKey, tuple]:
    """``key -> (row id, *content)`` of every row a reconcile of *node_ids* owns.

    Owned means sourced at one of *node_ids*, or a target-owned hint pointing
    at one of them: exactly the rows the reconcile would otherwise delete.
    """
    if len(node_ids) > chunk and await _scan_is_cheaper(session, repository_id, len(node_ids)):
        return await _scan_owned(session, repository_id, set(node_ids), target_owned_hints)
    stored: dict[EdgeKey, tuple] = {}
    for i in range(0, len(node_ids), chunk):
        batch = node_ids[i : i + chunk]
        rows = await session.execute(
            select(*_row_columns()).where(
                GraphEdge.repository_id == repository_id,
                or_(
                    GraphEdge.source_node_id.in_(batch),
                    and_(
                        GraphEdge.hint_source.in_(target_owned_hints),
                        GraphEdge.target_node_id.in_(batch),
                    ),
                ),
            )
        )
        for row in rows:
            stored[(row[0], row[1], row[2])] = tuple(row[3:])
    return stored


async def _scan_is_cheaper(session: AsyncSession, repository_id: str, id_count: int) -> bool:
    total = (
        await session.execute(
            select(func.count())
            .select_from(GraphEdge)
            .where(GraphEdge.repository_id == repository_id)
        )
    ).scalar_one()
    return id_count >= total * _SCAN_IDS_PER_ROW


async def _scan_owned(
    session: AsyncSession,
    repository_id: str,
    node_ids: set[str],
    target_owned_hints: frozenset[str],
) -> dict[EdgeKey, tuple]:
    """:func:`load_owned_edges` as one scan, with the ownership test in Python."""
    hint_at = 4 + _content_columns().index("hint_source")
    rows = await session.execute(
        select(*_row_columns()).where(GraphEdge.repository_id == repository_id)
    )
    return {
        (row[0], row[1], row[2]): tuple(row[3:])
        for row in rows
        if row[0] in node_ids or (row[hint_at] in target_owned_hints and row[1] in node_ids)
    }


def diff_edges(
    stored: dict[EdgeKey, tuple],
    payloads: list[dict],
    to_row: Callable[[dict], dict],
) -> tuple[list[str], list[dict]]:
    """``(row ids to delete, rows to insert)`` turning *stored* into *payloads*.

    *payloads* are content values keyed by column (no id); *to_row* builds the
    full INSERT row and runs only for rows that are written. A stored row whose
    content matches is kept; a changed one is deleted and reinserted, so the
    unique key never collides.
    """
    keep: set[EdgeKey] = set()
    inserts: list[dict] = []
    for payload in payloads:
        key = _key(payload)
        current = stored.get(key)
        if current is not None:
            content = _content(payload)
            if content is not None and current[1:] == content:
                keep.add(key)
                continue
        inserts.append(to_row(payload))
    stale = [current[0] for key, current in stored.items() if key not in keep]
    return stale, inserts


async def apply_edge_diff(
    session: AsyncSession,
    repository_id: str,
    stale_ids: list[str],
    inserts: list[dict],
    chunk: int,
) -> None:
    """Delete *stale_ids*, then insert *inserts* as plain executemany batches."""
    for i in range(0, len(stale_ids), chunk):
        await session.execute(
            delete(GraphEdge).where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.id.in_(stale_ids[i : i + chunk]),
            )
        )
    stmt = insert(GraphEdge).execution_options(render_nulls=True)
    for i in range(0, len(inserts), _BATCH_SIZE):
        await session.execute(stmt, inserts[i : i + _BATCH_SIZE])
    await session.flush()
