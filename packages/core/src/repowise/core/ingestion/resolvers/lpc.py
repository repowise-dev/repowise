"""Resolve literal LPC inherit/include statements to indexed repository files."""

from __future__ import annotations

from pathlib import Path

from ..languages.lpc_support import resolve_statement
from .context import ResolverContext


def resolve_lpc_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    repo_root = ctx.repo_path.resolve() if ctx.repo_path else Path.cwd().resolve()
    importer = Path(importer_path)
    if not importer.is_absolute():
        importer = repo_root / importer
    target = resolve_statement(module_path, importer)
    if target is None:
        return None
    try:
        rel = target.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None  # resolved into a root outside this repository
    return rel if rel in ctx.path_set else None
