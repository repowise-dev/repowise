"""Cargo crates from the resolver's :class:`CargoWorkspaceIndex`."""

from __future__ import annotations

from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust_workspace import (
    CargoCrate,
    get_or_build_cargo_workspace_index,
)


def _root(crate: CargoCrate) -> str:
    """The crate's directory: its ``src_dir`` minus the trailing ``src``."""
    return "" if crate.src_dir == "src" else crate.src_dir.removesuffix("/src")


def units(ctx: ResolverContext) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    index = get_or_build_cargo_workspace_index(ctx)
    if index is None:
        return []
    by_root = {_root(crate): crate.name for crate in index.crates}
    names = set(by_root.values())

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for crate in index.crates:
        root = _root(crate)
        deps: set[str] = set()
        for dep in crate.dependencies:
            if not dep.is_path:
                continue
            path = "" if dep.path == "." else dep.path
            # A ``{ workspace = true }`` dependency's path is resolved against
            # the member, not the workspace root, so it can miss; the package
            # name still names the sibling crate.
            target = by_root.get(path) if path is not None else None
            if target is None and dep.package in names:
                target = dep.package
            if target is not None:
                deps.add(f"cargo:{target}")
        unit = BuildUnit(
            id=f"cargo:{crate.name}",
            ecosystem="cargo",
            name=crate.name,
            root=root,
            manifest=f"{root}/Cargo.toml" if root else "Cargo.toml",
            source="cargo",
            deps=tuple(deps),
        )
        out.append((unit, ()))
    return out
