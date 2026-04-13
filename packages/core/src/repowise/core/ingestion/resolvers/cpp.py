"""C / C++ import resolution."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext


def resolve_cpp_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a C/C++ ``#include`` to a repo-relative file path."""
    importer_dir = Path(importer_path).parent
    repo_root = ctx.repo_path.resolve() if ctx.repo_path else None

    # 1. Try compile_commands.json include paths
    for inc_dir in ctx.extract_include_dirs(importer_path):
        candidate = (Path(inc_dir) / module_path).resolve()
        if repo_root:
            try:
                rel = candidate.relative_to(repo_root).as_posix()
                if rel in ctx.path_set:
                    return rel
            except ValueError:
                pass

    # 2. Try relative to the importer's directory
    if repo_root:
        try:
            rel = (importer_dir / module_path).resolve().relative_to(repo_root).as_posix()
            if rel in ctx.path_set:
                return rel
        except ValueError:
            pass

    # 3. Stem-matching fallback
    stem = Path(module_path).stem.lower()
    return ctx.stem_lookup(stem)
