"""F# compile-order dependency spine from fsproj ``<Compile Include>`` items.

F# compiles a project's files in the declared order, and a file may only
reference earlier files — the fsproj item order IS a real dependency
constraint, not a heuristic. Each consecutive pair contributes one
``imports`` hint edge (``later → earlier``); adjacent pairs keep the
spine linear instead of quadratic, and the graph stays connected through
the chain. ``hint_source="compile_order"`` marks the synthesis so density
metrics can separate declared imports from project-file evidence.

A real ``open``-resolved edge between the same pair wins (the pass never
overwrites existing edges).
"""

from __future__ import annotations

import itertools
import posixpath
import re
from pathlib import Path

import networkx as nx
import structlog

log = structlog.get_logger(__name__)

_COMPILE_INCLUDE_RE = re.compile(r"<Compile\s+Include\s*=\s*\"([^\"]+)\"", re.I)

_HINT = "compile_order"


def _project_sources(fsproj: Path, repo_path: Path) -> list[str]:
    """Repo-relative source paths in fsproj declaration order."""
    try:
        text = fsproj.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    project_dir = fsproj.parent
    sources: list[str] = []
    for raw in _COMPILE_INCLUDE_RE.findall(text):
        rel = raw.replace("\\", "/")
        if "*" in rel or "$(" in rel:  # globs / MSBuild properties — skip
            continue
        try:
            full = (project_dir / rel).resolve().relative_to(repo_path.resolve())
        except (ValueError, OSError):
            continue
        sources.append(posixpath.normpath(full.as_posix()))
    return sources


def add_fsharp_compile_order_edges(
    graph: nx.DiGraph, repo_path: Path, *, prune_nested_git: bool = True
) -> int:
    """Emit ``later → earlier`` hint edges per fsproj; return count added."""
    from repowise.core.fs_walk import iter_glob

    added = 0
    for fsproj in sorted(iter_glob(repo_path, "*.fsproj", prune_nested_git=prune_nested_git)):
        sources = [s for s in _project_sources(fsproj, repo_path) if graph.has_node(s)]
        for earlier, later in itertools.pairwise(sources):
            if graph.has_edge(later, earlier):
                continue  # a resolved open (or stronger evidence) wins
            graph.add_edge(
                later,
                earlier,
                edge_type="imports",
                imported_names=[],
                hint_source=_HINT,
            )
            added += 1
    return added
