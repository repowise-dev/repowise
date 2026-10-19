"""External-systems refresh when a dependency manifest changed."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ._common import LogFn, _noop_log


async def refresh_external_systems(
    session: Any,
    repo_id: str,
    repo_path: Any,
    file_diffs: list,
    *,
    log: LogFn | None = None,
) -> bool:
    """Re-extract + reconcile external systems (C4 L1) when a manifest changed.

    The incremental path historically never refreshed the ``external_systems``
    table, so the C4 architecture panel served the init-time dependency list
    until the next full re-index. Extraction is a bounded repo walk (no LLM, no
    network), so it is gated on an actual dependency-manifest change in this
    update's diff — the common no-manifest update pays nothing.

    When a manifest did change, the *whole* repo is re-extracted: it's cheap,
    and only a complete set supports a clean reconcile that also drops deps
    removed from a manifest. The table is then replaced (removed deps pruned)
    and ``external:{name}`` graph nodes re-linked so a brand-new dependency's
    node isn't left with a NULL FK.

    Ceiling: re-extraction walks the repo (depth ≤ 4) rather than parsing only
    the changed manifests — fine because it runs only on a manifest change and
    a partial parse can't see cross-manifest removals. Returns ``True`` when a
    refresh actually ran.
    """
    log = log or _noop_log
    from repowise.core.ingestion.external_systems import is_manifest_path

    if not any(is_manifest_path(fd.path) for fd in file_diffs or []):
        return False

    from repowise.core.ingestion.external_systems import extract_external_systems

    records = await asyncio.to_thread(extract_external_systems, Path(repo_path))
    systems = [
        {
            "name": r.name,
            "display_name": r.display_name,
            "ecosystem": r.ecosystem,
            "category": r.category,
            "io_kind": r.io_kind,
            "version": r.version,
            "declared_in": r.declared_in,
            "is_dev_dep": r.is_dev_dep,
        }
        for r in records
    ]

    from repowise.core.persistence.crud import (
        build_external_system_link_map,
        link_graph_nodes_to_external_systems,
        replace_external_systems,
    )

    id_map = await replace_external_systems(session, repo_id, systems)
    name_to_id = build_external_system_link_map(systems, id_map)
    await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
    log(f"External systems refreshed: [cyan]{len(systems)}[/cyan] deps")
    return True
