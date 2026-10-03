"""Resolve BYOND Dream Maker ``#include`` paths."""

from __future__ import annotations

import posixpath

from .context import ResolverContext


def resolve_dm_include(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> str | None:
    """Resolve a DM include after normalizing Dream Maker's Windows separators.

    Dream Maker projects commonly spell paths in ``.dme`` files with
    backslashes even when the repository is indexed on POSIX. Includes may be
    project-root-relative (the normal ``.dme`` form) or relative to the file
    containing the directive, so both exact candidates are checked before an
    unambiguous suffix fallback.
    """
    raw = module_path.strip().replace("\\", "/")
    if not raw or raw.startswith(("/", "~")):
        return None

    normalized = posixpath.normpath(raw)
    if normalized in (".", "/"):
        return None

    importer_dir = posixpath.dirname(importer_path)
    relative = posixpath.normpath(posixpath.join(importer_dir, normalized))

    root_candidate = normalized if not normalized.startswith("..") else ""
    candidates = (
        (root_candidate, relative)
        if importer_path.lower().endswith(".dme")
        else (
            relative,
            root_candidate,
        )
    )
    for candidate in candidates:
        if candidate and not candidate.startswith("..") and candidate in ctx.path_set:
            return candidate

    if normalized.startswith("..") or "/" not in normalized:
        return None
    needle = f"/{normalized}"
    hits = [path for path in ctx.sorted_paths if path.endswith(needle)]
    return hits[0] if len(hits) == 1 else None
