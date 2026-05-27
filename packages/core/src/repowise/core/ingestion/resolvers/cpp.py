"""C / C++ import resolution."""

from __future__ import annotations

import posixpath
from pathlib import Path

from .context import ResolverContext


def resolve_cpp_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a C/C++ ``#include`` to a repo-relative file path."""
    repo_root = ctx.repo_path.resolve() if ctx.repo_path else None

    # The graph builder passes ``importer_path`` as a *repo-relative* POSIX
    # path. Reduce it to its repo-relative directory; if a caller ever passes
    # an absolute path, fold it back under the repo root first so the
    # importer-relative join below stays repo-relative (and never resolves
    # against the process CWD).
    importer_rel = importer_path
    if repo_root and Path(importer_path).is_absolute():
        try:
            importer_rel = Path(importer_path).resolve().relative_to(repo_root).as_posix()
        except ValueError:
            importer_rel = Path(importer_path).as_posix()
    importer_dir = posixpath.dirname(Path(importer_rel).as_posix())

    # 1. Try compile_commands.json include paths (absolute on-disk dirs)
    for inc_dir in ctx.extract_include_dirs(importer_path):
        candidate = (Path(inc_dir) / module_path).resolve()
        if repo_root:
            try:
                rel = candidate.relative_to(repo_root).as_posix()
                if rel in ctx.path_set:
                    return rel
            except ValueError:
                pass

    # 2. Relative to the importer's directory. ``importer_dir`` and the
    # ``#include`` target are both repo-relative, so normalise the join as a
    # plain POSIX path (collapsing ``..``/``.``) and test membership directly —
    # never touch the filesystem, which would resolve against the CWD.
    candidate_rel = posixpath.normpath(posixpath.join(importer_dir, module_path))
    if candidate_rel in ctx.path_set:
        return candidate_rel

    # 3. Stem-matching fallback
    stem = Path(module_path).stem.lower()
    return ctx.stem_lookup(stem)
