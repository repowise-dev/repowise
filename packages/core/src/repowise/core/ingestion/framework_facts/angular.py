"""Angular and Nx: the entry files a workspace config names."""

from __future__ import annotations

import posixpath
from collections.abc import Set as AbstractSet

from .base import FrameworkFacts

ANGULAR = FrameworkFacts(name="Angular", anchor="framework:angular")

#: Workspace config naming what an Angular build loads: Angular CLI's
#: ``angular.json`` and an Nx project's ``project.json``.
ANGULAR_WORKSPACE_FILES = frozenset({"angular.json", "project.json"})
# Build and test options whose value is a file loaded with no importer. A
# package (`"polyfills": ["zone.js"]`) is dropped by the path check.
_ANGULAR_ENTRY_OPTIONS = frozenset({"main", "browser", "server", "polyfills", "karmaConfig", "scripts"})
# Options holding objects that name such a file: `fileReplacements: [{ with }]`,
# `ssr: { entry }`, `scripts: [{ input }]`.
_ANGULAR_ENTRY_KEYS = ("with", "entry", "input")


def _nx_root(config_path: str, paths: AbstractSet[str]) -> str:
    """The nearest directory above an Nx ``project.json`` holding ``nx.json``, else the repo root."""
    parts = config_path.split("/")[:-1]
    for depth in range(len(parts), 0, -1):
        base = "/".join(parts[:depth])
        if f"{base}/nx.json" in paths:
            return base
    return ""


def angular_entry_files(config_path: str, config: object, paths: AbstractSet[str]) -> list[str]:
    """The members of *paths* the workspace config at *config_path* names as loaded.

    Entry options (``main``, ``browser``, ``polyfills``, ``scripts``,
    ``ssr.entry``, ...) and each ``fileReplacements`` ``with``
    (``environment.prod.ts``, swapped in by the build), read only from targets
    an Angular builder runs. ``angular.json`` paths are relative to its
    directory, an Nx ``project.json``'s to the workspace root; a value is kept
    only when it names a file.
    """
    values: list[object] = []

    def collect(value: object) -> None:
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                values.extend(item.get(k) for k in _ANGULAR_ENTRY_KEYS)
            else:
                values.append(item)

    def walk(node: object) -> None:
        if isinstance(node, dict):
            builder = node.get("builder") or node.get("executor")
            if isinstance(builder, str) and "angular" not in builder:
                return  # an Nx target another toolchain builds (`@nx/node:build`)
            for key, value in node.items():
                if key in _ANGULAR_ENTRY_OPTIONS or key in ("fileReplacements", "ssr"):
                    collect(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    if config_path.rpartition("/")[2] == "project.json":
        base = _nx_root(config_path, paths)
    else:
        base = posixpath.dirname(config_path)
    found = {
        posixpath.normpath(posixpath.join(base, v)) for v in values if isinstance(v, str) and v
    }
    return sorted(found & paths)
