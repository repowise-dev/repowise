"""Cargo crates from the resolver's :class:`CargoWorkspaceIndex`, plus every other ``[package]`` manifest.

The index is built from a root ``Cargo.toml`` and covers its workspace. A
crate it does not cover (a standalone crate, one in the workspace's
``exclude``, any crate in a repo with no root manifest) is found by the
package-root scan, named by its ``[package]``, and its dependency tables are
read with the resolver's own parser, against the crate's directory.

A crate name taken twice keeps the first root in sorted order. The later
root is left to the manifest fallback, and a path dependency pointing at
it links that ``manifest:`` unit rather than the crate that kept the name.
A path outside the repo links nothing.
"""

from __future__ import annotations

import structlog

from repowise.core.build_units.adapters import Found, linked_unit_id
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.package_roots import scan_package_manifests
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust_workspace import (
    CargoCrate,
    _parse_deps,
    get_or_build_cargo_workspace_index,
)
from repowise.core.ingestion.workspace_members import read_toml_or_none

log = structlog.get_logger(__name__)


def _root(crate: CargoCrate) -> str:
    """The crate's directory: its ``src_dir`` minus the trailing ``src``."""
    return "" if crate.src_dir == "src" else crate.src_dir.removesuffix("/src")


def _norm(path: str | None) -> str | None:
    return "" if path == "." else path


def _dep_ids(
    crate: CargoCrate,
    by_root: dict[str, str],
    ws_paths: dict[str, str],
    dropped: set[str],
) -> set[str]:
    """The unit id for each path dependency that lands on an in-repo crate.

    A ``{ workspace = true }`` dependency's path is resolved against the
    member, not the workspace root, so it misses; the root's own
    ``[workspace.dependencies]`` entry of that name holds the right path. A
    path outside the repo (None) links nothing, whatever its name. A path at
    a root that lost its name links that root's ``manifest:`` unit.
    """
    ids: set[str] = set()
    for dep in crate.dependencies:
        path = _norm(dep.path) if dep.is_path else None
        if path is None:
            continue
        target = linked_unit_id(path, by_root, dropped)
        if target is None and dep.name in ws_paths:
            target = linked_unit_id(ws_paths[dep.name], by_root, dropped)
        if target is not None:
            ids.add(target)
    return ids


def _manifests(ctx: ResolverContext) -> dict[str, dict]:
    """Each readable ``Cargo.toml``'s directory, mapped to its parsed table.

    A manifest that does not parse is left out, so nothing here rules on it.
    """
    repo = ctx.repo_path
    # Without Rust sources there is no crate to own, and no reason to walk.
    if repo is None or not any(path.endswith(".rs") for path in ctx.path_set):
        return {}
    scanned = scan_package_manifests(repo, prune_nested_git=ctx.prune_nested_git)
    dirs = [root for root, names in scanned.items() if "Cargo.toml" in names]
    if (repo / "Cargo.toml").is_file():
        dirs.append("")  # the scan leaves the repo root out
    tables = {root: read_toml_or_none(repo / root / "Cargo.toml") for root in sorted(dirs)}
    return {root: data for root, data in tables.items() if data is not None}


def _package_name(data: dict) -> str | None:
    """The ``[package]`` name, or None for a manifest declaring no package (a virtual workspace)."""
    package = data.get("package")
    name = package.get("name") if isinstance(package, dict) else None
    return name if isinstance(name, str) and name else None


def _crate(ctx: ResolverContext, root: str, name: str, data: dict) -> CargoCrate:
    """A crate the index does not cover, with the path deps its own tables declare."""
    repo = ctx.repo_path.resolve()
    workspace = data.get("workspace")
    ws_deps = workspace.get("dependencies") if isinstance(workspace, dict) else None
    tables = ("dependencies", "dev-dependencies", "build-dependencies")
    raw = {k: v for table in tables if isinstance(data.get(table), dict) for k, v in data[table].items()}
    return CargoCrate(
        name=name,
        src_dir=f"{root}/src" if root else "src",
        dependencies=_parse_deps(raw, repo / root, repo, ws_deps=ws_deps if isinstance(ws_deps, dict) else None),
    )


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


def _manifest(root: str) -> str:
    return f"{root}/Cargo.toml" if root else "Cargo.toml"


def units(ctx: ResolverContext) -> Found:
    index = get_or_build_cargo_workspace_index(ctx)
    crates = list(index.crates) if index is not None else []
    ws_deps = index.workspace_dependencies if index is not None else ()
    covered = {_root(crate) for crate in crates}
    manifests = _manifests(ctx)
    for root, data in manifests.items():
        name = _package_name(data)
        if name is not None and root not in covered:
            crates.append(_crate(ctx, root, name, data))

    # A name taken twice keeps its first root in sorted order. The later root
    # is not claimed, so the fallback gives it a manifest unit and a path
    # dependency on it does not resolve to the crate that kept the name.
    by_root: dict[str, str] = {}
    dropped: set[str] = set()
    taken: set[str] = set()
    kept: list[CargoCrate] = []
    for crate in sorted(crates, key=_root):
        root = _root(crate)
        uid = f"cargo:{crate.name}"
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=root)
            dropped.add(root)
            continue
        taken.add(uid)
        by_root[root] = uid
        kept.append(crate)
    ws_paths = {
        dep.name: path for dep in ws_deps if dep.is_path and (path := _norm(dep.path)) is not None
    }
    claims = [
        (_unit(crate.name, _root(crate), _dep_ids(crate, by_root, ws_paths, dropped)), ())
        for crate in kept
    ]
    virtual = frozenset(_manifest(root) for root, data in manifests.items() if _package_name(data) is None)
    return Found(claims, virtual)
