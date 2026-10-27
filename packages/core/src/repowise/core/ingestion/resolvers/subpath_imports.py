"""Node.js subpath imports (``package.json`` ``"imports"``, the ``#`` prefix).

Node lets a package declare private aliases for its own internals::

    "imports": { "#lib/*": "./src/lib/*", "#config": "./src/config.ts" }

so ``import x from '#lib/components/Modal.svelte'`` resolves inside the same
package. This is a plain Node/ESM feature — SvelteKit apps reach for it as the
alternative to ``$lib``, but so do ordinary TS/JS packages. Without it every
``#``-prefixed specifier becomes an ``external:`` node, which strands whatever
it points at: on svelte.dev that alone marked eight live components unreachable.

Only ``"imports"`` (``#`` keys, package-private) lives here. ``"exports"``
(what a package shows the outside world) is already handled by
``ts_workspace.resolve_via_workspaces``.
"""

from __future__ import annotations

import json
import posixpath
from typing import TYPE_CHECKING

from .ts_workspace import _get_repo_scan, _probe_path

if TYPE_CHECKING:
    from .context import ResolverContext

_SUBPATH_PREFIX = "#"

# Beyond the TS/JS extensions _probe_path already tries — a subpath alias
# routinely points straight at a component.
_EXTRA_EXTENSIONS = (".svelte", ".vue", ".astro")


def _flatten(value: object) -> str | None:
    """Reduce a conditional target ({"import": ..., "default": ...}) to a path."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("import", "module", "default", "require", "node"):
            if key in value:
                flattened = _flatten(value[key])
                if flattened:
                    return flattened
        for nested in value.values():
            flattened = _flatten(nested)
            if flattened:
                return flattened
    if isinstance(value, list):
        for item in value:
            flattened = _flatten(item)
            if flattened:
                return flattened
    return None


def _build_index(ctx: ResolverContext) -> tuple[tuple[str, dict[str, str]], ...]:
    """(package_dir, {alias_key: target}) for every package declaring imports.

    Sorted deepest-first so a nested package's aliases win over the root's.
    """
    cached = getattr(ctx, "_subpath_imports_index", None)
    if cached is not None:
        return cached

    entries: list[tuple[str, dict[str, str]]] = []
    if ctx.repo_path is not None:
        for pkg_file in _get_repo_scan(ctx).package_jsons:
            try:
                data = json.loads(pkg_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            raw = data.get("imports")
            if not isinstance(raw, dict):
                continue
            aliases: dict[str, str] = {}
            for key, value in raw.items():
                if not isinstance(key, str) or not key.startswith(_SUBPATH_PREFIX):
                    continue
                target = _flatten(value)
                if target:
                    aliases[key] = target.lstrip("./")
            if not aliases:
                continue
            try:
                pkg_dir = pkg_file.parent.relative_to(ctx.repo_path).as_posix()
            except ValueError:
                continue
            entries.append(("" if pkg_dir == "." else pkg_dir, aliases))

    entries.sort(key=lambda pair: len(pair[0]), reverse=True)
    result = tuple(entries)
    ctx._subpath_imports_index = result  # type: ignore[attr-defined]
    return result


def _apply(alias_key: str, target: str, module_path: str) -> str | None:
    """Expand one alias entry against *module_path*, or None if it misses."""
    if "*" not in alias_key:
        return target if module_path == alias_key else None
    prefix, _, suffix = alias_key.partition("*")
    if not module_path.startswith(prefix) or not module_path.endswith(suffix):
        return None
    star = module_path[len(prefix) : len(module_path) - len(suffix) or None]
    return target.replace("*", star, 1)


def resolve_subpath_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve a ``#alias`` specifier to a repo-relative file, or None."""
    if not module_path.startswith(_SUBPATH_PREFIX):
        return None

    for pkg_dir, aliases in _build_index(ctx):
        if pkg_dir and not importer_path.startswith(pkg_dir + "/"):
            continue
        # Longest key first so "#lib/ui/*" beats "#lib/*".
        for alias_key in sorted(aliases, key=len, reverse=True):
            expanded = _apply(alias_key, aliases[alias_key], module_path)
            if expanded is None:
                continue
            base = posixpath.join(pkg_dir, expanded) if pkg_dir else expanded
            hit = _probe_path(base, ctx.path_set)
            if hit is not None:
                return hit
            for ext in _EXTRA_EXTENSIONS:
                if base + ext in ctx.path_set:
                    return base + ext
    return None
