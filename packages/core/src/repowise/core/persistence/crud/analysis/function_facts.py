"""The per-function store: one row per function symbol, its role and body facts.

Facts are pure on a file's bytes, so a run rewrites the rows of the files it
walked and leaves the rest. A role is not: a seed or a call in another file
moves it, so every run restamps every row's role from its own role map.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.execution_graph import file_of_symbol
from ...models import FunctionFact

if TYPE_CHECKING:
    from ....analysis.execution_roles import ExecutionRoles

_CHUNK = 500
# Key ranges OR-ed into one DELETE: two bound values each, far under SQLite's
# variable limit, and SQLAlchemy keeps the OR flat, so no expression depth.
_RANGES_PER_DELETE = 200


def _row(repository_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
    assigns = row.get("receiver_assigns")
    return {
        "repository_id": repository_id,
        "symbol_id": row["symbol_id"],
        "awaits": row.get("awaits"),
        "is_generator": row.get("is_generator"),
        "uses_receiver": row.get("uses_receiver"),
        "receiver_assigns_known": row.get("receiver_assigns_known"),
        "receiver_assigns_json": json.dumps(list(assigns)) if assigns else None,
        "early_exits": row.get("early_exits"),
        "ccn": row.get("ccn"),
        "nloc": row.get("nloc"),
        "params": row.get("params"),
        "max_nesting": row.get("max_nesting"),
    }


def key_range(path: str) -> tuple[str, str]:
    """A file's rows as a half-open key range: every symbol id there is ``path::...``.

    Symbol ids carry forward slashes; ``:;`` is the first string after every
    ``path::`` suffix in bytewise order, which the column's ``C`` collation
    gives PostgreSQL too.
    """
    path = path.replace("\\", "/")
    return f"{path}::", f"{path}:;"


def _file_range_chunks(repository_id: str, paths: Iterable[str]) -> Iterator[Any]:
    """The rows of *paths* as WHERE clauses, a chunk of key ranges each."""
    ranges = sorted({key_range(path) for path in paths})
    for i in range(0, len(ranges), _RANGES_PER_DELETE):
        chunk = ranges[i : i + _RANGES_PER_DELETE]
        yield and_(
            FunctionFact.repository_id == repository_id,
            or_(
                *(
                    and_(FunctionFact.symbol_id >= low, FunctionFact.symbol_id < high)
                    for low, high in chunk
                )
            ),
        )


async def delete_file_rows(session: AsyncSession, repository_id: str, paths: Iterable[str]) -> None:
    """Delete the rows of *paths*, a chunk of key ranges per statement."""
    for clause in _file_range_chunks(repository_id, paths):
        await session.execute(delete(FunctionFact).where(clause))


async def get_file_facts(
    session: AsyncSession, repository_id: str, paths: Iterable[str]
) -> list[FunctionFact]:
    """The stored rows of the functions in *paths*."""
    out: list[FunctionFact] = []
    for clause in _file_range_chunks(repository_id, paths):
        out.extend((await session.execute(select(FunctionFact).where(clause))).scalars())
    return out


async def write_function_facts(
    session: AsyncSession,
    repository_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    file_paths: Iterable[str] | None = None,
    roles: ExecutionRoles | None = None,
) -> int:
    """Replace the rows of *file_paths* (every row when ``None``) with *rows*,
    then restamp every row's role from *roles*. Returns the rows written."""
    if file_paths is None:
        await session.execute(delete(FunctionFact).where(FunctionFact.repository_id == repository_id))
    else:
        await delete_file_rows(session, repository_id, file_paths)
    # Two walked functions can resolve to one symbol; the first keeps it. Key
    # order fills the clustered pages instead of splitting them half empty.
    by_symbol = {row["symbol_id"]: _row(repository_id, row) for row in reversed(list(rows))}
    fresh = [by_symbol[symbol] for symbol in sorted(by_symbol)]
    # On the table, not the mapper: the ORM bulk path splits a batch wherever a
    # column flips between NULL and a value, which doubled this write.
    for i in range(0, len(fresh), _CHUNK):
        await session.execute(insert(FunctionFact.__table__), fresh[i : i + _CHUNK])
    if roles is not None:
        await _restamp_roles(session, repository_id, roles)
    return len(fresh)


async def _restamp_roles(session: AsyncSession, repository_id: str, roles: ExecutionRoles) -> None:
    stored = await session.execute(
        select(FunctionFact.symbol_id, FunctionFact.execution_role).where(
            FunctionFact.repository_id == repository_id
        )
    )
    changed = [
        {"repository_id": repository_id, "symbol_id": symbol, "execution_role": role}
        for symbol, current in stored.all()
        if (role := roles.role_of(symbol, file_of_symbol(symbol))) != current
    ]
    for i in range(0, len(changed), _CHUNK):
        await session.execute(update(FunctionFact), changed[i : i + _CHUNK])


async def get_function_facts(
    session: AsyncSession, repository_id: str, symbol_ids: Iterable[str]
) -> dict[str, FunctionFact]:
    """The stored rows for *symbol_ids*, by symbol id."""
    ids = sorted(set(symbol_ids))
    out: dict[str, FunctionFact] = {}
    for i in range(0, len(ids), _CHUNK):
        result = await session.execute(
            select(FunctionFact).where(
                FunctionFact.repository_id == repository_id,
                FunctionFact.symbol_id.in_(ids[i : i + _CHUNK]),
            )
        )
        out.update((row.symbol_id, row) for row in result.scalars())
    return out


__all__ = [
    "delete_file_rows",
    "get_file_facts",
    "get_function_facts",
    "key_range",
    "write_function_facts",
]
