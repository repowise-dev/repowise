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

from ...models import FunctionFact

if TYPE_CHECKING:
    from ....analysis.execution_roles import ExecutionRoles

_CHUNK = 500


def _row(repository_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
    assigns = row.get("receiver_assigns")
    return {
        "repository_id": repository_id,
        "symbol_id": row["symbol_id"],
        "file_path": row["file_path"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "awaits": row.get("awaits"),
        "is_generator": row.get("is_generator"),
        "uses_receiver": row.get("uses_receiver"),
        "receiver_assigns_json": None if assigns is None else json.dumps(list(assigns)),
        "early_exits": row.get("early_exits"),
    }


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
    scope = FunctionFact.repository_id == repository_id
    if file_paths is None:
        await session.execute(delete(FunctionFact).where(scope))
    else:
        paths = sorted(set(file_paths))
        for i in range(0, len(paths), _CHUNK):
            await session.execute(
                delete(FunctionFact).where(scope, FunctionFact.file_path.in_(paths[i : i + _CHUNK]))
            )
    # Two walked functions can resolve to one symbol; the first keeps it.
    fresh = list({row["symbol_id"]: _row(repository_id, row) for row in reversed(list(rows))}.values())
    for i in range(0, len(fresh), _CHUNK):
        await session.execute(insert(FunctionFact), fresh[i : i + _CHUNK])
    if roles is not None:
        await _restamp_roles(session, repository_id, roles)
    return len(fresh)


async def _restamp_roles(session: AsyncSession, repository_id: str, roles: ExecutionRoles) -> None:
    stored = await session.execute(
        select(FunctionFact.symbol_id, FunctionFact.file_path, FunctionFact.execution_role).where(
            FunctionFact.repository_id == repository_id
        )
    )
    changed = [
        {"repository_id": repository_id, "symbol_id": symbol, "execution_role": role}
        for symbol, path, current in stored.all()
        if (role := roles.role_of(symbol, path)) != current
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


__all__ = ["get_function_facts", "write_function_facts"]
