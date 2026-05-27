"""Go import resolution."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext

_GO_MOD_SKIP_DIRS = frozenset({"vendor", "node_modules", ".git"})


def _read_module_directive(go_mod: Path) -> str | None:
    try:
        for line in go_mod.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    except Exception:
        pass
    return None


def read_go_module_path(repo_path: Path | None) -> str | None:
    """Read the ``module`` directive from the root ``go.mod``, if present."""
    if repo_path is None:
        return None
    go_mod = repo_path / "go.mod"
    if not go_mod.is_file():
        return None
    return _read_module_directive(go_mod)


def read_go_modules(repo_path: Path | None) -> tuple[tuple[str, str], ...]:
    """Discover every ``go.mod`` under *repo_path* and return
    ``((module_dir_posix, module_path), ...)`` sorted longest-module-path-first.

    This supports Go monorepos with multiple modules (e.g.
    ``services/foo/go.mod`` + ``libs/bar/go.mod``).
    """
    if repo_path is None or not repo_path.is_dir():
        return ()
    found: list[tuple[str, str]] = []
    for go_mod in repo_path.rglob("go.mod"):
        # Skip vendored / nested-package directories
        rel_parts = go_mod.relative_to(repo_path).parts
        if any(part in _GO_MOD_SKIP_DIRS for part in rel_parts):
            continue
        module_path = _read_module_directive(go_mod)
        if not module_path:
            continue
        module_dir = go_mod.parent.relative_to(repo_path).as_posix()
        if module_dir == ".":
            module_dir = ""
        found.append((module_dir, module_path))
    # Longest module path first so prefix-matching prefers the most specific.
    found.sort(key=lambda t: len(t[1]), reverse=True)
    return tuple(found)


def resolve_go_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Go import path to a repo-relative file path."""
    # Multi-module: try each known module's prefix, longest first.
    for module_dir, mod_path in ctx.go_modules:
        if module_path == mod_path or module_path.startswith(mod_path + "/"):
            suffix = module_path[len(mod_path) :].lstrip("/")
            if module_dir and suffix:
                rel_dir = f"{module_dir}/{suffix}"
            else:
                rel_dir = module_dir or suffix
            for p in ctx.path_set:
                if p.endswith(".go"):
                    p_dir = str(Path(p).parent.as_posix())
                    if p_dir == rel_dir or (rel_dir and p_dir.endswith(f"/{rel_dir}")):
                        return p
            pkg_name = (rel_dir or suffix).rsplit("/", 1)[-1].lower()
            if pkg_name:
                result = ctx.stem_lookup(pkg_name)
                if result:
                    return result
            # Matched a module but found nothing — fall through to global
            # stem map / external rather than breaking early.
            break

    # Single-module back-compat: use the legacy ``go_module_path`` field if
    # ``go_modules`` was not populated (e.g. tests building a context manually).
    if not ctx.go_modules and ctx.go_module_path and module_path.startswith(ctx.go_module_path):
        suffix = module_path[len(ctx.go_module_path) :].lstrip("/")
        for p in ctx.path_set:
            if p.endswith(".go"):
                p_dir = str(Path(p).parent.as_posix())
                if p_dir == suffix or p_dir.endswith(f"/{suffix}"):
                    return p
        pkg_name = suffix.rsplit("/", 1)[-1].lower() if suffix else ""
        if pkg_name:
            result = ctx.stem_lookup(pkg_name)
            if result:
                return result

    # No module match — fall back to stem matching on the last segment.
    stem = module_path.rsplit("/", 1)[-1].lower()
    result = ctx.stem_lookup(stem)
    if result:
        return result

    # External package
    return ctx.add_external_node(module_path)


def resolve_go_import_all(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a Go import to **every** ``.go`` file in the target package.

    Go imports name a package *directory*; the package's symbols are spread
    across all its sibling ``.go`` files with no per-file import. The
    legacy :func:`resolve_go_import` returns a single representative file,
    which leaves the package's other files with ``in_degree == 0`` and
    mis-flagged as unreachable. This variant consults the
    :class:`GoPackageIndex` and returns the full file set so the builder
    can fan an IMPORTS edge out to each one.

    External packages (no local ``go.mod`` match) resolve to a single
    external node — exactly as :func:`resolve_go_import` would — and are
    returned as a one-tuple. Returns an empty tuple only when nothing at
    all resolves.
    """
    from .go_workspace import get_or_build_go_index

    index = get_or_build_go_index(ctx)
    files = index.files_for_import(module_path)
    if files:
        return files

    # Not a known local package directory — defer to the single-target
    # resolver (handles longest-prefix stem matches and external nodes).
    single = resolve_go_import(module_path, importer_path, ctx)
    return (single,) if single else ()
