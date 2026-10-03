"""Edges that come from a declaration, not a dependency, must not close a cycle.

Two shapes produced phantom break-cycle plans:

* A type name the referencing file declares itself (a nested ``Node`` in two
  sibling Java policies, a Kotlin ``expect``/``actual`` pair, two C# projects
  each with a ``Contributor`` class) was resolved to the *other* file, minting
  edges in both directions between files that never name each other. The
  edges stay (call resolution and dead code read them) but are stamped so the
  cycle views skip them.
* A Rust parent's ``mod child;`` was an ``imports`` edge to the child, so the
  child importing its parent read as a two-file cycle.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.cohesion import MODULE_DECLARATION_HINT, OWN_TYPE_NAME_HINT


def _build(repo: Path, files: dict[str, str]) -> GraphBuilder:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return builder


def _cycles(builder: GraphBuilder) -> list[set[str]]:
    graph = builder.cycle_subgraph()
    return [set(c) for c in nx.strongly_connected_components(graph) if len(c) > 1]


_PKG = "src/main/java/com/acme/"


class TestJvmOwnTypeFirst:
    def test_sibling_nested_types_of_one_name_are_not_a_cycle(self, tmp_path: Path) -> None:
        b = _build(
            tmp_path,
            {
                _PKG + "LruPolicy.java": (
                    "package com.acme;\npublic class LruPolicy {\n"
                    "  Node head;\n  static final class Node { Node next; }\n}\n"
                ),
                _PKG + "FifoPolicy.java": (
                    "package com.acme;\npublic class FifoPolicy {\n"
                    "  Node head;\n  static final class Node { Node next; }\n}\n"
                ),
            },
        )
        g = b.graph()
        for src, dst in (("LruPolicy", "FifoPolicy"), ("FifoPolicy", "LruPolicy")):
            edge = g.get_edge_data(_PKG + src + ".java", _PKG + dst + ".java")
            assert edge["hint_source"] == OWN_TYPE_NAME_HINT, edge
        assert _cycles(b) == []

    def test_real_same_package_mutual_reference_is_still_a_cycle(self, tmp_path: Path) -> None:
        b = _build(
            tmp_path,
            {
                _PKG + "Project.java": "package com.acme;\npublic class Project { User owner; }\n",
                _PKG + "User.java": "package com.acme;\npublic class User { Project project; }\n",
            },
        )
        assert _cycles(b) == [{_PKG + "Project.java", _PKG + "User.java"}]

    def test_own_nested_type_does_not_hide_a_sibling_type(self, tmp_path: Path) -> None:
        b = _build(
            tmp_path,
            {
                _PKG + "Cache.java": (
                    "package com.acme;\npublic class Cache {\n"
                    "  Stats stats;\n  static final class Node {}\n}\n"
                ),
                _PKG + "Stats.java": "package com.acme;\npublic class Stats {}\n",
            },
        )
        edge = b.graph().get_edge_data(_PKG + "Cache.java", _PKG + "Stats.java")
        assert edge is not None and "hint_source" not in edge

    def test_kotlin_expect_and_actual_are_not_a_cycle(self, tmp_path: Path) -> None:
        common = "src/commonMain/kotlin/io/acme/ByteOrder.kt"
        jvm = "src/jvmMain/kotlin/io/acme/ByteOrderJvm.kt"
        b = _build(
            tmp_path,
            {
                common: (
                    "package io.acme\n"
                    "expect class ByteOrder {\n  fun flip(): ByteOrder\n}\n"
                ),
                jvm: (
                    "package io.acme\n"
                    "actual class ByteOrder {\n  actual fun flip(): ByteOrder = this\n}\n"
                ),
            },
        )
        assert _cycles(b) == []


class TestCSharpOwnTypeFirst:
    def test_same_named_classes_in_two_projects_are_not_a_cycle(self, tmp_path: Path) -> None:
        csproj = '<Project Sdk="Microsoft.NET.Sdk"></Project>\n'
        src = "src/Core/Contributor.cs"
        tpl = "template/Core/Contributor.cs"
        body = (
            "namespace {ns};\npublic class Contributor {{\n"
            "  public Contributor(string name) {{}}\n"
            "  public static Contributor Create(Contributor other) => other;\n}}\n"
        )
        b = _build(
            tmp_path,
            {
                "src/Core/Core.csproj": csproj,
                "template/Core/Core.csproj": csproj,
                src: body.format(ns="Acme.Core"),
                tpl: body.format(ns="Template.Core"),
            },
        )
        g = b.graph()
        for a, z in ((src, tpl), (tpl, src)):
            edge = g.get_edge_data(a, z)
            assert edge is None or edge.get("hint_source") == OWN_TYPE_NAME_HINT, edge
        assert _cycles(b) == []


class TestRustModuleDeclaration:
    def test_parent_declaring_a_child_is_not_a_cycle(self, tmp_path: Path) -> None:
        b = _build(
            tmp_path,
            {
                "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                "src/lib.rs": "pub mod ser;\n",
                "src/ser/mod.rs": "mod fmt;\npub trait Serializer {}\n",
                "src/ser/fmt.rs": (
                    "use crate::ser::Serializer;\npub struct Fmt;\nimpl Serializer for Fmt {}\n"
                ),
            },
        )
        edge = b.graph().get_edge_data("src/ser/mod.rs", "src/ser/fmt.rs")
        assert edge is not None, "the declaration edge stays for reachability"
        assert edge.get("hint_source") == MODULE_DECLARATION_HINT
        assert _cycles(b) == []

    def test_a_reexport_from_the_child_withdraws_the_exemption(self, tmp_path: Path) -> None:
        b = _build(
            tmp_path,
            {
                "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                "src/lib.rs": "pub mod ser;\n",
                "src/ser/mod.rs": (
                    "mod impossible;\npub use self::impossible::Impossible;\n"
                    "pub trait Serializer {}\n"
                ),
                "src/ser/impossible.rs": (
                    "use crate::ser::Serializer;\npub struct Impossible;\n"
                    "impl Serializer for Impossible {}\n"
                ),
            },
        )
        edge = b.graph().get_edge_data("src/ser/mod.rs", "src/ser/impossible.rs")
        assert edge is not None
        assert edge.get("hint_source") != MODULE_DECLARATION_HINT
        assert _cycles(b) == [{"src/ser/mod.rs", "src/ser/impossible.rs"}]
