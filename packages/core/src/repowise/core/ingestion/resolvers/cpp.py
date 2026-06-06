"""C / C++ import resolution.

Resolves a ``#include "x/y.h"`` or ``#include <x/y.h>`` directive to a
repo-relative file path. Order, highest-fidelity first:

1. **compile_commands.json include dirs** for the *importer* TU.
2. **Workspace public-header map** (``CppWorkspaceIndex.public_header_includes``)
   so ``#include "leveldb/cache.h"`` lands on
   ``include/leveldb/cache.h`` even without ``compile_commands``.
3. **Per-target include-search-roots** for every target that owns the
   importer.
4. **Importer-directory relative** join (``db_impl.cc`` includes
   ``"db/builder.h"`` → ``db/builder.h``).
5. **Stdlib filter** — angle-bracket system includes like ``<vector>``
   / ``<stdio.h>`` resolve to ``None`` (no edge emitted) so the graph
   isn't polluted with one external node per stdlib import.
6. **Stem fallback** — last-ditch lookup against the global stem map.

:func:`resolve_cpp_import_all` performs sibling fan-out: when the
importer is a translation unit (``.cc`` / ``.cpp`` / ``.c``) and the
include resolves to a header that belongs to one or more targets in
the workspace index, the resolver also emits import targets for every
other TU sharing those targets. That mirrors the JVM / Go same-package
fan-out and rescues the leveldb-shape false positives where every
public header reads as ``unreachable_file`` because the only ``.cc``
that includes it is one file out of many in the same library target.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from .context import ResolverContext


_SOURCE_TU_EXTS: tuple[str, ...] = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cppm", ".ixx", ".mxx")
_HEADER_EXTS: tuple[str, ...] = (".h", ".hpp", ".hxx", ".hh", ".h++", ".inc")


def _is_system_include(module_path: str, system_form: bool) -> bool:
    """Best-effort check for a stdlib-style angle-bracket include."""
    if not system_form:
        return False
    from .cpp_workspace import is_stdlib_include

    return is_stdlib_include(module_path)


def _normalise_importer(importer_path: str, ctx: ResolverContext) -> tuple[str, str]:
    """Return ``(importer_repo_relative, importer_dir)``."""
    repo_root = ctx.repo_path.resolve() if ctx.repo_path else None
    importer_rel = importer_path
    if repo_root and Path(importer_path).is_absolute():
        try:
            importer_rel = Path(importer_path).resolve().relative_to(repo_root).as_posix()
        except ValueError:
            importer_rel = Path(importer_path).as_posix()
    importer_dir = posixpath.dirname(Path(importer_rel).as_posix())
    return importer_rel, importer_dir


def _resolve_single(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
    *,
    system_form: bool,
) -> str | None:
    """Resolve a single include path; returns the resolved repo file or None."""
    repo_root = ctx.repo_path.resolve() if ctx.repo_path else None
    importer_rel, importer_dir = _normalise_importer(importer_path, ctx)

    # Drop stdlib system includes outright — no node, no edge.
    if _is_system_include(module_path, system_form):
        return None

    # 1. compile_commands include dirs (absolute on-disk paths)
    for inc_dir in ctx.extract_include_dirs(importer_path):
        candidate = (Path(inc_dir) / module_path).resolve()
        if repo_root:
            try:
                rel = candidate.relative_to(repo_root).as_posix()
                if rel in ctx.path_set:
                    return rel
            except ValueError:
                pass

    # Lazy import to avoid pulling the workspace index on every C file
    # parse if it isn't relevant (the warmup builds it eagerly when any
    # C/C++ files are present).
    cpp_index = None
    try:
        from .cpp_workspace import get_or_build_cpp_index

        cpp_index = get_or_build_cpp_index(ctx)
    except Exception:
        cpp_index = None

    # 2. Workspace public-header map
    if cpp_index is not None:
        target = cpp_index.public_header_includes.get(module_path)
        if target and target in ctx.path_set:
            return target

    # 3. Per-target include-search-roots for the importer's owning targets
    if cpp_index is not None:
        owning = cpp_index.file_to_targets.get(importer_rel, ())
        for tid in owning:
            search_roots = cpp_index.target_include_search_dirs.get(tid, ())
            for root in search_roots:
                joined = posixpath.normpath(posixpath.join(root, module_path)) if root else module_path
                if joined in ctx.path_set:
                    return joined

    # 4. Importer-directory relative
    candidate_rel = posixpath.normpath(posixpath.join(importer_dir, module_path))
    if candidate_rel in ctx.path_set:
        return candidate_rel

    # 6. Stem fallback (5 was system-include filter above)
    stem = Path(module_path).stem.lower()
    return ctx.stem_lookup(stem)


def resolve_cpp_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a C/C++ ``#include`` to a repo-relative file path."""
    # The parser strips ``< >`` / ``" "`` wrappers before passing
    # ``module_path``; we treat any path that lacks an absolute prefix as
    # a quoted include and let the importer-relative join catch it. The
    # stdlib filter is conservative — bare names like ``vector`` resolve
    # to None which is what we want.
    # Heuristic: if the raw text never collides with a repo file in
    # ``ctx.path_set`` AND it matches a stdlib name, treat as system.
    system_form = False
    if module_path:
        from .cpp_workspace import is_stdlib_include

        # Only treat as system when the path looks unambiguous (bare
        # stem like ``vector`` or canonical ``stdio.h``) — quoted-form
        # ``"vector"`` could in theory be a repo file, but in practice
        # repos don't ship ``./vector`` next to a C++ TU.
        if is_stdlib_include(module_path):
            system_form = True
    return _resolve_single(module_path, importer_path, ctx, system_form=system_form)


def resolve_cpp_import_all(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> tuple[str, ...]:
    """Resolve a ``#include`` and fan out across sibling TUs in shared targets.

    Returns a tuple of resolved repo paths. The primary resolved file is
    always first; sibling TUs follow. Used by :mod:`..graph.builder` so
    the IMPORTS edges from a header propagate to every implementation
    file in the same target — rescuing public-header-only entry points
    from ``unreachable_file`` flags.
    """
    primary = resolve_cpp_import(module_path, importer_path, ctx)
    if primary is None:
        return ()

    # Fan-out only makes sense when the import RESOLVES to a header and
    # the importer is a TU — otherwise we'd be linking ``.cc`` → ``.cc``
    # files that have no source-level relationship.
    if not primary.lower().endswith(_HEADER_EXTS):
        return (primary,)
    importer_rel, _ = _normalise_importer(importer_path, ctx)
    if not importer_rel.lower().endswith(_SOURCE_TU_EXTS):
        return (primary,)

    try:
        from .cpp_workspace import get_or_build_cpp_index

        cpp_index = get_or_build_cpp_index(ctx)
    except Exception:
        return (primary,)

    owning_targets = cpp_index.file_to_targets.get(primary, ())
    if not owning_targets:
        return (primary,)

    out: list[str] = [primary]
    seen: set[str] = {primary, importer_rel}
    for tid in owning_targets:
        t = cpp_index.targets.get(tid)
        if t is None:
            continue
        # Limit fan-out scope: only target a small set of siblings — a
        # single-header library can otherwise pull in dozens of TUs and
        # blow up the import-edge count. Cap at the target's first 32
        # sources (stable order) which is enough for the rescue effect.
        # Header-only targets (fmt-like: public headers, zero sources)
        # fan out across the target's other headers instead — otherwise
        # one included header leaves the rest of the library orphaned.
        pool = t.sources or (t.public_headers + t.private_headers)
        for src in pool[:32]:
            if src in seen:
                continue
            seen.add(src)
            out.append(src)
    return tuple(out)
