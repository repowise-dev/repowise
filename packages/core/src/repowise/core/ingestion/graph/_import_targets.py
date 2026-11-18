"""Every file one import resolves to, by the importer's language.

Shared by :meth:`GraphBuilder.build`, which draws the import edges, and by the
incremental patch planner, which re-resolves unchanged files' imports to find
the ones an added or removed file re-routes.
"""

from __future__ import annotations

from typing import Any

from ..resolvers import ResolverContext, resolve_import


def resolve_import_targets(
    imp: Any, path: str, language: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """The files (or ``external:`` nodes) *imp* in *path* resolves to, first one primary."""
    # Go and JVM imports name a package *directory*; fan the edge out to every
    # file in the resolved package so sibling files share importers
    # (otherwise they look unreachable).
    if language == "go":
        from ..resolvers.go import resolve_go_import_all

        return tuple(resolve_go_import_all(imp.module_path, path, ctx))
    if language == "java":
        from ..resolvers.java import resolve_java_import_all

        return tuple(resolve_java_import_all(imp.module_path, path, ctx))
    if language == "kotlin":
        from ..resolvers.kotlin import resolve_kotlin_import_all

        return tuple(resolve_kotlin_import_all(imp.module_path, path, ctx))
    if language == "scala":
        from ..resolvers.scala import resolve_scala_import_all

        return tuple(resolve_scala_import_all(imp.module_path, path, ctx))
    if language in ("cpp", "c"):
        # Fan-out across sibling TUs in the same CMake/Bazel target so a public
        # header reached by one ``.cc`` is also marked imported by the other
        # ``.cc`` files in the library — rescues every public-header symbol
        # whose only declared import lives in one sibling without the
        # workspace edge.
        from ..resolvers.cpp import resolve_cpp_import_all

        return tuple(resolve_cpp_import_all(imp.module_path, path, ctx))
    if language == "python":
        # ``from pkg import submodule`` must also edge into the submodule
        # file, not just ``pkg/__init__.py`` (#666).
        from ..resolvers.python import resolve_python_import_all

        return tuple(resolve_python_import_all(imp, path, ctx))
    if language in ("typescript", "javascript"):
        # React Native: ``./Button`` also means ``Button.ios.tsx`` /
        # ``Button.android.tsx`` (Metro platform extensions). A repo with no
        # such files resolves exactly as before (#3603).
        from ..resolvers.typescript import resolve_ts_js_import_all

        return tuple(resolve_ts_js_import_all(imp.module_path, path, ctx))
    if language == "pascal":
        # Same-named platform units: one edge per project variant.
        from ..resolvers.pascal import resolve_pascal_import_all

        return tuple(resolve_pascal_import_all(imp.module_path, path, ctx))
    single = resolve_import(imp.module_path, path, language, ctx)
    return (single,) if single else ()
