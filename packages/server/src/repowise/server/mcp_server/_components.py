"""Component targets for get_context and the component list for get_overview.

Both are opt-in: a ``component:<name>`` target, or ``include=["components"]``.
Neither runs on a default call. A component card gains declared against
observed ownership only with ``include=["ownership"]``. The resolved
components are cached per repo, index state, catalog file times, workspace
graph and exclusion rules, so a batch of component targets and a later
overview resolve them once.
"""

from __future__ import annotations

import os
from typing import Any

from repowise.core.components.catalog import CATALOG_FILES, Component
from repowise.core.components.store import (
    RepoComponents,
    component_ownership,
    component_rollups,
    load_repo_components,
    services_from_system_graph,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import _is_workspace_mode, is_excluded
from repowise.server.mcp_server._index_state import index_state_key

COMPONENT_PREFIX = "component:"
_LIST_CAP = 25
_SAMPLE_FILES = 10
_FACTS = ("health", "hotspots", "bus_factor", "test_reach", "dead_code")

_CACHE: dict[tuple, RepoComponents] = {}
_CACHE_LIMIT = 16


def reset_cache() -> None:
    """Drop every cached resolution. For tests and an in-process re-index."""
    _CACHE.clear()


def is_component_target(target: str) -> bool:
    return target.startswith(COMPONENT_PREFIX)


def _catalog_times(repo_root: Any) -> tuple[int, ...]:
    times = []
    for rel in CATALOG_FILES:
        try:
            times.append(os.stat(os.path.join(str(repo_root), rel)).st_mtime_ns)
        except OSError:
            times.append(0)
    return tuple(times)


async def _load(
    session: Any, repository: Any, repo_root: Any, alias: str | None, exclude_spec: Any
) -> RepoComponents:
    graph = None
    enricher = _state._cross_repo_enricher
    if enricher is not None and _is_workspace_mode() and enricher.has_system_graph:
        graph = enricher.get_system_graph()
    key = (
        repository.id,
        index_state_key(repository),
        str(repo_root),
        _catalog_times(repo_root),
        alias,
        id(graph),
        id(exclude_spec),
    )
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    found = await load_repo_components(
        session,
        repository.id,
        repo_root,
        services=services_from_system_graph(graph, alias),
        is_excluded=(lambda p: is_excluded(p, exclude_spec)) if exclude_spec else None,
    )
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = found
    return found


def _describe(comp: Component) -> dict[str, Any]:
    """The component's identity and declared context; empty fields left out."""
    out: dict[str, Any] = {"name": comp.name}
    if comp.declared:
        out["source"] = "declared"
        out["declared_in"] = comp.declared_in
    else:
        out["source"] = "derived"
        out["derived_from"] = comp.source
    fields = {
        "title": comp.title,
        "description": comp.description,
        "component_type": comp.type,
        "lifecycle": comp.lifecycle,
        "owner": comp.owner,
        "system": comp.system,
        "criticality": comp.criticality,
        "tags": list(comp.tags),
        "capability": comp.capability,
        "paths": list(comp.paths) or ([f"{comp.root}/**"] if comp.root else []),
    }
    out.update((k, v) for k, v in fields.items() if v)
    return out


def _empty_note(comp: Component) -> str:
    if not comp.paths and not comp.root:
        return (
            "Declares no repowise.dev/paths and no derived component has this name, "
            "so it claims no files."
        )
    return "Its paths match no indexed file that an earlier component has not claimed."


async def component_cards(
    session: Any,
    repository: Any,
    targets: list[str],
    *,
    repo_root: Any,
    alias: str | None,
    exclude_spec: Any,
    ownership: bool = False,
) -> dict[str, dict[str, Any]]:
    """get_context cards for ``component:<name>`` *targets*: components resolved
    once and each fact table read once for the whole batch, in turn on *session*.
    *ownership* adds each card's declared against observed ownership block."""
    found = await _load(session, repository, repo_root, alias, exclude_spec)
    wanted = {t: found.resolved.get(t[len(COMPONENT_PREFIX) :].strip()) for t in targets}
    to_fold = {
        c.name: found.resolved.files[c.name]
        for c in wanted.values()
        if c is not None and found.resolved.files[c.name]
    }
    rollups = (
        await component_rollups(session, repository.id, to_fold, found.test_files)
        if to_fold
        else {}
    )
    owners: dict[str, dict[str, Any]] = {}
    directory_issues: list[str] = []
    if ownership:
        found_comps = {c.name: c for c in wanted.values() if c is not None}
        owners, directory_issues = await component_ownership(
            session,
            repository.id,
            repo_root,
            [(c, found.resolved.files[c.name]) for c in found_comps.values()],
            {n: r["bus_factor"] for n, r in rollups.items()},
        )
    cards: dict[str, dict[str, Any]] = {}
    for target, comp in wanted.items():
        name = target[len(COMPONENT_PREFIX) :].strip()
        if comp is None:
            cards[target] = {
                "target": target,
                "error": f"No component named {name!r}.",
                "available": [c.name for c in found.resolved.components[:_LIST_CAP]],
            }
            continue
        files = found.resolved.files[comp.name]
        card: dict[str, Any] = {"target": target, "type": "component", **_describe(comp)}
        card["file_count"] = len(files)
        if not files:
            card["note"] = _empty_note(comp)
            card["facts"] = {
                k: {"basis": "unavailable", "reason": "the component claims no files"}
                for k in _FACTS
            }
        else:
            facts = dict(rollups[comp.name])
            facts.pop("file_count")
            card["sample_files"] = files[:_SAMPLE_FILES]
            card["facts"] = facts
        if comp.name in owners:
            card["ownership"] = dict(owners[comp.name])
            if directory_issues:
                # Once per call, on the first card that carries ownership.
                card["ownership"]["directory_issues"] = directory_issues
                directory_issues = []
        cards[target] = card
    return cards


async def component_list(
    session: Any,
    repository: Any,
    *,
    repo_root: Any,
    alias: str | None,
    exclude_spec: Any,
    collector: OmissionCollector,
) -> dict[str, Any]:
    """The get_overview ``components`` block: declared first, then derived by size."""
    found = await _load(session, repository, repo_root, alias, exclude_spec)
    files = found.resolved.files
    declared = [c for c in found.resolved.components if c.declared]
    derived = sorted(
        (c for c in found.resolved.components if not c.declared),
        key=lambda c: (-len(files[c.name]), c.name),
    )
    rows = []
    for comp in [*declared, *derived]:
        row = _describe(comp)
        for key in ("description", "paths", "declared_in", "title", "capability"):
            row.pop(key, None)
        row["file_count"] = len(files[comp.name])
        rows.append(row)
    if len(rows) > _LIST_CAP:
        collector.add(
            f"components beyond cap={_LIST_CAP} ({len(rows) - _LIST_CAP} dropped)",
            "\n".join(f"{r['name']}: {r['file_count']} files" for r in rows[_LIST_CAP:]),
        )
    block: dict[str, Any] = {"items": rows[:_LIST_CAP], "total": len(rows)}
    if found.resolved.unassigned:
        block["unassigned_files"] = len(found.resolved.unassigned)
    errors = len(found.catalog.errors)
    if errors:
        block["catalog_errors"] = errors
    return block
