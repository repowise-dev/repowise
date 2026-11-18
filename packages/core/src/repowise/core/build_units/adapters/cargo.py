"""Cargo crates from the resolver's :class:`CargoWorkspaceIndex`, plus every other ``[package]`` manifest.

The index is built from a root ``Cargo.toml`` and covers its workspace. A
crate it does not cover (a standalone crate, one in the workspace's
``exclude``, any crate in a repo with no root manifest) is found by the
package-root scan and named by its ``[package]``. Ceiling: such a crate's
path dependencies are not read, so it has no deps; reading them through the
resolver's ``_parse_deps`` is the upgrade path.
"""

from __future__ import annotations

from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.package_roots import scan_package_manifests
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust_workspace import (
    CargoCrate,
    get_or_build_cargo_workspace_index,
)
from repowise.core.ingestion.workspace_members import manifest_package_name

_ROOTS_KEY = "_build_units_cargo_roots"


def _root(crate: CargoCrate) -> str:
    """The crate's directory: its ``src_dir`` minus the trailing ``src``."""
    return "" if crate.src_dir == "src" else crate.src_dir.removesuffix("/src")


def _norm(path: str | None) -> str | None:
    return "" if path == "." else path


def _dep_ids(crate: CargoCrate, by_root: dict[str, str], ws_paths: dict[str, str]) -> set[str]:
    """``cargo:<crate>`` for each path dependency that lands on an in-repo crate.

    A ``{ workspace = true }`` dependency's path is resolved against the
    member, not the workspace root, so it misses; the root's own
    ``[workspace.dependencies]`` entry of that name holds the right path. A
    path outside the repo (None) links nothing, whatever its name.
    """
    ids: set[str] = set()
    for dep in crate.dependencies:
        path = _norm(dep.path) if dep.is_path else None
        if path is None:
            continue
        target = by_root.get(path)
        if target is None and dep.name in ws_paths:
            target = by_root.get(ws_paths[dep.name])
        if target is not None:
            ids.add(f"cargo:{target}")
    return ids


def _manifest_roots(ctx: ResolverContext) -> dict[str, str | None]:
    """Each directory holding a ``Cargo.toml``, mapped to its ``[package]`` name or None."""
    cached = getattr(ctx, _ROOTS_KEY, None)
    if cached is not None:
        return cached
    repo = ctx.repo_path
    roots: dict[str, str | None] = {}
    # Without Rust sources there is no crate to own, and no reason to walk.
    if repo is not None and any(path.endswith(".rs") for path in ctx.path_set):
        scanned = scan_package_manifests(repo, prune_nested_git=ctx.prune_nested_git)
        dirs = [root for root, names in scanned.items() if "Cargo.toml" in names]
        if (repo / "Cargo.toml").is_file():
            dirs.append("")  # the scan leaves the repo root out
        roots = {d: manifest_package_name(repo / d / "Cargo.toml") for d in sorted(dirs)}
    setattr(ctx, _ROOTS_KEY, roots)
    return roots


def virtual_roots(ctx: ResolverContext) -> set[str]:
    """Directories whose ``Cargo.toml`` declares no ``[package]``: a virtual workspace, no unit."""
    return {root for root, name in _manifest_roots(ctx).items() if name is None}


def _unit(name: str, root: str, deps: set[str]) -> BuildUnit:
    return BuildUnit(
        id=f"cargo:{name}",
        ecosystem="cargo",
        name=name,
        root=root,
        manifest=f"{root}/Cargo.toml" if root else "Cargo.toml",
        source="cargo",
        deps=tuple(deps),
    )


def _indexed(ctx: ResolverContext) -> list[BuildUnit]:
    index = get_or_build_cargo_workspace_index(ctx)
    if index is None:
        return []
    by_root = {_root(crate): crate.name for crate in index.crates}
    ws_paths = {
        dep.name: path
        for dep in index.workspace_dependencies
        if dep.is_path and (path := _norm(dep.path)) is not None
    }

    return [
        _unit(crate.name, _root(crate), _dep_ids(crate, by_root, ws_paths)) for crate in index.crates
    ]


def units(ctx: ResolverContext) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    found = _indexed(ctx)
    covered = {unit.root for unit in found}
    found += [
        _unit(name, root, set())
        for root, name in _manifest_roots(ctx).items()
        if name is not None and root not in covered
    ]
    return [(unit, ()) for unit in found]
