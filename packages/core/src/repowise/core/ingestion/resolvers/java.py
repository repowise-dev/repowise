"""Java import resolution with package fan-out and wildcard expansion.

Resolves Java imports to repo-relative file paths using the
:class:`JvmWorkspaceIndex` for package-aware resolution.

Handles:
- ``import com.foo.Bar`` → file(s) defining ``Bar`` in package ``com.foo``
- ``import com.foo.*`` → all files in package ``com.foo``
- ``import static com.foo.Bar.*`` → file(s) defining ``Bar``
- ``java.lang.*`` filtering (no edges for builtin types)
- Cross-language resolution (Java importing Kotlin and vice versa)
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .jvm_gradle import resolve_via_jvm_gradle_index
from .jvm_workspace import get_or_build_jvm_index

if TYPE_CHECKING:
    from .context import ResolverContext


def resolve_java_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve a Java import to a single representative repo-relative file.

    For the graph builder's fan-out path, use :func:`resolve_java_import_all`
    which returns all target files.
    """
    targets = resolve_java_import_all(module_path, importer_path, ctx)
    return targets[0] if targets else None


def resolve_java_import_all(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a Java import to all matching repo-relative file paths.

    Returns a tuple of repo-relative paths (potentially multiple for
    wildcard imports and package fan-out). External packages resolve
    to a single ``external:`` node.
    """
    if not module_path:
        return ()

    jvm_index = get_or_build_jvm_index(ctx)

    # Filter java.lang.* builtins
    if jvm_index.is_java_lang(module_path):
        return ()

    parts = module_path.split(".")

    # Handle wildcard import: import com.foo.*
    if parts[-1] == "*":
        pkg_fqn = ".".join(parts[:-1])
        if not pkg_fqn:
            return ()
        files = jvm_index.wildcard_expand(pkg_fqn)
        if files:
            return files
        # External package
        return (ctx.add_external_node(module_path),)

    # Handle static wildcard: import static com.foo.Bar.*
    # (parsed as "com.foo.Bar.*" by the import extractor, or
    # the static keyword is stripped leaving "com.foo.Bar")
    # Also handles: import static com.foo.Bar.CONSTANT → resolve Bar

    # Try direct FQN resolution first
    files = jvm_index.files_for_fqn(module_path)
    if files:
        return files

    # Try Gradle index (handles cases where workspace index missed something)
    gradle_match = resolve_via_jvm_gradle_index(module_path, ctx)
    if gradle_match is not None:
        return (gradle_match,)

    # Try stem lookup (class name)
    local = parts[-1]
    result = ctx.stem_lookup(local.lower())
    if result and (result.endswith(".java") or result.endswith(".kt")):
        return (result,)

    # Try matching package path as directory structure
    if len(parts) > 1:
        dir_suffix = "/".join(parts[:-1])
        for p in ctx.path_set:
            if (p.endswith(".java") or p.endswith(".kt")) and dir_suffix in p:
                stem = PurePosixPath(p).stem
                if stem.lower() == local.lower():
                    return (p,)

    # External
    return (ctx.add_external_node(module_path),)
