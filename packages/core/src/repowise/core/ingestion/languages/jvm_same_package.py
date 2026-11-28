"""JVM same-package implicit reference resolution.

Background
----------
JVM languages need no import statement for types declared in the same
package — ``class OrderService { Order pending; }`` compiles with zero
imports when ``Order`` lives in a sibling file of the same package. The
import resolvers therefore produce **no edge at all** between the files
of a tightly-coupled package, which makes exactly the most cohesive
parts of a Java/Kotlin codebase look disconnected.

This is the JVM binding of the shared implicit-scope scan
(:mod:`.scope_scan`): that module owns the identifier scan, the
one-declaring-file rule and the edge emission; this one supplies the package
index, the default-import skip list and the JVM shadowing rule.

For each JVM file A in a multi-file package, every capitalized
identifier in A's source is checked against the package's declared
top-level types (from :class:`~..resolvers.jvm_workspace.JvmWorkspaceIndex`).
An edge A → B is emitted only when ALL of:

- the identifier names a top-level type declared in exactly **one**
  package file B (ambiguous names — declared in two or more files —
  produce no edge to anyone; a wrong edge is worse than a missing one);
- B is not A itself, and the type is not also declared in A;
- the identifier is not a ``java.lang`` / Kotlin default-import type
  name (``String``, ``List``, ... — overwhelmingly stdlib references);
- A does not explicitly import the same simple name from elsewhere
  (an explicit import shadows the same-package type in JVM semantics);
- no edge A → B already exists (a real import wins).

Top-level **types only** — Kotlin top-level functions and properties
are deliberately out of scope: lowercase callables collide with local
identifiers far too often for a text-level scan.

Emitted edges are ``imports`` edges carrying
``hint_source="same_package"`` so density metrics can count them
separately and any false positive is diagnosable at the source.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..cohesion import SAME_PACKAGE_HINT
from .scope_scan import FileScope, ScopeTier, collect_source_texts, emit_scope_edges

if TYPE_CHECKING:
    import networkx as nx

    from ...build_units import BuildUnitIndex
    from ..resolvers.jvm_workspace import JvmWorkspaceIndex

_JVM_LANGUAGES = ("java", "kotlin", "scala")

# ``import a.b.C`` / ``import static a.b.C.d`` — collect the simple type
# name so explicitly-imported names never produce a same-package edge.
_IMPORT_LINE_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.MULTILINE)

# Scala brace imports: ``import a.b.{C, D => E}`` — every name inside the
# braces (sources and rename targets alike) shadows same-package types.
_IMPORT_BRACES_RE = re.compile(r"^\s*import\s+[\w.]+\.\{([^}]*)\}", re.MULTILINE)

# Kotlin default-import types (kotlin.*, kotlin.collections.*, kotlin.io.*,
# kotlin.text.*, kotlin.ranges.*, kotlin.sequences.*, kotlin.annotation.*,
# kotlin.jvm.*) — visible without an import in every Kotlin file, exactly
# like java.lang in Java. A same-package type shadowing one of these names
# is legal but vanishingly rare next to genuine stdlib references, so the
# name is skipped wholesale.
_KOTLIN_DEFAULT_TYPES = frozenset({
    "Any", "Nothing", "Unit", "String", "CharSequence",
    "Int", "Long", "Short", "Byte", "Char", "Boolean", "Float", "Double", "Number",
    "Array", "IntArray", "LongArray", "ShortArray", "ByteArray", "CharArray",
    "BooleanArray", "FloatArray", "DoubleArray",
    "List", "MutableList", "Set", "MutableSet", "Map", "MutableMap",
    "Collection", "MutableCollection", "Iterable", "MutableIterable",
    "Iterator", "MutableIterator", "ListIterator", "MutableListIterator",
    "Sequence", "Pair", "Triple", "Result", "Lazy", "Regex",
    "Comparable", "Comparator", "Throwable", "Exception", "Error",
    "RuntimeException", "IllegalArgumentException", "IllegalStateException",
    "IndexOutOfBoundsException", "NullPointerException",
    "UnsupportedOperationException", "NumberFormatException",
    "ClassCastException", "NoSuchElementException", "ConcurrentModificationException",
    "Annotation", "Enum", "Function",
    "IntRange", "LongRange", "CharRange", "ClosedRange",
    "Deprecated", "Suppress", "OptIn", "DslMarker", "PublishedApi",
    "JvmStatic", "JvmField", "JvmName", "JvmOverloads", "JvmInline",
    "Volatile", "Synchronized", "Transient", "Strictfp", "Throws",
    "StringBuilder", "KClass",
})

# Scala Predef / default-import types — visible without an import in every
# Scala file. Names shared with java.lang/Kotlin (String, Exception, …) are
# already covered by those sets; this adds the Scala-specific surface.
_SCALA_DEFAULT_TYPES = frozenset({
    "Option", "Some", "None", "Either", "Left", "Right", "Try", "Success",
    "Failure", "Future", "Promise", "Seq", "IndexedSeq", "LinearSeq", "Vector",
    "Stream", "LazyList", "Nil", "AnyRef", "AnyVal", "BigInt", "BigDecimal",
    "Ordering", "Ordered", "PartialFunction", "Symbol", "Tuple1", "Tuple2",
    "Tuple3", "Range", "App", "Serializable", "Product", "Equals", "Unit",
})


def _shadowed_names(text: str) -> frozenset[str]:
    """Simple names *text* imports explicitly — those shadow a package sibling.

    Wildcard and static-member imports shadow nothing here: neither yields a
    capitalized simple name.
    """
    names = {
        m.group(1).rstrip(".").rsplit(".", 1)[-1]
        for m in _IMPORT_LINE_RE.finditer(text)
    }
    for m in _IMPORT_BRACES_RE.finditer(text):
        names.update(re.findall(r"\w+", m.group(1)))
    return frozenset(names)


def resolve_jvm_same_package_refs(
    graph: nx.DiGraph,
    jvm_index: JvmWorkspaceIndex,
    texts: dict[str, str],
    build_units: BuildUnitIndex | None = None,
) -> int:
    """Emit same-package ``imports`` edges for JVM files.

    *texts* maps repo-relative path → source text for every Java/Kotlin
    (and Scala, once routed through the index) file to scan.

    *build_units*, when given, enforces the build tool's dependency
    directions: a candidate edge whose **declaring** file's unit depends,
    directly or transitively, on the **referencing** file's unit is dropped —
    Gradle forbids that dependency, so the reference cannot be real (a
    package split across modules is one scope to the package index, so a
    KDoc mention alone would otherwise link a library to the app built on
    it). A file with no unit, or a missing index, keeps every edge.

    Returns the number of edges added.
    """
    from ..resolvers.jvm_workspace import _JAVA_LANG_TYPES

    transitive_deps = _transitive_deps_fn(build_units)

    def may_link(referencing: str, declaring: str) -> bool:
        if build_units is None:
            return True
        ref_units = {u.id for u in build_units.units_owning(referencing)}
        if not ref_units:
            return True
        dec_units = {u.id for u in build_units.units_owning(declaring)}
        # Files sharing a build unit always link.
        if ref_units & dec_units:
            return True
        for unit in dec_units:
            for ref in transitive_deps(unit) & ref_units:
                if unit not in transitive_deps(ref):
                    # the declaring module is built on the referrer and the
                    # referrer doesn't depend back on it
                    return False
        return True

    def plan(path: str, text: str) -> FileScope | None:
        pkg_fqn = jvm_index.package_for_file(path)
        if not pkg_fqn:
            return None
        pkg = jvm_index.packages.get(pkg_fqn)
        if pkg is None or len(pkg.files) < 2:
            return None
        # Not deduped: a file declaring a name twice reads as ambiguous.
        # Changing that moves edges, so it needs its own measurement.
        return FileScope(
            tiers=(
                ScopeTier(
                    hint=SAME_PACKAGE_HINT,
                    lookup=lambda ident: pkg.exported_top_level.get(ident, ()),
                ),
            ),
            shadowed=_shadowed_names(text),
        )

    return emit_scope_edges(
        graph,
        texts.items(),
        plan,
        skip_names=_JAVA_LANG_TYPES | _KOTLIN_DEFAULT_TYPES | _SCALA_DEFAULT_TYPES,
        may_link=may_link,
    )


def _transitive_deps_fn(
    build_units: BuildUnitIndex | None,
) -> Callable[[str], frozenset[str]]:
    """Memoized transitive-dependency closure over the build-unit graph."""
    closures: dict[str, frozenset[str]] = {}
    if build_units is None:
        return lambda unit_id: frozenset()

    def closure(unit_id: str) -> frozenset[str]:
        cached = closures.get(unit_id)
        if cached is not None:
            return cached
        # Explicit stack; only *finished* closures are cached, so cycles
        # never leave an incomplete closure behind.
        reached: set[str] = set()
        unit = build_units.unit(unit_id)
        stack = list(unit.deps) if unit else []
        while stack:
            dep = stack.pop()
            if dep in reached:
                continue
            reached.add(dep)
            finished = closures.get(dep)
            if finished is not None:
                reached |= finished
                continue
            dep_unit = build_units.unit(dep)
            if dep_unit:
                stack.extend(dep_unit.deps)
        closures[unit_id] = frozenset(reached)
        return closures[unit_id]

    return closure



def collect_jvm_source_texts(
    parsed_files: dict[str, Any], source_map: dict[str, bytes] | None = None
) -> dict[str, str]:
    """Text of each parsed JVM file, keyed by repo path."""
    return collect_source_texts(parsed_files, _JVM_LANGUAGES, source_map)
