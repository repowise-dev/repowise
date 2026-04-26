"""Python import resolution."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext


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

    # Absolute import: try obvious filesystem layouts
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
