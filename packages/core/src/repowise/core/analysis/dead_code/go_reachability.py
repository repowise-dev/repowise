"""Package-granular reachability for Go.

Go's unit of compilation is the *package* (a directory of ``.go`` files
sharing a ``package X`` clause), not the file. The generic unreachable-file
pass treats ``in_degree == 0`` as dead, which mis-flags two Go-specific
shapes the file-level view cannot see:

- **Entry-package siblings.** ``cmd/foo/main.go`` is an entry point, but the
  helper files next to it (``cmd/foo/server.go`` …) are never *imported* —
  they are compiled into the ``package main`` binary alongside ``main.go``.
  File-level reachability flags them; package-level reachability does not.
- **Belt-and-suspenders for a partially-linked package.** Phase 1's import
  fan-out gives every file in an imported package an inbound edge, so an
  imported package is normally all-or-nothing. If any sibling still carries
  an importer, the whole package is live.

``func init`` deliberately does **not** rescue a package here: ``init`` runs
only when the package is *linked*, which requires the package to be imported
(or to be ``package main``). An otherwise-unimported package whose only
signal is ``func init`` is genuinely dead, so we let it surface.

The helper derives packages from the graph itself (``.go`` file nodes grouped
by parent directory) — the ``GoPackageIndex`` built during ingestion is not
threaded into the analyzer, and the directory grouping is all reachability
needs.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def _pkg_dir(node: str) -> str:
    """Repo-relative POSIX directory of a ``.go`` file ("" = repo root)."""
    parent = PurePosixPath(node).parent.as_posix()
    return "" if parent == "." else parent


def build_go_package_files(graph: Any) -> dict[str, list[str]]:
    """Group every ``.go`` file node in *graph* by its package directory."""
    packages: dict[str, list[str]] = {}
    for node in graph.nodes():
        s = str(node)
        if not s.endswith(".go"):
            continue
        packages.setdefault(_pkg_dir(s), []).append(s)
    return packages


def is_go_file_reachable(
    node: str,
    graph: Any,
    package_files: dict[str, list[str]],
) -> bool:
    """Return True if a Go file is reachable at *package* granularity.

    Called from the analyzer only for ``.go`` nodes that already survived the
    generic skips (entry point, test, never-flag) and have ``in_degree == 0``.
    A file is reachable when any sibling in its package is imported from
    outside the package, or when any sibling is an entry-point file (the
    package is an entry ``package main``). See the module docstring for the
    ``func init`` rationale.
    """
    if graph.in_degree(node) > 0:
        return True

    for sibling in package_files.get(_pkg_dir(node), ()):
        if sibling == node:
            continue
        if graph.in_degree(sibling) > 0:
            return True
        if graph.nodes.get(sibling, {}).get("is_entry_point", False):
            return True

    return False
