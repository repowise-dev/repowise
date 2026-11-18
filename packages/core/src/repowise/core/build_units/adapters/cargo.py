"""Cargo crates from the resolver's :class:`CargoWorkspaceIndex`, plus every other ``[package]`` manifest.

The index is built from a root ``Cargo.toml`` and covers its workspace. A
crate it does not cover (a standalone crate, one in the workspace's
``exclude``, any crate in a repo with no root manifest) is found by the
package-root scan and named by its ``[package]``. Ceiling: such a crate's
path dependencies are not read, so it has no deps; reading them through the
resolver's ``_parse_deps`` is the upgrade path.
"""

from __future__ import annotations

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.package_roots import scan_package_manifests
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust_workspace import (
    CargoCrate,
    get_or_build_cargo_workspace_index,
)
from repowise.core.ingestion.workspace_members import read_toml_or_none


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


def _package_names(ctx: ResolverContext) -> dict[str, str | None]:
    """Each readable ``Cargo.toml``'s directory, mapped to its ``[package]`` name or None.

    None means the manifest parsed and declares no package (a virtual
    workspace). A manifest that does not parse is left out, so nothing here
    rules on it.
    """
    repo = ctx.repo_path
    # Without Rust sources there is no crate to own, and no reason to walk.
    if repo is None or not any(path.endswith(".rs") for path in ctx.path_set):
        return {}
    scanned = scan_package_manifests(repo, prune_nested_git=ctx.prune_nested_git)
    dirs = [root for root, names in scanned.items() if "Cargo.toml" in names]
    if (repo / "Cargo.toml").is_file():
        dirs.append("")  # the scan leaves the repo root out
    names: dict[str, str | None] = {}
    for root in sorted(dirs):
        data = read_toml_or_none(repo / root / "Cargo.toml")
        if data is None:
            continue
        package = data.get("package")
        name = package.get("name") if isinstance(package, dict) else None
        names[root] = name if isinstance(name, str) and name else None
    return names


def _unit(name: str, root: str, deps: set[str]) -> BuildUnit:
    return BuildUnit(
        id=f"cargo:{name}",
        ecosystem="cargo",
        name=name,
        root=root,
        manifest=_manifest(root),
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


def _manifest(root: str) -> str:
    return f"{root}/Cargo.toml" if root else "Cargo.toml"


def units(ctx: ResolverContext) -> Found:
    found = _indexed(ctx)
    covered = {unit.root for unit in found}
    package_names = _package_names(ctx)
    found += [
        _unit(name, root, set())
        for root, name in package_names.items()
        if name is not None and root not in covered
    ]
    virtual = frozenset(_manifest(root) for root, name in package_names.items() if name is None)
    return Found([(unit, ()) for unit in found], virtual)
