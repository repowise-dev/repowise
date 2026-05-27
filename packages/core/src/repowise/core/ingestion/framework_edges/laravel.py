"""Laravel routes / service-provider / Eloquent convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_LARAVEL_ROUTE_ARRAY_RE = re.compile(
    r"Route::(?:get|post|put|patch|delete|any|match|resource|apiResource)\s*\([^,]*,\s*\[\s*([\w\\]+)::class"
)
_LARAVEL_ROUTE_LEGACY_RE = re.compile(
    r"Route::(?:get|post|put|patch|delete|any|match)\s*\([^,]*,\s*['\"]([\w\\]+)@\w+['\"]"
)
_LARAVEL_ROUTE_RESOURCE_RE = re.compile(
    r"Route::(?:resource|apiResource)\s*\(\s*['\"][^'\"]+['\"]\s*,\s*([\w\\]+)::class"
)
_LARAVEL_BIND_RE = re.compile(
    r"->\s*(?:bind|singleton|instance)\s*\(\s*([\w\\]+)::class\s*,\s*([\w\\]+)::class"
)
_LARAVEL_ELOQUENT_RE = re.compile(
    r"\$this->\s*(?:hasMany|hasOne|belongsTo|belongsToMany|morphMany|morphOne|morphTo)\s*\(\s*([\w\\]+)::class"
)


def _resolve_laravel_class(
    ctx: ResolverContext, fqn: str, class_to_file: dict[str, str], path_set: set[str]
) -> str | None:
    """Resolve `Foo\\Bar\\Baz` (or short `Bar`) to repo-relative .php path."""
    from ..resolvers.php_composer import resolve_via_psr4

    if "\\" in fqn:
        result = resolve_via_psr4(fqn, ctx)
        if result and result in path_set:
            return result
    short = fqn.rsplit("\\", 1)[-1]
    return class_to_file.get(short)


def _add_laravel_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("php",))

    # ---- routes/web.php / routes/api.php → controllers ----
    for routes_path in ("routes/web.php", "routes/api.php"):
        if routes_path not in path_set:
            continue
        text = read_text(parsed_files[routes_path])
        if not text:
            continue
        seen_targets: set[str] = set()
        for regex in (
            _LARAVEL_ROUTE_ARRAY_RE,
            _LARAVEL_ROUTE_LEGACY_RE,
            _LARAVEL_ROUTE_RESOURCE_RE,
        ):
            for m in regex.finditer(text):
                target = _resolve_laravel_class(ctx, m.group(1), class_to_file, path_set)
                if target and target in path_set and target not in seen_targets:
                    seen_targets.add(target)
                    if _add_edge_if_new(graph, routes_path, target):
                        count += 1

    # ---- Service providers → bound classes ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "php":
            continue
        if not path.endswith("ServiceProvider.php"):
            continue
        text = read_text(parsed)
        if not text:
            continue
        for m in _LARAVEL_BIND_RE.finditer(text):
            for fqn in (m.group(1), m.group(2)):
                target = _resolve_laravel_class(ctx, fqn, class_to_file, path_set)
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

    # ---- Eloquent relationships: model → related model ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "php":
            continue
        text = read_text(parsed)
        if not text:
            continue
        for m in _LARAVEL_ELOQUENT_RE.finditer(text):
            target = _resolve_laravel_class(ctx, m.group(1), class_to_file, path_set)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1

    return count


class _LaravelHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return (
            "laravel" in dctx.stack_lower
            or "routes/web.php" in dctx.path_set
            or "routes/api.php" in dctx.path_set
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_laravel_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_LaravelHandler()]
