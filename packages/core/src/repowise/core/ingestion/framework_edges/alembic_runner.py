"""Code that runs Alembic commands loads every migration script by path.

``alembic.command.upgrade(config, "head")`` imports ``env.py`` and each file in
the script directory's ``versions/`` from the filesystem, so the code calling
it has no import edge to any of them, and a test that migrates a database
reaches none of the migrations it runs. Each Python file using
``alembic.command`` gets an edge to every file of every script directory: a
directory holding ``env.py`` beside a ``versions/`` directory of migrations.

Ceiling: the script directory a call points at is not read from its config;
every script directory in the repository is linked, which over-claims only in
a repository holding several.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from ..source_text import source_bytes
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new

if TYPE_CHECKING:
    import networkx as nx

# Stamped on the edge so consumers that mean "imports" can tell it apart.
ALEMBIC_RUNNER_HINT = "alembic_runner"

_COMMAND_USE = re.compile(
    rb"^\s*(?:from\s+alembic\s+import\s+[^\n]*\bcommand\b"
    rb"|import\s+alembic\.command\b"
    rb"|from\s+alembic\.command\s+import\b)",
    re.MULTILINE,
)


def script_files(path_set: set[str]) -> list[str]:
    """``env.py`` and the migrations of every Alembic script directory in *path_set*."""
    out: list[str] = []
    for path in sorted(path_set):
        folder = posixpath.dirname(path)
        if posixpath.basename(folder) == "versions" and path.endswith(".py"):
            env = posixpath.join(posixpath.dirname(folder), "env.py")
            if env in path_set:
                out.append(path)
        elif posixpath.basename(path) == "env.py" and any(
            p.startswith(f"{folder}/versions/") for p in path_set
        ):
            out.append(path)
    return out


def _add_runner_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    scripts = script_files(path_set)
    if not scripts:
        return 0
    count = 0
    for path in sorted(p for p in path_set if p.endswith(".py")):
        text = source_bytes(path, parsed_files[path].file_info.abs_path, ctx.source_map)
        if b"alembic" not in text or not _COMMAND_USE.search(text):
            continue
        for target in scripts:
            if _add_edge_if_new(graph, path, target):
                graph[path][target]["hint_source"] = ALEMBIC_RUNNER_HINT
                count += 1
    return count


class _AlembicRunnerHandler:
    """Code running Alembic commands loads every migration script."""

    def detect(self, dctx: DetectionContext) -> bool:
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_runner_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_AlembicRunnerHandler()]
