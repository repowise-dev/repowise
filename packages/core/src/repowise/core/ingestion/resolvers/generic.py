"""Generic stem-based import resolution fallback."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext


def resolve_generic_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Fall back to stem matching for unsupported languages."""
    stem = Path(module_path).stem.lower()
    return ctx.stem_lookup(stem)
