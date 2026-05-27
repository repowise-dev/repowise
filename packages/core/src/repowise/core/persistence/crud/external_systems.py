"""CRUD operations for the external systems domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    ExternalSystem,
    GraphNode,
    WikiSymbol,
    _new_uuid,
    _now_utc,
)
from ._shared import _batch_upsert

# ---------------------------------------------------------------------------
# ExternalSystem CRUD
# ---------------------------------------------------------------------------


async def bulk_upsert_external_systems(
    session: AsyncSession,
    repository_id: str,
    systems: list[dict],
) -> dict[tuple[str, str], int]:
    """Upsert external systems for a repository.

    Each element of *systems* is a dict with keys matching ``ExternalSystem``
    columns (excluding ``id``, ``repository_id``, ``created_at``).

    Returns a mapping of ``(name, declared_in)`` → row ``id`` for both newly
    inserted and existing rows, so callers can link ``graph_nodes`` to the
    persisted external system without an extra round-trip.
    """
    id_map: dict[tuple[str, str], int] = {}
    for sys_data in systems:
        name = sys_data.get("name", "")
        declared_in = sys_data.get("declared_in", "")
        if not name:
            continue
        result = await session.execute(
            select(ExternalSystem).where(
                ExternalSystem.repository_id == repository_id,
                ExternalSystem.name == name,
                ExternalSystem.declared_in == declared_in,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            for key, val in sys_data.items():
                if key not in ("id", "repository_id", "created_at") and hasattr(existing, key):
                    setattr(existing, key, val)
            id_map[(name, declared_in)] = existing.id
        else:
            row = ExternalSystem(
                repository_id=repository_id,
                **{k: v for k, v in sys_data.items() if k not in ("id", "repository_id")},
            )
            session.add(row)
            await session.flush()
            id_map[(name, declared_in)] = row.id
    await session.flush()
    return id_map


async def link_graph_nodes_to_external_systems(
    session: AsyncSession,
    repository_id: str,
    name_to_id: dict[str, int],
) -> int:
    """Resolve ``external:{name}`` graph nodes to their ExternalSystem row.

    ``name_to_id`` should be a flat map of dep name → ExternalSystem id
    (collapse multi-manifest entries by picking any id — the C4 renderer
    only needs ``name``/``category`` which are the same across rows).

    Returns the number of graph_nodes updated.
    """
    if not name_to_id:
        return 0
    prefix = "external:"
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_id.like(f"{prefix}%"),
        )
    )
    updated = 0
    for node in result.scalars():
        suffix = node.node_id[len(prefix) :]
        # Try the full suffix first, then the first segment (handles e.g.
        # ``external:fastapi.responses`` → ``fastapi``).
        sys_id = name_to_id.get(suffix)
        if sys_id is None and "." in suffix:
            sys_id = name_to_id.get(suffix.split(".", 1)[0])
        if sys_id is None and "/" in suffix:
            sys_id = name_to_id.get(suffix.split("/", 1)[0])
        if sys_id is not None and node.external_system_id != sys_id:
            node.external_system_id = sys_id
            updated += 1
    await session.flush()
    return updated


async def list_external_systems(session: AsyncSession, repository_id: str) -> list[ExternalSystem]:
    """List all external systems for a repository, ordered by name."""
    result = await session.execute(
        select(ExternalSystem)
        .where(ExternalSystem.repository_id == repository_id)
        .order_by(ExternalSystem.name)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# WikiSymbol CRUD (batch)
# ---------------------------------------------------------------------------


def _symbol_id(sym: Any) -> str:
    return getattr(sym, "id", None) or f"{sym.file_path}::{sym.name}"


def _update_wiki_symbol(existing: WikiSymbol, sym: Any) -> None:
    existing.name = sym.name
    existing.qualified_name = getattr(sym, "qualified_name", sym.name)
    existing.kind = sym.kind
    existing.signature = getattr(sym, "signature", "")
    existing.start_line = getattr(sym, "start_line", 0)
    existing.end_line = getattr(sym, "end_line", 0)
    existing.docstring = getattr(sym, "docstring", None)
    existing.visibility = getattr(sym, "visibility", "public")
    existing.is_async = getattr(sym, "is_async", False)
    existing.complexity_estimate = getattr(sym, "complexity_estimate", 0)
    existing.language = getattr(sym, "language", "")
    existing.parent_name = getattr(sym, "parent_name", None)
    existing.updated_at = _now_utc()


async def batch_upsert_symbols(
    session: AsyncSession,
    repository_id: str,
    symbols: list,  # list[ingestion.models.Symbol]
) -> None:
    """Upsert ingestion Symbol objects into the wiki_symbols table.

    Accepts ingestion.models.Symbol dataclass instances (duck-typed).
    """
    await _batch_upsert(
        session,
        WikiSymbol,
        symbols,
        key_fn=lambda sym: (
            WikiSymbol.repository_id == repository_id,
            WikiSymbol.symbol_id == _symbol_id(sym),
        ),
        update_fn=_update_wiki_symbol,
        insert_fn=lambda sym: WikiSymbol(
            id=_new_uuid(),
            repository_id=repository_id,
            file_path=getattr(sym, "file_path", ""),
            symbol_id=_symbol_id(sym),
            name=sym.name,
            qualified_name=getattr(sym, "qualified_name", sym.name),
            kind=sym.kind,
            signature=getattr(sym, "signature", ""),
            start_line=getattr(sym, "start_line", 0),
            end_line=getattr(sym, "end_line", 0),
            docstring=getattr(sym, "docstring", None),
            visibility=getattr(sym, "visibility", "public"),
            is_async=getattr(sym, "is_async", False),
            complexity_estimate=getattr(sym, "complexity_estimate", 0),
            language=getattr(sym, "language", ""),
            parent_name=getattr(sym, "parent_name", None),
        ),
    )
