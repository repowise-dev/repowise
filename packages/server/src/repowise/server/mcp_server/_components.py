"""Component targets for get_context and the component list for get_overview.

Both are opt-in: a ``component:<name>`` target, or ``include=["components"]``.
Neither runs on a default call.
"""

from __future__ import annotations

from typing import Any

from repowise.core.components.catalog import Component
from repowise.core.components.store import (
    RepoComponents,
    component_rollup,
    load_repo_components,
    services_from_system_graph,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import _is_workspace_mode, is_excluded

COMPONENT_PREFIX = "component:"
_LIST_CAP = 25
_SAMPLE_FILES = 10
_HINT = (
    'get_context(targets=["component:<name>"]) rolls one up: health, hotspots, '
    "bus factor, test reach, dead code. Declare components in "
    ".repowise/components.yaml or catalog-info.yaml; `repowise components check` "
    "validates them."
)


def is_component_target(target: str) -> bool:
    return target.startswith(COMPONENT_PREFIX)


async def _load(
    session: Any, repository: Any, repo_root: Any, alias: str | None, exclude_spec: Any
) -> RepoComponents:
    services: list[tuple[str, str]] = []
    enricher = _state._cross_repo_enricher
    if enricher is not None and _is_workspace_mode() and enricher.has_system_graph:
        services = services_from_system_graph(enricher.get_system_graph(), alias)
    return await load_repo_components(
        session,
        repository.id,
        repo_root,
        services=services,
        is_excluded=(lambda p: is_excluded(p, exclude_spec)) if exclude_spec else None,
    )


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


async def component_card(
    session: Any,
    repository: Any,
    target: str,
    *,
    repo_root: Any,
    alias: str | None,
    exclude_spec: Any,
) -> dict[str, Any]:
    """The get_context card for a ``component:<name>`` target."""
    name = target[len(COMPONENT_PREFIX) :].strip()
    found = await _load(session, repository, repo_root, alias, exclude_spec)
    comp = found.resolved.get(name)
    if comp is None:
        return {
            "target": target,
            "error": f"No component named {name!r}.",
            "available": [c.name for c in found.resolved.components[:_LIST_CAP]],
        }
    files = found.resolved.files[comp.name]
    facts = await component_rollup(session, repository.id, files, found.test_files)
    card: dict[str, Any] = {"target": target, "type": "component", **_describe(comp)}
    card["file_count"] = facts.pop("file_count")
    card["sample_files"] = files[:_SAMPLE_FILES]
    card["facts"] = facts
    return card


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
    block["hint"] = _HINT
    return block
