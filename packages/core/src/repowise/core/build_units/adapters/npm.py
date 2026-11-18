"""npm, yarn and pnpm workspace members from the TypeScript resolver's workspace reader."""

from __future__ import annotations

from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.ts_workspace import (
    _parsed_package_jsons,
    get_or_build_workspace_info,
)

_DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def units(ctx: ResolverContext) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    members = get_or_build_workspace_info(ctx)
    if not members:
        return []
    manifests = dict(_parsed_package_jsons(ctx))

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for name, info in members.items():
        member_dir: str = info["dir"]
        root = "" if member_dir == "." else member_dir
        data = manifests.get(member_dir) or {}
        deps = {
            f"npm:{dep}"
            for key in _DEP_FIELDS
            if isinstance(data.get(key), dict)
            for dep in data[key]
            if dep in members
        }
        unit = BuildUnit(
            id=f"npm:{name}",
            ecosystem="npm",
            name=name,
            root=root,
            manifest=f"{root}/package.json" if root else "package.json",
            source="npm-workspace",
            deps=tuple(deps),
        )
        out.append((unit, ()))
    return out
