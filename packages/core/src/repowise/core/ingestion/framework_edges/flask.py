"""Flask ``register_blueprint`` convention edges.

The registration is recognised by ``ingestion.framework_routes``, shared with
the contract dialect that reads the same call for its ``url_prefix``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..framework_routes import flask_blueprints
from ..resolvers import ResolverContext, resolve_import
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


def _add_flask_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Detect register_blueprint() calls and link app files to blueprint modules."""
    count = 0
    var_to_file: dict[str, str] = {}

    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            for name in imp.imported_names:
                if "blueprint" in name.lower() or name.lower().endswith("bp"):
                    resolved = resolve_import(
                        imp.module_path,
                        path,
                        parsed.file_info.language,
                        ctx,
                    )
                    if resolved and resolved in path_set:
                        var_to_file[name] = resolved

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        source = read_text(parsed)
        for mount in flask_blueprints(source):
            # The import map is keyed on the name this file bound, so a dotted
            # `views.bp` is resolved through its head, as the module it names.
            var_name = mount.var.split(".")[0]
            target = var_to_file.get(var_name)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1
    return count


class _FlaskHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "flask" in dctx.stack_lower

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_flask_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_FlaskHandler()]
