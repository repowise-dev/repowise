"""Python import resolution."""

from __future__ import annotations

from pathlib import Path

from ..languages.python_modules import build_python_module_index
from .context import ResolverContext


def _module_index(ctx: ResolverContext) -> dict[str, str]:
    """Source-root-aware dotted-module → path index, built once per context.

    Cached on the context (mirrors the lazy per-language index pattern used
    by the PHP / TS / Kotlin resolvers) so the index is computed a single
    time across every Python import in a build.
    """
    idx = getattr(ctx, "_python_module_index", None)
    if idx is None:
        idx = build_python_module_index(ctx.path_set)
        ctx._python_module_index = idx
    return idx


def resolve_python_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Python import to a repo-relative file path."""
    importer_dir = Path(importer_path).parent

    # Relative import: ".sibling" or "..parent.module"
    if module_path.startswith("."):
        dots = len(module_path) - len(module_path.lstrip("."))
        rest = module_path[dots:].replace(".", "/")
        base = importer_dir
        for _ in range(dots - 1):
            base = base.parent
        candidates = [
            (base / rest).with_suffix(".py").as_posix() if rest else None,
            (base / rest / "__init__.py").as_posix() if rest else None,
        ]
        for c in candidates:
            if c and c in ctx.path_set:
                return c
        return None

    # Absolute import: resolve via the source-root-aware module index first.
    # This maps the fully-qualified dotted name to its defining file no
    # matter how deeply the source root is nested (``src/``,
    # ``packages/*/src/``, …) — the case the naive layout probes below miss,
    # and the reason cross-package imports such as
    # ``from repowise.core.persistence.models import SecurityFinding`` used
    # to fall through to an ambiguous stem match.
    hit = _module_index(ctx).get(module_path)
    if hit:
        return hit

    # Fallback: obvious flat / single-``src`` filesystem layouts. Kept as a
    # belt-and-suspenders path for PEP 420 namespace packages (no
    # ``__init__.py``) that the index cannot derive a dotted name for.
    dotted = module_path.replace(".", "/")
    candidates = [
        f"{dotted}.py",
        f"{dotted}/__init__.py",
        f"src/{dotted}.py",
        f"src/{dotted}/__init__.py",
    ]
    for c in candidates:
        if c in ctx.path_set:
            return c

    # Stem-only fallback
    stem = module_path.split(".")[-1].lower()
    return ctx.stem_lookup(stem)
