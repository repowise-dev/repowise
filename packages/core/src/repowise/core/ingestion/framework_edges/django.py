"""Django convention edges.

The same-directory ``urls``->``views`` guess is kept for the file pairs it was
written for, and a URLconf that names its view module explicitly now also gets
an edge to that module, read from ``ingestion.framework_routes`` — the same
declaration the contract extractor reads for the route's path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..framework_routes import django_includes, django_routes
from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


def _add_django_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """Django conventions: admin->models, urls->views in the same directory."""
    count = 0
    by_dir: dict[str, dict[str, str]] = {}
    for p in path_set:
        pp = Path(p)
        d = pp.parent.as_posix()
        by_dir.setdefault(d, {})[pp.stem] = p

    for _d, stems in by_dir.items():
        if (
            "admin" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["admin"], stems["models"])
        ):
            count += 1
        if (
            "urls" in stems
            and "views" in stems
            and _add_edge_if_new(graph, stems["urls"], stems["views"])
        ):
            count += 1
        if (
            "forms" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["forms"], stems["models"])
        ):
            count += 1
        if (
            "serializers" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["serializers"], stems["models"])
        ):
            count += 1
    return count


def _module_index(path_set: set[str]) -> dict[str, str]:
    """Dotted module -> file, for every suffix a Django import could name.

    A name claimed by two files is dropped rather than bound to whichever the
    walk reached first.
    """
    seen: dict[str, str | None] = {}
    for path in path_set:
        if not path.endswith(".py"):
            continue
        parts = path.removesuffix("/__init__.py").removesuffix(".py").split("/")
        for i in range(len(parts)):
            module = ".".join(parts[i:])
            seen[module] = None if module in seen and seen[module] != path else path
    return {m: p for m, p in seen.items() if p is not None}


def _add_urlconf_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], path_set: set[str]
) -> int:
    """``urls.py`` -> the view module and the sub-URLconf each entry names."""
    count = 0
    by_module = _module_index(path_set)
    for path, parsed in parsed_files.items():
        if not path.endswith("urls.py") or parsed.file_info.language != "python":
            continue
        text = read_text(parsed)
        if not text:
            continue
        # The head of `views.detail` is a module path, not a declaring type:
        # the module resolves to a file, the trailing segment is the view.
        modules = [
            route.handler.rpartition(".")[0]
            for route in django_routes(text)
            if route.handler and "." in route.handler
        ]
        modules += [module for _prefix, module in django_includes(text)]
        for module in modules:
            target = by_module.get(module)
            if target and _add_edge_if_new(graph, path, target):
                count += 1
    return count


class _DjangoHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "django" in dctx.stack_lower

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_django_edges(graph, path_set) + _add_urlconf_edges(
            graph, parsed_files, path_set
        )


HANDLERS: list[FrameworkHandler] = [_DjangoHandler()]
