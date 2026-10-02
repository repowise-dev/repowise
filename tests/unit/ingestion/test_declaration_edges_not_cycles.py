"""Edges that come from a declaration, not a dependency, must not close a cycle.

Two shapes produced phantom break-cycle plans:

* A type name the referencing file declares itself (a nested ``Node`` in two
  sibling Java policies, a Kotlin ``expect``/``actual`` pair, two C# projects
  each with a ``Contributor`` class) was resolved to the *other* file, minting
  edges in both directions between files that never name each other.
* A Rust parent's ``mod child;`` was an ``imports`` edge to the child, so the
  child importing its parent read as a two-file cycle.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.cohesion import MODULE_DECLARATION_HINT


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
        assert not g.has_edge(_PKG + "LruPolicy.java", _PKG + "FifoPolicy.java")
        assert not g.has_edge(_PKG + "FifoPolicy.java", _PKG + "LruPolicy.java")
        assert _cycles(b) == []
        # The nested type is still seen as used by its own file.
        assert "Node" in g.nodes[_PKG + "LruPolicy.java"]["local_type_uses"]

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
        assert b.graph().has_edge(_PKG + "Cache.java", _PKG + "Stats.java")

    def test_a_type_naming_itself_is_not_a_local_use(self, tmp_path: Path) -> None:
        b = _build(
            tmp_path,
            {_PKG + "Chain.java": "package com.acme;\npublic class Chain { Chain next; }\n"},
        )
        assert "Chain" not in (b.graph().nodes[_PKG + "Chain.java"].get("local_type_uses") or ())

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
    def test_same_named_classes_in_two_projects_do_not_link(self, tmp_path: Path) -> None:
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
        assert not g.has_edge(src, tpl)
        assert not g.has_edge(tpl, src)


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

    def test_associated_type_names_do_not_bind_to_a_child_impl(self, tmp_path: Path) -> None:
        # The parent names ``Self::Ok``, ``S::Error`` and ``Ok = Self::Ok``; the
        # child declares an ``impl Error`` block and associated ``type Ok``.
        # None of that is a parent-to-child dependency.
        b = _build(
            tmp_path,
            {
                "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                "src/lib.rs": "pub mod ser;\n",
                "src/ser/mod.rs": (
                    "mod fmt;\n"
                    "pub trait Error {}\n"
                    "pub trait Seq { type Ok; }\n"
                    "pub trait Serializer {\n"
                    "    type Ok;\n    type Error: Error;\n"
                    "    type Seq: Seq<Ok = Self::Ok>;\n"
                    "    fn run<S: Serializer>(s: S) -> Result<S::Ok, S::Error>;\n"
                    "    fn ok(self) -> Self::Ok;\n}\n"
                ),
                "src/ser/fmt.rs": (
                    "use crate::ser::{Error, Seq, Serializer};\n"
                    "pub struct Fmt;\npub struct E;\nimpl Error for E {}\n"
                    "impl Seq for Fmt { type Ok = (); }\n"
                    "impl Serializer for Fmt {\n    type Ok = ();\n    type Error = E;\n"
                    "    type Seq = Fmt;\n"
                    "    fn run<S: Serializer>(s: S) -> Result<S::Ok, S::Error> { todo!() }\n"
                    "    fn ok(self) -> Self::Ok {}\n}\n"
                ),
            },
        )
        edge = b.graph().get_edge_data("src/ser/mod.rs", "src/ser/fmt.rs")
        assert edge is not None
        assert edge.get("hint_source") == MODULE_DECLARATION_HINT, edge
        assert _cycles(b) == []
