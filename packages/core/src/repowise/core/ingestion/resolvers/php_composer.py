"""composer.json PSR-4 autoload parsing for PHP import resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext


def _normalise_dirs(value: object) -> list[str]:
    """PSR-4 values may be a single string or a list of strings."""
    if isinstance(value, str):
        return [value.rstrip("/")]
    if isinstance(value, list):
        return [str(v).rstrip("/") for v in value if isinstance(v, str)]
    return []


def read_composer_psr4(repo_path: Path | None) -> dict[str, list[str]]:
    """Read root ``composer.json`` and return ``{namespace_prefix: [dir, ...]}``.

    Both ``autoload.psr-4`` and ``autoload-dev.psr-4`` are merged. Namespace
    keys are returned with their trailing ``\\`` preserved (composer spec).
    """
    if repo_path is None:
        return {}
    composer = repo_path / "composer.json"
    if not composer.is_file():
        return {}
    try:
        data = json.loads(composer.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}

    result: dict[str, list[str]] = {}
    for section in ("autoload", "autoload-dev"):
        block = data.get(section) if isinstance(data, dict) else None
        if not isinstance(block, dict):
            continue
        psr4 = block.get("psr-4")
        if not isinstance(psr4, dict):
            continue
        for prefix, dirs in psr4.items():
            if not isinstance(prefix, str):
                continue
            result.setdefault(prefix, []).extend(_normalise_dirs(dirs))
    return result


def get_or_build_psr4_map(ctx: "ResolverContext") -> dict[str, list[str]]:
    cached = getattr(ctx, "_php_psr4_map", None)
    if cached is not None:
        return cached
    psr4 = read_composer_psr4(ctx.repo_path)
    ctx._php_psr4_map = psr4  # type: ignore[attr-defined]
    return psr4


def resolve_via_psr4(module_path: str, ctx: "ResolverContext") -> str | None:
    """Try PSR-4 prefix matching from composer.json. Returns repo-relative
    path or None.

    *module_path* is the FQN as written in PHP (``Foo\\Bar\\Baz`` form).
    """
    psr4 = get_or_build_psr4_map(ctx)
    if not psr4:
        return None
    fqn = module_path.replace("/", "\\")
    # Longest-prefix match wins. Composer prefixes always end in `\\`.
    best: tuple[str, list[str]] | None = None
    for prefix, dirs in psr4.items():
        if not prefix:
            continue
        if fqn.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, dirs)
    if best is None:
        return None
    prefix, dirs = best
    tail = fqn[len(prefix) :].replace("\\", "/")
    if not tail:
        return None
    for base_dir in dirs:
        candidate = f"{base_dir}/{tail}.php" if base_dir else f"{tail}.php"
        if candidate in ctx.path_set:
            return candidate
        # Also tolerate paths whose first segment differs (some repos vendor
        # the root differently). Probe by suffix as a forgiving fallback.
        suffix_probe = f"/{candidate}"
        for p in ctx.path_set:
            if p == candidate or p.endswith(suffix_probe):
                return p
    return None
