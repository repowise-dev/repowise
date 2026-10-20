"""The per-function store: one row per function symbol, its role, body facts and tests.

Facts are pure on a file's bytes, so a run rewrites the rows of the files it
walked and leaves the rest. A role is not: a seed or a call in another file
moves it, so every run restamps every row's role from its own role map. The
nearest tests move the same way (a test or a caller elsewhere changes), so they
are restamped with it, and only the rows whose answer changed are written.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.execution_graph import file_of_symbol
from ....analysis.function_test_map import (
    FunctionTests,
    MappedTest,
    decode_tests,
    encoded_map,
)
from ...models import FunctionFact, GraphNode

if TYPE_CHECKING:
    from ....analysis.execution_roles import ExecutionRoles

_CHUNK = 500
# Key ranges OR-ed into one DELETE: two bound values each, far under SQLite's
# variable limit, and SQLAlchemy keeps the OR flat, so no expression depth.
_RANGES_PER_DELETE = 200


def _row(repository_id: str, row: Mapping[str, Any], tests: Mapping[str, str]) -> dict[str, Any]:
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
        "tests_json": tests.get(row["symbol_id"]),
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
    tests: FunctionTests | None = None,
) -> int:
    """Replace the rows of *file_paths* (every row when ``None``) with *rows*,
    then restamp every row's role from *roles* and its tests from *tests*
    (``analysis.function_test_map``). Returns the rows written."""
    if file_paths is None:
        await session.execute(delete(FunctionFact).where(FunctionFact.repository_id == repository_id))
    else:
        await delete_file_rows(session, repository_id, file_paths)
    # Two walked functions can resolve to one symbol; the first keeps it. Key
    # order fills the clustered pages instead of splitting them half empty.
    encoded = encoded_map(tests) if tests is not None else {}
    by_symbol = {row["symbol_id"]: _row(repository_id, row, encoded) for row in reversed(list(rows))}
    fresh = [by_symbol[symbol] for symbol in sorted(by_symbol)]
    # On the table, not the mapper: the ORM bulk path splits a batch wherever a
    # column flips between NULL and a value, which doubled this write.
    for i in range(0, len(fresh), _CHUNK):
        await session.execute(insert(FunctionFact.__table__), fresh[i : i + _CHUNK])
    if roles is not None or tests is not None:
        await _restamp(session, repository_id, roles, None if tests is None else encoded)
    return len(fresh)


async def _restamp(
    session: AsyncSession,
    repository_id: str,
    roles: ExecutionRoles | None,
    tests: Mapping[str, str] | None,
) -> None:
    """Write each row's role and tests where they moved. *tests* is the run's
    encoded map; a symbol it leaves out stores NULL (never mapped)."""
    stored = (
        await session.execute(
            select(
                FunctionFact.symbol_id, FunctionFact.execution_role, FunctionFact.tests_json
            ).where(FunctionFact.repository_id == repository_id)
        )
    ).all()
    if roles is not None:
        await _write_moved(
            session,
            repository_id,
            _moved(stored, 1, "execution_role", lambda s: roles.role_of(s, file_of_symbol(s))),
        )
    if tests is not None:
        await _write_moved(session, repository_id, _moved(stored, 2, "tests_json", tests.get))


def _moved(
    stored: Iterable[Any], at: int, column: str, now: Callable[[str], Any]
) -> list[dict[str, Any]]:
    """``{symbol_id, column}`` for each stored row whose *column* (row index
    *at*) differs from *now*'s answer."""
    return [
        {"symbol_id": row[0], column: value}
        for row in stored
        if (value := now(row[0])) != row[at]
    ]


async def _write_moved(
    session: AsyncSession, repository_id: str, changed: list[dict[str, Any]]
) -> None:
    """One column per batch: the ORM bulk path splits a batch whose rows set different columns."""
    for i in range(0, len(changed), _CHUNK):
        batch = [{"repository_id": repository_id, **row} for row in changed[i : i + _CHUNK]]
        await session.execute(update(FunctionFact), batch)


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


async def tests_for_functions(
    session: AsyncSession,
    repository_id: str,
    function_ids: Iterable[str],
    k: int | None = None,
) -> dict[str, tuple[MappedTest, ...]]:
    """The stored nearest tests of each of *function_ids*, nearest first, at most *k*.

    An empty tuple is a mapped function no test reaches; an id absent from the
    result was never mapped (no row, or a run before the map existed).
    """
    ids = sorted(set(function_ids))
    out: dict[str, tuple[MappedTest, ...]] = {}
    for i in range(0, len(ids), _CHUNK):
        result = await session.execute(
            select(FunctionFact.symbol_id, FunctionFact.tests_json).where(
                FunctionFact.repository_id == repository_id,
                FunctionFact.symbol_id.in_(ids[i : i + _CHUNK]),
            )
        )
        out.update(_decoded(result.all(), k))
    return out


async def tests_in_files(
    session: AsyncSession, repository_id: str, paths: Iterable[str]
) -> dict[str, tuple[MappedTest, ...]]:
    """:func:`tests_for_functions` for every function in *paths*, in one statement."""
    files = sorted(set(paths))
    if not files:
        return {}
    result = await session.execute(
        select(FunctionFact.symbol_id, FunctionFact.tests_json)
        .join(
            GraphNode,
            and_(
                GraphNode.repository_id == FunctionFact.repository_id,
                GraphNode.node_id == FunctionFact.symbol_id,
            ),
        )
        .where(FunctionFact.repository_id == repository_id, GraphNode.file_path.in_(files))
    )
    return dict(_decoded(result.all(), None))


def _decoded(
    rows: Iterable[tuple[str, str | None]], k: int | None
) -> Iterator[tuple[str, tuple[MappedTest, ...]]]:
    for symbol, stored in rows:
        mapped = decode_tests(stored)
        if mapped is not None:
            yield symbol, mapped[:k] if k is not None else mapped


__all__ = [
    "delete_file_rows",
    "get_file_facts",
    "get_function_facts",
    "key_range",
    "tests_for_functions",
    "tests_in_files",
    "write_function_facts",
]
