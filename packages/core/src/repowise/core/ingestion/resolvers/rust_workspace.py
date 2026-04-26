"""Cargo workspace index — maps sibling crate names to their src/ directories.

A Cargo workspace declares member crates in the root ``Cargo.toml``::

    [workspace]
    members = ["crates/foo", "crates/bar"]

Each member is a directory containing its own ``Cargo.toml`` with a
``[package] name = "foo-thing"`` entry. Inside any sibling crate, a
``use foo_thing::baz`` should resolve to ``crates/foo/src/lib.rs``-rooted
modules (Cargo replaces ``-`` with ``_`` for the import identifier).

The index is built lazily on first access via
``ResolverContext.cargo_workspace_index`` and cached on the context.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CargoCrate:
    """A workspace member crate."""

    name: str  # package name as it appears in Cargo.toml (may contain "-")
    src_dir: str  # repo-relative POSIX path to the crate's src/ directory


@dataclass(frozen=True)
class CargoWorkspaceIndex:
    """Cargo workspace member index. Map from crate-import-name → src dir."""

    crates: tuple[CargoCrate, ...]

    def lookup(self, import_prefix: str) -> str | None:
        """Find the src/ dir for a crate referenced as ``import_prefix::...``."""
        # Cargo replaces "-" with "_" for the Rust import identifier.
        for crate in self.crates:
            normalised = crate.name.replace("-", "_")
            if normalised == import_prefix:
                return crate.src_dir
        return None


def get_or_build_cargo_workspace_index(ctx) -> CargoWorkspaceIndex | None:
    """Lazily build (and cache) the Cargo workspace index for the current repo."""
    cached = getattr(ctx, "_cargo_workspace_index", "__unset__")
    if cached != "__unset__":
        return cached  # type: ignore[return-value]

    index = _build_cargo_workspace_index(ctx)
    setattr(ctx, "_cargo_workspace_index", index)
    return index


def _build_cargo_workspace_index(ctx) -> CargoWorkspaceIndex | None:
    repo_path = getattr(ctx, "repo_path", None)
    if not repo_path:
        return None

    root_toml = Path(repo_path) / "Cargo.toml"
    if not root_toml.exists():
        return None

    try:
        with open(root_toml, "rb") as f:
            root_data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    workspace = root_data.get("workspace") or {}
    members = workspace.get("members") or []
    if not isinstance(members, list):
        return None

    crates: list[CargoCrate] = []
    repo = Path(repo_path).resolve()

    # Single-crate repo with a [package] at the root: still index it.
    root_pkg = root_data.get("package") or {}
    if root_pkg.get("name"):
        crates.append(CargoCrate(name=str(root_pkg["name"]), src_dir="src"))

    for member in members:
        if not isinstance(member, str):
            continue
        member_path = (repo / member).resolve()
        try:
            member_rel = member_path.relative_to(repo).as_posix()
        except ValueError:
            continue
        member_toml = member_path / "Cargo.toml"
        if not member_toml.exists():
            continue
        try:
            with open(member_toml, "rb") as f:
                member_data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        pkg = member_data.get("package") or {}
        name = pkg.get("name")
        if not name:
            continue
        src_dir = f"{member_rel}/src" if member_rel else "src"
        crates.append(CargoCrate(name=str(name), src_dir=src_dir))

    if not crates:
        return None
    log.debug("Built Cargo workspace index", crate_count=len(crates))
    return CargoWorkspaceIndex(crates=tuple(crates))
