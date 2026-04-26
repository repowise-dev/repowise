"""Kotlin import resolution."""

from __future__ import annotations

from pathlib import PurePosixPath

from .context import ResolverContext
from .kotlin_gradle import resolve_via_kotlin_index


def resolve_kotlin_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Kotlin import to a repo-relative file path."""
    parts = module_path.split(".")
    local = parts[-1]

    if local == "*":
        return None

    # Gradle-aware resolution: settings.gradle subprojects + per-module
    # sourceSets parsing yields a {package → files} index. Consult it first
    # so multi-module Android/JVM layouts resolve correctly.
    gradle_match = resolve_via_kotlin_index(module_path, ctx)
    if gradle_match is not None:
        return gradle_match

    # Try stem lookup on the class/function name
    result = ctx.stem_lookup(local.lower())
    if result and (result.endswith(".kt") or result.endswith(".kts")):
        return result

    # Try matching the package path as a directory structure
    if len(parts) > 1:
        dir_suffix = "/".join(parts[:-1])
        for p in ctx.path_set:
            if p.endswith(".kt") and dir_suffix in p:
                stem = PurePosixPath(p).stem
                if stem.lower() == local.lower():
                    return p

    return ctx.add_external_node(module_path)
