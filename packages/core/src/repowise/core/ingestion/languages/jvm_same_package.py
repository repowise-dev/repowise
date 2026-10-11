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
from typing import TYPE_CHECKING, Any

from ..cohesion import SAME_PACKAGE_HINT
from .scope_scan import FileScope, ScopeTier, collect_source_texts, emit_scope_edges

if TYPE_CHECKING:
    import networkx as nx

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
) -> int:
    """Emit same-package ``imports`` edges for JVM files.

    *texts* maps repo-relative path → source text for every Java/Kotlin
    (and Scala, once routed through the index) file to scan.

    Returns the number of edges added.
    """
    from ..resolvers.jvm_workspace import _JAVA_LANG_TYPES

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
    )


def collect_jvm_source_texts(
    parsed_files: dict[str, Any], source_map: dict[str, bytes] | None = None
) -> dict[str, str]:
    """Text of each parsed JVM file, keyed by repo path."""
    return collect_source_texts(parsed_files, _JVM_LANGUAGES, source_map)
