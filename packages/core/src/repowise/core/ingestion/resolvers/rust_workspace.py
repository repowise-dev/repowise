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
    is_proc_macro: bool = False  # True if [lib] proc-macro = true
    bin_paths: tuple[str, ...] = ()  # repo-relative POSIX paths to [[bin]] entry points


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

    def is_file_in_proc_macro_crate(self, file_path: str) -> bool:
        """Check if a file belongs to a proc-macro crate."""
        crate = self.lookup_crate_for_file(file_path)
        return crate is not None and crate.is_proc_macro


def get_or_build_cargo_workspace_index(ctx) -> CargoWorkspaceIndex | None:
    """Lazily build (and cache) the Cargo workspace index for the current repo."""
    cached = getattr(ctx, "_cargo_workspace_index", "__unset__")
    if cached != "__unset__":
        return cached  # type: ignore[return-value]

    index = _build_cargo_workspace_index(ctx)
    setattr(ctx, "_cargo_workspace_index", index)
    return index


def _parse_deps(
    raw: dict,
    crate_dir: Path,
    repo: Path,
    ws_deps: dict | None = None,
) -> tuple[CargoDep, ...]:
    """Parse a ``[dependencies]`` / ``[dev-dependencies]`` table into ``CargoDep`` tuples.

    When *ws_deps* is provided (the raw ``[workspace.dependencies]`` table),
    entries with ``workspace = true`` inherit their spec from it (Cargo 1.64+).
    """
    deps: list[CargoDep] = []
    for name, spec in raw.items():
        if isinstance(spec, str):
            deps.append(CargoDep(name=name, package=name, is_path=False, path=None))
        elif isinstance(spec, dict):
            # Workspace inheritance: { workspace = true } pulls from
            # [workspace.dependencies] in the root Cargo.toml.
            if spec.get("workspace") and ws_deps is not None:
                ws_spec = ws_deps.get(name, {})
                if isinstance(ws_spec, str):
                    deps.append(CargoDep(name=name, package=name, is_path=False, path=None))
                    continue
                elif isinstance(ws_spec, dict):
                    spec = {**ws_spec, **{k: v for k, v in spec.items() if k != "workspace"}}
                else:
                    continue

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


def _parse_bin_targets(cargo_data: dict, member_rel: str, repo: Path) -> tuple[str, ...]:
    """Extract ``[[bin]]`` entry-point paths from Cargo.toml data."""
    bins = cargo_data.get("bin", [])
    if not isinstance(bins, list):
        return ()
    paths: list[str] = []
    for entry in bins:
        if not isinstance(entry, dict):
            continue
        path_str = entry.get("path")
        if path_str:
            rel = f"{member_rel}/{path_str}" if member_rel else path_str
            paths.append(Path(rel).as_posix())
    return tuple(paths)


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
    # Raw workspace deps table for { workspace = true } inheritance
    ws_deps_raw = workspace.get("dependencies", {})

    root_pkg = root_data.get("package") or {}
    if root_pkg.get("name"):
        root_deps = _parse_deps(
            {**root_data.get("dependencies", {}),
             **root_data.get("dev-dependencies", {}),
             **root_data.get("build-dependencies", {})},
            Path(repo_path),
            repo,
            ws_deps=ws_deps_raw,
        )
        root_bins = _parse_bin_targets(root_data, "", repo)
        crates.append(CargoCrate(
            name=str(root_pkg["name"]), src_dir="src",
            dependencies=root_deps, bin_paths=root_bins,
        ))

    # Parse workspace-level shared dependencies
    ws_deps = _parse_deps(ws_deps_raw, Path(repo_path), repo)

    # Parse exclude patterns
    exclude_patterns = workspace.get("exclude", [])
    excluded_paths: set[Path] = set()
    for pattern in exclude_patterns:
        if isinstance(pattern, str):
            if pattern == ".":
                excluded_paths.add(repo)
            else:
                excluded_paths.update(p.resolve() for p in repo.glob(pattern))

    for member_pattern in members:
        if not isinstance(member_pattern, str):
            continue
        if member_pattern == ".":
            matched_paths = [repo]
        else:
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
            if member_path == repo and root_pkg.get("name"):
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
            # Check if this is a proc-macro crate
            lib_section = member_data.get("lib", {})
            is_proc_macro = bool(
                lib_section.get("proc-macro", False) or lib_section.get("proc_macro", False)
            )
            src_dir = f"{member_rel}/src" if member_rel else "src"
            member_deps = _parse_deps(
                {**member_data.get("dependencies", {}),
                 **member_data.get("dev-dependencies", {}),
                 **member_data.get("build-dependencies", {})},
                member_path,
                repo,
                ws_deps=ws_deps_raw,
            )
            bin_paths = _parse_bin_targets(member_data, member_rel, repo)
            crates.append(CargoCrate(
                name=str(name),
                src_dir=src_dir,
                dependencies=member_deps,
                is_proc_macro=is_proc_macro,
                bin_paths=bin_paths,
            ))

    if not crates:
        return None
    log.debug("Built Cargo workspace index", crate_count=len(crates))
    return CargoWorkspaceIndex(crates=tuple(crates), workspace_dependencies=ws_deps)
