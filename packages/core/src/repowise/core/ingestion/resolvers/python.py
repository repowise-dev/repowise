"""Python import resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..languages.python_modules import build_python_module_index
from .context import ResolverContext

if TYPE_CHECKING:
    from ..models import Import


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


def resolve_python_import_all(
    imp: Import, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a Python import, fanning edges out to submodule files.

    ``from pkg import a, b`` resolves to ``pkg/__init__.py`` only, so when an
    imported name is itself a submodule file the submodule never gains an
    inbound edge and the dead-code analyzer reports it unreachable (#666) —
    FastAPI apps wiring routers through their package are the canonical hit.
    Probe every imported name against the package directory and emit the
    submodule targets alongside the package itself. The bare-relative form
    (``from . import a, b``) is already split upstream by
    ``expand_bare_relative_imports``; this covers the named-package forms,
    both absolute and relative.
    """
    base = resolve_python_import(imp.module_path, importer_path, ctx)
    if base is None:
        return ()
    targets = [base]
    names = imp.imported_names or []
    if base.endswith("__init__.py") and names and names != ["*"]:
        index = _module_index(ctx)
        base_dir = Path(base).parent.as_posix()
        for name in names:
            if not name or name == "*" or "." in name:
                continue
            # Source-root-aware index first (absolute imports), then direct
            # sibling probes, which also cover the relative form.
            hit = None
            if not imp.is_relative:
                hit = index.get(f"{imp.module_path}.{name}")
            if hit is None:
                for candidate in (f"{base_dir}/{name}.py", f"{base_dir}/{name}/__init__.py"):
                    if candidate in ctx.path_set:
                        hit = candidate
                        break
            if hit and hit != base:
                targets.append(hit)
    return tuple(dict.fromkeys(targets))
