"""Go import resolution."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext


def read_go_module_path(repo_path: Path | None) -> str | None:
    """Read the ``module`` directive from ``go.mod``, if present."""
    if repo_path is None:
        return None
    go_mod = repo_path / "go.mod"
    if not go_mod.is_file():
        return None
    try:
        for line in go_mod.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    except Exception:
        pass
    return None


def resolve_go_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Go import path to a repo-relative file path."""
    # If we know the module path and the import starts with it,
    # strip the prefix to get the repo-relative package dir.
    if ctx.go_module_path and module_path.startswith(ctx.go_module_path):
        suffix = module_path[len(ctx.go_module_path) :]
        rel_dir = suffix.lstrip("/")
        # Find any Go file in that directory
        for p in ctx.path_set:
            if p.endswith(".go"):
                p_dir = str(Path(p).parent.as_posix())
                if p_dir == rel_dir or p_dir.endswith(f"/{rel_dir}"):
                    return p
        # Try stem matching as fallback for the package name
        pkg_name = rel_dir.rsplit("/", 1)[-1].lower() if rel_dir else ""
        if pkg_name:
            result = ctx.stem_lookup(pkg_name)
            if result:
                return result

    # No go.mod match — fall back to stem matching on the last segment
    stem = module_path.rsplit("/", 1)[-1].lower()
    result = ctx.stem_lookup(stem)
    if result:
        return result

    # External package
    return ctx.add_external_node(module_path)
