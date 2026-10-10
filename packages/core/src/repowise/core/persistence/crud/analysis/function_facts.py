"""The per-function store: one row per function symbol, its role and body facts.

Facts are pure on a file's bytes, so a run rewrites the rows of the files it
walked and leaves the rest. A role is not: a seed or a call in another file
moves it, so every run restamps every row's role from its own role map.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.execution_graph import file_of_symbol
from ...models import FunctionFact

if TYPE_CHECKING:
    from ....analysis.execution_roles import ExecutionRoles

_CHUNK = 500


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
    }


def file_rows(repository_id: str, path: str) -> Any:
    """A file's rows as a key range: every symbol id there is ``path::...``.

    Symbol ids carry forward slashes; ``:;`` is the first string after every
    ``path::`` suffix in bytewise order, which the column's ``C`` collation
    gives PostgreSQL too.
    """
    path = path.replace("\\", "/")
    return (
        FunctionFact.repository_id == repository_id,
        FunctionFact.symbol_id >= f"{path}::",
        FunctionFact.symbol_id < f"{path}:;",
    )


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
        for path in sorted(set(file_paths)):
            await session.execute(delete(FunctionFact).where(*file_rows(repository_id, path)))
    # Two walked functions can resolve to one symbol; the first keeps it. Key
    # order fills the clustered pages instead of splitting them half empty.
    by_symbol = {row["symbol_id"]: _row(repository_id, row) for row in reversed(list(rows))}
    fresh = [by_symbol[symbol] for symbol in sorted(by_symbol)]
    for i in range(0, len(fresh), _CHUNK):
        await session.execute(insert(FunctionFact), fresh[i : i + _CHUNK])
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


__all__ = ["file_rows", "get_function_facts", "write_function_facts"]
