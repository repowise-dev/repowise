"""CRUD operations for the external systems domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...analysis.external_systems.links import (
    EXTERNAL_NODE_PREFIX,
    declaration_name_candidates,
)
from ..models import (
    ExternalSystem,
    GraphNode,
    WikiSymbol,
    _new_uuid,
    _now_utc,
)
from ._shared import _batch_upsert_keyed

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


async def replace_external_systems(
    session: AsyncSession,
    repository_id: str,
    systems: list[dict],
) -> dict[tuple[str, str], int]:
    """Reconcile a repository's external systems to exactly *systems*.

    Like :func:`bulk_upsert_external_systems`, but also DELETES rows whose
    ``(name, declared_in)`` key is absent from *systems* — so a dependency
    dropped from a manifest stops being served (the plain upsert leaks it
    forever). The ``graph_nodes.external_system_id`` FK is ``ON DELETE SET
    NULL``, so a deleted row's links clear automatically; callers re-link from
    the returned id_map. Returns ``(name, declared_in)`` → row ``id`` for the
    surviving set (same shape as :func:`bulk_upsert_external_systems`).

    Intended for the incremental update path, which re-extracts the whole repo
    (a complete set) whenever a manifest changed. The full-init path stays on
    the pure upsert — it delete-then-inserts elsewhere.
    """
    fresh_keys = {(s.get("name", ""), s.get("declared_in", "")) for s in systems if s.get("name")}
    result = await session.execute(
        select(ExternalSystem).where(ExternalSystem.repository_id == repository_id)
    )
    for row in result.scalars():
        if (row.name, row.declared_in) not in fresh_keys:
            await session.delete(row)
    await session.flush()
    return await bulk_upsert_external_systems(session, repository_id, systems)


async def link_graph_nodes_to_external_systems(
    session: AsyncSession,
    repository_id: str,
    name_to_id: dict[str, int | None],
) -> int:
    """Resolve ``external:{name}`` graph nodes to their ExternalSystem row.

    Bare names shared by multiple ecosystems map to ``None`` so ambiguous
    targets remain unlinked; ecosystem-qualified keys remain resolvable.

    Returns the number of graph_nodes updated.
    """
    if not name_to_id:
        return 0

    prefix = EXTERNAL_NODE_PREFIX
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_id.like(f"{prefix}%"),
        )
    )
    updated = 0
    for node in result.scalars():
        suffix = node.node_id[len(prefix) :]
        sys_id = next(
            (
                name_to_id[name]
                for name in declaration_name_candidates(suffix)
                if name in name_to_id
            ),
            None,
        )
        if node.external_system_id != sys_id:
            node.external_system_id = sys_id
            updated += 1
    await session.flush()
    return updated


def build_external_system_link_map(
    systems: list[dict],
    id_map: dict[tuple[str, str], int],
) -> dict[str, int | None]:
    """Build exact/prefixed link keys while preserving name ambiguity.

    The ambiguity rule itself lives in ``analysis.external_systems.links`` and
    is shared with the plain-record path, so the stored ``external_system_id``
    and a link built without a database cannot disagree about what resolves.
    Declarations whose row was not persisted are dropped before indexing: they
    have no id to resolve to, and letting one poison a bare name would refuse a
    target the old map linked.
    """
    from repowise.core.analysis.external_systems.links import build_declaration_index

    persisted = [
        system
        for system in systems
        if system.get("name")
        and id_map.get((system.get("name", ""), system.get("declared_in", ""))) is not None
    ]
    # First row wins per identity, matching the insertion order the map is built in.
    first_id: dict[tuple[str, str], int] = {}
    for system in persisted:
        identity = (system.get("ecosystem", ""), system.get("name", ""))
        first_id.setdefault(
            identity, id_map[(system.get("name", ""), system.get("declared_in", ""))]
        )
    return {
        key: (None if identity is None else first_id.get(identity))
        for key, identity in build_declaration_index(persisted).items()
    }


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


def _new_wiki_symbol(repository_id: str, sym: Any) -> WikiSymbol:
    """Build a WikiSymbol row from an ingestion Symbol (shared insert recipe)."""
    return WikiSymbol(
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
    )


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
    await _batch_upsert_keyed(
        session,
        WikiSymbol,
        symbols,
        prefilter=(WikiSymbol.repository_id == repository_id,),
        item_key_fn=_symbol_id,
        row_key_fn=lambda row: row.symbol_id,
        update_fn=_update_wiki_symbol,
        insert_fn=lambda sym: _new_wiki_symbol(repository_id, sym),
    )


# Chunk size for the scoped existence SELECT — stays under SQLite's
# host-parameter limit when a wide catch-up update touches many files.
_SYMBOL_RECONCILE_CHUNK = 400


async def reconcile_symbols_for_files(
    session: AsyncSession,
    repository_id: str,
    file_paths: list[str],
    symbols: list,  # fresh parse of exactly those files
) -> int:
    """Make ``wiki_symbols`` for *file_paths* match a fresh parse (*symbols*).

    The incremental update path re-parses changed files but historically never
    persisted their symbols, so bounds fossilized at the last full index and
    the get_answer hydrator served drifted signatures. The repo-wide
    :func:`batch_upsert_symbols` reloads *every* symbol row for the repo, so
    calling it per update would SELECT the whole table. This scopes the
    existence query to the changed *file_paths*, upserts their symbols, and
    prunes rows for symbols that vanished from a still-existing file (a symbol
    deleted/renamed inside a file a pure upsert would otherwise leave behind as
    a stale row). Whole-file deletions are handled separately by the stale-row
    pruner; this only touches the files it was handed.

    Returns the number of pruned rows.
    """
    scoped = [p for p in dict.fromkeys(file_paths) if p]
    if not scoped:
        return 0

    existing_rows: list[WikiSymbol] = []
    for i in range(0, len(scoped), _SYMBOL_RECONCILE_CHUNK):
        chunk = scoped[i : i + _SYMBOL_RECONCILE_CHUNK]
        rows = (
            (
                await session.execute(
                    select(WikiSymbol).where(
                        WikiSymbol.repository_id == repository_id,
                        WikiSymbol.file_path.in_(chunk),
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_rows.extend(rows)
    by_id: dict[str, WikiSymbol] = {row.symbol_id: row for row in existing_rows}

    fresh_ids: set[str] = set()
    for sym in symbols:
        sid = _symbol_id(sym)
        # Within-batch duplicate ids: first inserts, later ones update it.
        fresh_ids.add(sid)
        existing = by_id.get(sid)
        if existing is not None:
            _update_wiki_symbol(existing, sym)
        else:
            obj = _new_wiki_symbol(repository_id, sym)
            session.add(obj)
            by_id[sid] = obj

    pruned = 0
    for sid, row in list(by_id.items()):
        if sid not in fresh_ids:
            await session.delete(row)
            pruned += 1
    await session.flush()
    return pruned
