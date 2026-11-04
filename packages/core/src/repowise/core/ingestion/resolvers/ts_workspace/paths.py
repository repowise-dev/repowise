"""Repo-relative path probing for workspace targets."""

from __future__ import annotations

import posixpath

_PROBE_EXTENSIONS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
)


def _normalize_repo_rel(base: str) -> str:
    """Collapse a joined repo-relative path so it can match ``path_set``.

    The root package's dir is ``"."``, so joins yield ``"./src/x.ts"``. A
    trailing slash survives (callers use the result as a ``startswith``
    prefix), and a path that is the root itself becomes ``""``.
    """
    if not (base.startswith("./") or "/./" in base):
        return base
    normalized = posixpath.normpath(base)
    if normalized == ".":
        return ""
    return f"{normalized}/" if base.endswith("/") and not normalized.endswith("/") else normalized


def _probe_path(base: str, path_set: set[str]) -> str | None:
    """The file for path stem ``base``: as-is, then with a TS/JS extension, then ``index.*``."""
    base = _normalize_repo_rel(base)
    if base in path_set:
        return base
    for ext in _PROBE_EXTENSIONS:
        cand = base + ext
        if cand in path_set:
            return cand
    for ext in _PROBE_EXTENSIONS:
        cand = f"{base}/index{ext}"
        if cand in path_set:
            return cand
    return None
