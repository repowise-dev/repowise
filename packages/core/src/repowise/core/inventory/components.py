"""Component inventory: each component with its owners, languages, entry points
and the third-party packages its files import.

A plain document built from the index (components from :mod:`repowise.core.components`,
declared teams through the ``team-directory`` seam, packages from
:mod:`.usage`). It is what a software inventory review asks for (PCI DSS 6.3.2
names one): what the code is made of and who answers for each part.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.external_systems import package_key
from repowise.core.inventory.usage import load_usage_index, lookup_key

INVENTORY_VERSION = 1


def _key(decl: Any) -> str:
    """One key per package however its manifests spell it."""
    return package_key(decl.ecosystem, lookup_key(decl.ecosystem, decl.name))


async def component_inventory(
    session: Any, repository_id: str, repo_root: Path | str
) -> dict[str, Any]:
    """The inventory document for one indexed repository."""
    from repowise.core.components.ownership import Activity, assess_component
    from repowise.core.components.store import load_repo_components, workspace_services
    from repowise.core.ownership.directory import team_directories
    from repowise.core.persistence.models import GraphNode

    root = Path(repo_root)
    found = await load_repo_components(session, repository_id, root, services=workspace_services(root))
    declared = team_directories.get().load(root)
    nodes = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.language, GraphNode.is_entry_point).where(
                GraphNode.repository_id == repository_id, GraphNode.node_type == "file"
            )
        )
    ).all()
    language = {n: lang for n, lang, _e in nodes}
    entries = {n for n, _l, e in nodes if e}

    index = await load_usage_index(session, repository_id)
    decl_by_id = {d.id: d for d in index.declarations}
    packages_of: dict[str, set[str]] = {}
    for src, dst, _names in index.edges:
        decl = decl_by_id.get(index.nodes.get(dst))  # type: ignore[arg-type]
        if decl is not None:
            packages_of.setdefault(src, set()).add(_key(decl))

    components = []
    imported: set[str] = set()
    for comp in found.resolved.components:
        files = found.resolved.files.get(comp.name, [])
        prod = [f for f in files if f not in found.test_files]
        owners = assess_component(comp, files, declared, Activity())
        pkg_files: Counter[str] = Counter(k for f in prod for k in packages_of.get(f, ()))
        test_only = {k for f in files if f in found.test_files for k in packages_of.get(f, ())}
        test_only -= set(pkg_files)
        imported |= set(pkg_files) | test_only
        row: dict[str, Any] = {
            "name": comp.name,
            "source": comp.source,
            "file_count": len(files),
            "test_file_count": len(files) - len(prod),
            "owners": {
                "teams": [
                    {"team": t["team"], "files": t["files"]}
                    for t in owners.get("declared_teams", [])
                ],
                "files_with_declared_owner": owners.get("files_with_declared_owner", 0),
            },
            "languages": dict(Counter(language[f] for f in files if language.get(f)).most_common()),
            "entry_points": sorted(f for f in files if f in entries),
            "packages": [
                {"package_key": k, "files": n}
                for k, n in sorted(pkg_files.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        }
        if test_only:
            row["test_only_packages"] = sorted(test_only)
        for key in ("criticality", "system", "lifecycle"):
            if getattr(comp, key):
                row[key] = getattr(comp, key)
        components.append(row)

    declared_keys = {_key(d) for d in index.declarations}
    return {
        "kind": "repowise.inventory",
        "version": INVENTORY_VERSION,
        "components": components,
        "unassigned_files": len(found.resolved.unassigned),
        "packages": {
            "declared": len(declared_keys),
            "imported": len(imported),
            "declared_not_imported": sorted(declared_keys - imported),
        },
        "catalog_sources": list(found.catalog.sources),
        "directory_issues": list(declared.issues),
        "method": {
            "components": "declared in the catalog, else derived from the index",
            "owners": "declared teams from the catalog spec.owner and CODEOWNERS",
            "languages": "file count per language of the indexed files",
            "entry_points": "files the index marks as entry points",
            "packages": "manifest-declared packages the component's production files import, "
            "with the number of importing files; test_only_packages are imported by tests only",
            "declared_not_imported": "declared packages no import edge links to; the import "
            "name may differ from the package name, so this is not proof of non-use",
        },
    }


__all__ = ["INVENTORY_VERSION", "component_inventory"]
