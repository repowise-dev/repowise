"""Kotlin import resolution with JVM workspace awareness.

Resolves Kotlin imports using the shared JVM workspace index for
package-aware cross-language resolution (Kotlin ↔ Java), fan-out to
package siblings, and wildcard imports.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .jvm_gradle import declaration_rank, get_or_build_source_set_graph
from .jvm_workspace import classify_jvm_import, get_or_build_jvm_index
from .kotlin_gradle import resolve_via_kotlin_index

if TYPE_CHECKING:
    from .context import ResolverContext


def resolve_kotlin_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve a Kotlin import to a single representative repo-relative file."""
    targets = resolve_kotlin_import_all(module_path, importer_path, ctx)
    return targets[0] if targets else None


def resolve_kotlin_import_all(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a Kotlin import to all matching repo-relative file paths.

    Fan-out semantics mirror Java: wildcard imports expand to all
    package files; single-class imports resolve across both .kt and
    .java files in the same package.
    """
    if not module_path:
        return ()

    parts = module_path.split(".")
    local = parts[-1]

    jvm_index = get_or_build_jvm_index(ctx)

    # Filter stdlib namespaces (java./javax./jdk./kotlin.) and bare
    # java.lang types
    namespace_class = classify_jvm_import(module_path, kotlin=True)
    if namespace_class == "stdlib" or jvm_index.is_java_lang(module_path):
        return ()

    # Handle wildcard import: import com.foo.*
    if local == "*":
        pkg_fqn = ".".join(parts[:-1])
        if pkg_fqn:
            files = jvm_index.wildcard_expand(pkg_fqn)
            if files:
                return files
            # ``import okio.ByteString.Companion.*`` — the prefix is a
            # type (or nested object), not a package.
            files = jvm_index.files_for_fqn(pkg_fqn) or jvm_index.files_for_member_fqn(pkg_fqn)
            if files:
                return files
        return (ctx.add_external_node(module_path),) if module_path else ()

    # Try JVM workspace index first (handles cross-language Java ↔ Kotlin).
    # Member imports (``import okio.ByteString.Companion.encodeUtf8``) name a
    # function/constant inside a type, so the declaring type resolves them.
    files = jvm_index.files_for_fqn(module_path) or jvm_index.files_for_member_fqn(module_path)
    if files:
        return _visible_first(files, set(parts[1:]), importer_path, ctx)

    # Gradle-aware resolution (settings.gradle subprojects)
    gradle_match = resolve_via_kotlin_index(module_path, ctx)
    if gradle_match is not None:
        return (gradle_match,)

    # Known-external namespaces (e.g. kotlinx.) — exact lookups above get
    # first refusal (the repo may BE that library), but the fuzzy stem /
    # directory fallbacks below must not false-match a local file.
    if namespace_class == "external":
        return (ctx.add_external_node(module_path),)

    # Match on the class/function name alone, but only among the files of the
    # package the import actually names: a repo-wide match discards the
    # package, so a third-party type binds to any same-named repo class.
    if len(parts) > 1:
        scoped = jvm_index.file_in_package_by_stem(".".join(parts[:-1]), local)
        if scoped and (scoped.endswith(".kt") or scoped.endswith(".kts") or scoped.endswith(".java")):
            return (scoped,)
    else:
        # A single-segment import carries no package to check against.
        result = ctx.stem_lookup(local.lower())
        if result and (result.endswith(".kt") or result.endswith(".kts") or result.endswith(".java")):
            return (result,)

    # Try matching the package path as a directory structure
    if len(parts) > 1:
        dir_suffix = "/".join(parts[:-1])
        for p in ctx.sorted_paths:
            if (p.endswith(".kt") or p.endswith(".java")) and dir_suffix in p:
                stem = PurePosixPath(p).stem
                if stem.lower() == local.lower():
                    return (p,)

    return (ctx.add_external_node(module_path),)


def _visible_first(
    files: tuple[str, ...], names: set[str], importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """*files* with the declaration *importer_path* sees most directly first.

    The first file is the one the import binds (``declaration_rank``). A file
    holds the ``expect`` declaration when it declares one of the imported
    path's names ``expect``. When several files declare the name, one in a
    source set the importer cannot see is dropped.
    """
    if len(files) < 2:
        return files
    parsed = ctx.parsed_files or {}
    graph = get_or_build_source_set_graph(ctx)

    def rank(path: str) -> int | None:
        symbols = parsed[path].symbols if path in parsed else ()
        expect = any("expect" in s.modifiers and s.name in names for s in symbols)
        return declaration_rank(importer_path, path, expect, graph)

    ranked = [(r, path) for path in files if (r := rank(path)) is not None]
    return tuple(path for _, path in sorted(ranked, key=lambda pair: pair[0]))
