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
6. **Stem fallback** — last-ditch lookup against the global stem map; a
   path-qualified include only lands on a file whose path ends with it.

An include names one file, so it resolves to one file. Build-target
membership never becomes an import edge: an includer of one header does
not depend on every other file of that header's target. Dead-code
reachability reads target membership from stamps the C/C++ warmup puts
on file nodes instead.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from ..languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS
from .context import ResolverContext

_SOURCE_TU_EXTS: tuple[str, ...] = (
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".cppm",
    ".ixx",
    ".mxx",
    # Objective-C is a superset of C, so a ``.m`` / ``.mm`` file is a
    # translation unit for its plain-C symbols too.
    ".m",
    ".mm",
)
# Include fragments sit with the headers: ``#include "math_impl.inl"`` resolves
# by the same rules, and a fragment is never a translation unit of its own.
_HEADER_EXTS: tuple[str, ...] = (
    ".h",
    ".hpp",
    ".hxx",
    ".hh",
    ".h++",
    ".inc",
    *sorted(INCLUDE_FRAGMENT_EXTENSIONS),
)


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
    return _stem_fallback(module_path, importer_dir, ctx)


def _dir_distance(a: str, b: str) -> int:
    """Directory hops between two repo-relative directories."""
    pa = a.split("/") if a else []
    pb = b.split("/") if b else []
    common = 0
    for x, y in zip(pa, pb, strict=False):
        if x != y:
            break
        common += 1
    return len(pa) + len(pb) - 2 * common


def _stem_fallback(module_path: str, importer_dir: str, ctx: ResolverContext) -> str | None:
    """The repo file *module_path* most plausibly names, by file stem alone.

    An include with a directory part (``"storage/x.h"``) names a file whose
    path ends with that written path; when several do, the one nearest the
    importer wins, and when none does there is no edge: a same-named file
    elsewhere is a different header. A bare name keeps the stem rules below.

    The stem map holds every file of every kind, so ``"data/Clump.hpp"``
    finds ``Clump.cpp`` beside ``Clump.hpp``. The exact file name wins; an
    include of a header otherwise takes only another header, never a
    translation unit or a non-C file that happens to share the stem. An
    include with no extension (``<format>``, ``<QString>``) names that exact
    file or nothing in the repo.
    """
    # An angle include can still arrive wrapped (``<core/Logger.hpp>``).
    written = module_path.strip("<>").replace("\\", "/").lower()
    # Leading ``./`` and ``../`` already failed the importer-relative join.
    parts = [p for p in written.split("/") if p]
    while parts and parts[0] in (".", ".."):
        parts.pop(0)
    name = parts[-1] if parts else ""
    candidates = ctx.stem_map.get(Path(name).stem) or ()
    if len(parts) > 1:
        suffix = "/" + "/".join(parts)
        matches = [c for c in candidates if ("/" + c.lower()).endswith(suffix)]
        # ``min`` keeps the stem map's order among equally near files.
        return min(
            matches,
            key=lambda c: _dir_distance(posixpath.dirname(c), importer_dir),
            default=None,
        )
    for candidate in candidates:
        if posixpath.basename(candidate).lower() == name:
            return candidate
    if "." not in name:
        return None
    if name.endswith(_HEADER_EXTS):
        return next((c for c in candidates if c.lower().endswith(_HEADER_EXTS)), None)
    return candidates[0] if candidates else None


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
