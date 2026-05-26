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
class CargoDep:
    """A dependency declared in Cargo.toml."""

    name: str  # import name (may differ from package name)
    package: str  # actual crate name on crates.io
    is_path: bool  # True if path dependency
    path: str | None  # resolved repo-relative path for path deps


@dataclass(frozen=True)
class CargoCrate:
    """A workspace member crate."""

    name: str  # package name as it appears in Cargo.toml (may contain "-")
    src_dir: str  # repo-relative POSIX path to the crate's src/ directory
    dependencies: tuple[CargoDep, ...] = ()


@dataclass(frozen=True)
class CargoWorkspaceIndex:
    """Cargo workspace member index. Map from crate-import-name → src dir."""

    crates: tuple[CargoCrate, ...]
    workspace_dependencies: tuple[CargoDep, ...] = ()

    def lookup(self, import_prefix: str) -> str | None:
        """Find the src/ dir for a crate referenced as ``import_prefix::...``."""
        # Cargo replaces "-" with "_" for the Rust import identifier.
        for crate in self.crates:
            normalised = crate.name.replace("-", "_")
            if normalised == import_prefix:
                return crate.src_dir
        return None

    def lookup_crate_for_file(self, file_path: str) -> CargoCrate | None:
        """Find the crate owning a given file path (longest prefix match)."""
        best: CargoCrate | None = None
        best_len = -1
        for crate in self.crates:
            # crate.src_dir is like "crates/foo/src"; check the parent dir
            crate_prefix = crate.src_dir.rsplit("/src", 1)[0] + "/"
            if file_path.startswith(crate_prefix) and len(crate_prefix) > best_len:
                best = crate
                best_len = len(crate_prefix)
        return best


def get_or_build_cargo_workspace_index(ctx) -> CargoWorkspaceIndex | None:
    """Lazily build (and cache) the Cargo workspace index for the current repo."""
    cached = getattr(ctx, "_cargo_workspace_index", "__unset__")
    if cached != "__unset__":
        return cached  # type: ignore[return-value]

    index = _build_cargo_workspace_index(ctx)
    setattr(ctx, "_cargo_workspace_index", index)
    return index


def _parse_deps(
    raw: dict, crate_dir: Path, repo: Path
) -> tuple[CargoDep, ...]:
    """Parse a ``[dependencies]`` / ``[dev-dependencies]`` table into ``CargoDep`` tuples."""
    deps: list[CargoDep] = []
    for name, spec in raw.items():
        if isinstance(spec, str):
            deps.append(CargoDep(name=name, package=name, is_path=False, path=None))
        elif isinstance(spec, dict):
            package = spec.get("package", name)
            path_str = spec.get("path")
            resolved_path: str | None = None
            if path_str:
                abs_path = (crate_dir / path_str).resolve()
                try:
                    resolved_path = abs_path.relative_to(repo).as_posix()
                except ValueError:
                    resolved_path = None
            deps.append(
                CargoDep(
                    name=name,
                    package=package,
                    is_path=path_str is not None,
                    path=resolved_path,
                )
            )
    return tuple(deps)


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
        root_deps = _parse_deps(
            {**root_data.get("dependencies", {}),
             **root_data.get("dev-dependencies", {}),
             **root_data.get("build-dependencies", {})},
            Path(repo_path),
            repo,
        )
        crates.append(CargoCrate(name=str(root_pkg["name"]), src_dir="src", dependencies=root_deps))

    # Parse workspace-level shared dependencies
    ws_deps = _parse_deps(workspace.get("dependencies", {}), Path(repo_path), repo)

    # Parse exclude patterns
    exclude_patterns = workspace.get("exclude", [])
    excluded_paths: set[Path] = set()
    for pattern in exclude_patterns:
        if isinstance(pattern, str):
            excluded_paths.update(p.resolve() for p in repo.glob(pattern))

    for member_pattern in members:
        if not isinstance(member_pattern, str):
            continue
        matched_paths = sorted(repo.glob(member_pattern))
        if not matched_paths:
            # Fallback to literal path for backward compat
            matched_paths = [(repo / member_pattern).resolve()]
        for member_path in matched_paths:
            member_path = member_path.resolve()
            if not member_path.is_dir():
                continue
            if member_path in excluded_paths:
                continue
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
            member_deps = _parse_deps(
                {**member_data.get("dependencies", {}),
                 **member_data.get("dev-dependencies", {}),
                 **member_data.get("build-dependencies", {})},
                member_path,
                repo,
            )
            crates.append(CargoCrate(name=str(name), src_dir=src_dir, dependencies=member_deps))

    if not crates:
        return None
    log.debug("Built Cargo workspace index", crate_count=len(crates))
    return CargoWorkspaceIndex(crates=tuple(crates), workspace_dependencies=ws_deps)
