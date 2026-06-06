"""Scala import resolution with JVM workspace awareness.

Resolves Scala imports through the shared :class:`JvmWorkspaceIndex`
(package → files, FQN lookup, wildcard expansion — the same surface the
Java and Kotlin resolvers use, including cross-language Scala ↔ Java
resolution), with the sbt/Mill project index as a fallback for layouts
the package scan misses.

Handles:
- ``import com.foo.Bar``     → file(s) defining ``Bar`` in package ``com.foo``
- ``import com.foo._``/``.*`` → all files in package ``com.foo``
- ``import com.foo``          → package fan-out (Scala allows package imports)
- ``scala.`` / JDK namespaces → no edge (stdlib)

Brace imports and renames (``{A, B => C}``) are expanded into separate
single-name imports at extraction time, so they arrive here as plain
dotted paths. Implicits/givens resolution is out of scope for static
analysis — ``ScalaDynamicHints`` marks implicit/given declarations so
implicit-heavy files keep a liveness signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .jvm_workspace import get_or_build_jvm_index
from .scala_build import resolve_via_scala_index

if TYPE_CHECKING:
    from .context import ResolverContext

_SCALA_STDLIB_PREFIXES = ("scala.", "java.", "javax.", "jdk.")


def resolve_scala_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve a Scala import to a single representative repo-relative file."""
    targets = resolve_scala_import_all(module_path, importer_path, ctx)
    return targets[0] if targets else None


def resolve_scala_import_all(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a Scala import to all matching repo-relative file paths.

    Wildcards fan out to every file in the package; package imports fan
    out the same way (importing a package is legal Scala and a real
    dependency on its contents).
    """
    if not module_path:
        return ()

    # Stdlib namespaces produce no node (segment-aware: trailing dots).
    if module_path.startswith(_SCALA_STDLIB_PREFIXES):
        return ()

    parts = module_path.split(".")
    local = parts[-1]

    jvm_index = get_or_build_jvm_index(ctx)

    # Wildcard: import com.foo._ / com.foo.* (extraction normalises to *)
    if local in ("*", "_"):
        pkg_fqn = ".".join(parts[:-1])
        if not pkg_fqn:
            return ()
        files = jvm_index.wildcard_expand(pkg_fqn)
        if files:
            return files
        return (ctx.add_external_node(module_path),)

    # Exact FQN: package + top-level type (class/trait/object/case class)
    files = jvm_index.files_for_fqn(module_path)
    if files:
        return files

    # Package import: import com.foo.bar → depend on the package's files
    pkg_files = jvm_index.files_for_package(module_path)
    if pkg_files:
        return pkg_files

    # sbt/Mill project index (source-root aware) — catches layouts whose
    # package declarations the workspace scan missed.
    build_match = resolve_via_scala_index(module_path, ctx)
    if build_match is not None:
        return (build_match,)

    # Stem lookup on the type/object name
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".scala"):
        return (result,)

    # Package path as directory structure
    if len(parts) > 1:
        dir_suffix = "/".join(parts[:-1])
        for p in ctx.sorted_paths:
            if p.endswith(".scala") and dir_suffix.lower() in p.lower():
                stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if stem.lower() == local.lower():
                    return (p,)

    return (ctx.add_external_node(module_path),)
