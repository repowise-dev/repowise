"""Cohesion edges — file links that assert co-membership, not dependency.

Several languages let a file reference a sibling's declarations with no import
statement at all: Go package siblings, JVM same-package classes, C#
same-namespace types and ``partial`` fragments, Swift same-module files, and the
C/C++ header/implementation pair. Resolver passes synthesise file-level edges for
those relationships so reachability, dead-code, and orphan detection see cohesive
code as connected rather than as a field of isolated nodes.

Those edges are right for reachability and wrong for cycle detection. A Go
package is one compilation unit: its files cannot depend on each other, because
there is no edge to cut and no build order to break. Feed the synthesised edges
to a strongly-connected-component pass and every cohesive package becomes a
fabricated import cycle — and the C++ header pair, emitted in both directions by
construction, fabricates a two-file cycle for every ``foo.h`` / ``foo.c`` in the
repo.

So the split is: keep cohesion edges in the graph, where reachability needs them,
and drop them at the cycle boundary only. Every synthesising pass already stamps
``hint_source``; this module turns that stamp into the single predicate both
cycle definitions share. A new language pass joins by adding its hint to
:data:`COHESION_HINTS` — there is no per-language cycle logic to write.
"""

from __future__ import annotations

from typing import Any

#: Hint stamped on an edge between two files of one package / build target.
SAME_PACKAGE_HINT = "same_package"

#: Hint stamped on an edge to a type declared in the file's own namespace
#: (C#, VB.NET, PHP), where the language needs no import to name it.
SAME_NAMESPACE_HINT = "same_namespace"

#: Hint stamped on a PHP edge whose only evidence is a class name inside a
#: comment or docblock (``@param \App\Foo $x``). A docblock type keeps its
#: target reachable, but no code depends on it, so it cannot close a cycle.
DOC_COMMENT_HINT = "doc_comment"

#: Hint stamped on a parent module's edge to a child it declares (Rust
#: ``mod child;``). The declaration places the child in the module tree; it uses
#: nothing from it, so the child importing its parent is not a cycle. Any real
#: import or type use between the same two files withdraws the hint
#: (:func:`withdraw_declaration_hint`).
MODULE_DECLARATION_HINT = "module_declaration"

#: Hint stamped on a Java, Kotlin or C# ``type_use`` edge whose every type name
#: the source file declares itself (two classes that each nest a ``Node``, an
#: ``expect``/``actual`` pair). The language binds those names to the file's own
#: declaration, so the edge depends on nothing in its target.
OWN_TYPE_NAME_HINT = "own_type_name"

#: Languages whose import statement names a *compilation unit* that is exactly a
#: directory, so a fan-out landing in the importer's own directory landed on its
#: siblings — and a unit cannot depend on itself.
#:
#: Membership is narrow on purpose, because the test the builder applies is a
#: directory comparison and that is only sound where package identity *is* the
#: directory:
#:   * ``go`` — the package is the directory, by language rule; the resolver's
#:     own package index is keyed by directory.
#:   * ``java`` — javac requires a source file's package to match its directory.
#:
#: Deliberately excluded, each of which would make the directory test lie:
#:   * ``c`` / ``cpp`` — the fan-out unit is a CMake/Bazel target, not a
#:     directory. In a flat ``src/`` layout ``foo.c -> bar.h`` is a genuine,
#:     cuttable dependency between unrelated translation units. The real C/C++
#:     cohesion case (a header and its implementation) is already stamped
#:     ``header_source_pair`` by its own pass.
#:   * ``kotlin`` / ``scala`` — ``package`` need not match the directory, so two
#:     same-directory files can sit in different packages and an import between
#:     them is a real dependency.
#:   * ``python`` — its ``_all`` fan-out crosses real module boundaries
#:     (``from pkg import submodule``), so a sibling import is a genuine
#:     dependency and a genuine cycle if it closes one.
UNIT_FANOUT_LANGUAGES: frozenset[str] = frozenset({"go", "java"})

#: ``hint_source`` values marking an edge as intra-compilation-unit cohesion.
#:
#: Deliberately excluded: ``spec_mirror`` (an rspec file genuinely depends on its
#: subject) and ``compile_order`` (F# compilation order is a real dependency, and
#: acyclic by construction). Both are directional dependencies, not co-membership.
COHESION_HINTS: frozenset[str] = frozenset(
    {
        SAME_PACKAGE_HINT,  # JVM siblings; Go/JVM/C++ unit fan-out onto siblings
        SAME_NAMESPACE_HINT,  # C# / VB.NET / PHP same-namespace types
        "global_using",  # C# project-wide global usings
        "same_module",  # Swift SPM target siblings
        "partial_class",  # C# fragments of one partial type
        "header_source_pair",  # C/C++ foo.h <-> foo.c
        DOC_COMMENT_HINT,  # PHP name seen only in a comment: not co-membership,
        # but the same split applies (reachability yes, cycle no)
        MODULE_DECLARATION_HINT,  # Rust parent module declaring its child
        OWN_TYPE_NAME_HINT,  # JVM / C# name the source file declares itself
    }
)


def withdraw_declaration_hint(data: dict[str, Any]) -> None:
    """Drop the module-declaration hint once a real reference shares the edge."""
    if data.get("hint_source") == MODULE_DECLARATION_HINT:
        del data["hint_source"]


def is_cohesion_edge(data: Any) -> bool:
    """True if *data* — a graph edge's attribute dict — is a cohesion edge."""
    return data.get("hint_source") in COHESION_HINTS


def cannot_close_cycle(data: Any) -> bool:
    """True if *data* is an edge no runtime import cycle can run through.

    The one predicate both cycle definitions share (``cycle_subgraph`` and
    ``refactoring.graph_signals``): cohesion edges, type-only imports, deferred
    (function-local) imports, and ``dynamic_uses`` hints, which match a type by
    short name rather than resolve a dependency (#2886). The edges stay in the
    graph for reachability and centrality.
    """
    return (
        is_cohesion_edge(data)
        or bool(data.get("type_only"))
        or bool(data.get("deferred"))
        or data.get("edge_type") == "dynamic_uses"
    )
