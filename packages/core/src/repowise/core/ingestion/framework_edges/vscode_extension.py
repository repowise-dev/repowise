"""A VS Code extension test runs the extension its package declares.

An extension test imports the ``vscode`` module and runs inside an editor
host, which loads the package's ``main`` entry (``dist/extension.js``, built
from ``src/extension.ts``) and activates it. The test has no import edge to
that entry, so a change anywhere in the extension reaches no test. In a
package whose ``package.json`` declares ``engines.vscode``, each test file
importing ``vscode`` gets an edge to the files the manifest declares as its
start (:func:`~..resolvers.ts_workspace.manifest_entry_paths`).

Ceiling: a test run against another package's extension is not linked to it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from ..resolvers.ts_workspace import (
    _parsed_package_jsons,
    get_checked_in_builds,
    manifest_entry_paths,
)
from ..source_text import source_bytes
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new

if TYPE_CHECKING:
    import networkx as nx

# Stamped on the edge so consumers that mean "imports" can tell it apart.
VSCODE_HOST_HINT = "vscode_extension_host"

_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_IMPORTS_VSCODE = re.compile(rb"""(?:from\s+|require\(\s*|import\s*\(\s*)["']vscode["']""")


def _extensions(ctx: ResolverContext, path_set: set[str]) -> dict[str, set[str]]:
    """``{package dir: its entry files}`` for every package that is a VS Code extension."""
    checked_in = get_checked_in_builds(ctx)
    out: dict[str, set[str]] = {}
    for pkg_dir, data in _parsed_package_jsons(ctx):
        engines = data.get("engines")
        if isinstance(engines, dict) and "vscode" in engines:
            entries = manifest_entry_paths(pkg_dir, data, path_set, checked_in)
            if entries:
                out[pkg_dir] = entries
    return out


def _add_host_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    extensions = _extensions(ctx, path_set)
    count = 0
    for pkg_dir, entries in sorted(extensions.items()):
        prefix = "" if pkg_dir in ("", ".") else f"{pkg_dir}/"
        for path in sorted(p for p in path_set if p.startswith(prefix) and p.endswith(_JS_EXTS)):
            if not parsed_files[path].file_info.is_test:
                continue
            text = source_bytes(path, parsed_files[path].file_info.abs_path, ctx.source_map)
            if not _IMPORTS_VSCODE.search(text):
                continue
            for entry in sorted(entries):
                if _add_edge_if_new(graph, path, entry):
                    graph[path][entry]["hint_source"] = VSCODE_HOST_HINT
                    count += 1
    return count


class _VscodeHostHandler:
    """Extension tests run the extension their package declares."""

    def detect(self, dctx: DetectionContext) -> bool:
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_host_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_VscodeHostHandler()]
