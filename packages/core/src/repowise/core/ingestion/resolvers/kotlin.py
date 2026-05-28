"""Kotlin import resolution with JVM workspace awareness.

Resolves Kotlin imports using the shared JVM workspace index for
package-aware cross-language resolution (Kotlin ↔ Java), fan-out to
package siblings, and wildcard imports.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .jvm_workspace import get_or_build_jvm_index
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

    # Filter java.lang.* builtins
    if jvm_index.is_java_lang(module_path):
        return ()

    # Handle wildcard import: import com.foo.*
    if local == "*":
        pkg_fqn = ".".join(parts[:-1])
        if pkg_fqn:
            files = jvm_index.wildcard_expand(pkg_fqn)
            if files:
                return files
        return (ctx.add_external_node(module_path),) if module_path else ()

    # Try JVM workspace index first (handles cross-language Java ↔ Kotlin)
    files = jvm_index.files_for_fqn(module_path)
    if files:
        return files

    # Gradle-aware resolution (settings.gradle subprojects)
    gradle_match = resolve_via_kotlin_index(module_path, ctx)
    if gradle_match is not None:
        return (gradle_match,)

    # Try stem lookup on the class/function name
    result = ctx.stem_lookup(local.lower())
    if result and (result.endswith(".kt") or result.endswith(".kts") or result.endswith(".java")):
        return (result,)

    # Try matching the package path as a directory structure
    if len(parts) > 1:
        dir_suffix = "/".join(parts[:-1])
        for p in ctx.path_set:
            if (p.endswith(".kt") or p.endswith(".java")) and dir_suffix in p:
                stem = PurePosixPath(p).stem
                if stem.lower() == local.lower():
                    return (p,)

    return (ctx.add_external_node(module_path),)
