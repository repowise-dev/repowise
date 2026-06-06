"""Unit tests for C# partial-class linking and nested-type resolution."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.resolvers.dotnet.namespace_map import (
    build_namespace_map,
    scan_type_declarations,
)


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


class TestScanTypeDeclarations:
    def test_partial_flag_and_namespace(self) -> None:
        decls = scan_type_declarations(
            "namespace Acme.Models;\npublic partial class Order {\n}\nclass Plain {}\n"
        )
        by_name = {d.name: d for d in decls}
        assert by_name["Order"].is_partial is True
        assert by_name["Order"].fqn == "Acme.Models.Order"
        assert by_name["Plain"].is_partial is False

    def test_nested_type_one_level_qualified(self) -> None:
        decls = scan_type_declarations(
            "namespace A;\npublic class Outer {\n  public class Inner {}\n}\n"
        )
        by_name = {d.name: d for d in decls}
        assert by_name["Inner"].qualified == "Outer.Inner"

    def test_deeper_nesting_collapses_to_immediate_parent(self) -> None:
        # Recorded cut: only one level — Deep qualifies under Inner, not
        # Outer.Inner.Deep.
        decls = scan_type_declarations(
            "class Outer {\n  class Inner {\n    class Deep {}\n  }\n}\n"
        )
        by_name = {d.name: d for d in decls}
        assert by_name["Deep"].qualified == "Inner.Deep"

    def test_sibling_types_do_not_nest(self) -> None:
        decls = scan_type_declarations("class A {}\nclass B {}\n")
        by_name = {d.name: d for d in decls}
        assert by_name["B"].qualified == "B"

    def test_block_namespace_nesting(self) -> None:
        decls = scan_type_declarations(
            "namespace A {\n  class Outer {\n    class Inner {}\n  }\n}\n"
        )
        by_name = {d.name: d for d in decls}
        assert by_name["Inner"].qualified == "Outer.Inner"
        assert by_name["Inner"].fqn == "A.Outer.Inner"


class TestNamespaceMapNestedKeys:
    def test_type_map_carries_qualified_key(self, tmp_path: Path) -> None:
        f = tmp_path / "outer.cs"
        f.write_text("namespace A;\nclass Outer {\n  class Inner {}\n}\n")
        _ns, type_map, _partials = build_namespace_map([f])
        assert f in type_map["Inner"]
        assert f in type_map["Outer.Inner"]

    def test_partial_map_keyed_by_fqn(self, tmp_path: Path) -> None:
        a = tmp_path / "a.cs"
        b = tmp_path / "b.cs"
        a.write_text("namespace A;\npublic partial class Order {}\n")
        b.write_text("namespace A;\npublic partial class Order {}\n")
        _ns, _types, partials = build_namespace_map([a, b])
        assert sorted(partials["A.Order"]) == sorted([a, b])

    def test_same_name_different_namespace_not_merged(self, tmp_path: Path) -> None:
        a = tmp_path / "a.cs"
        b = tmp_path / "b.cs"
        a.write_text("namespace A;\npublic partial class Order {}\n")
        b.write_text("namespace B;\npublic partial class Order {}\n")
        _ns, _types, partials = build_namespace_map([a, b])
        assert len(partials["A.Order"]) == 1
        assert len(partials["B.Order"]) == 1


class TestPartialClassEdges:
    def test_partial_fragments_linked_bidirectionally(self, tmp_path: Path) -> None:
        (tmp_path / "Order.cs").write_text(
            "namespace Acme.Models;\npublic partial class Order {\n"
            "    public string Id { get; set; }\n}\n"
        )
        (tmp_path / "Order.Totals.cs").write_text(
            "namespace Acme.Models;\npublic partial class Order {\n"
            "    public decimal Total { get; set; }\n}\n"
        )
        graph = _build(tmp_path)
        edge_ab = graph.get_edge_data("Order.cs", "Order.Totals.cs")
        edge_ba = graph.get_edge_data("Order.Totals.cs", "Order.cs")
        assert edge_ab and edge_ab["edge_type"] == "imports"
        assert edge_ab["hint_source"] == "partial_class"
        assert edge_ba and edge_ba["hint_source"] == "partial_class"

    def test_non_partial_same_names_not_linked(self, tmp_path: Path) -> None:
        (tmp_path / "a.cs").write_text("namespace A;\npublic class Order {}\n")
        (tmp_path / "b.cs").write_text("namespace B;\npublic class Order {}\n")
        graph = _build(tmp_path)
        assert not graph.has_edge("a.cs", "b.cs")
        assert not graph.has_edge("b.cs", "a.cs")


class TestNestedTypeResolution:
    def test_outer_inner_type_ref_resolves(self, tmp_path: Path) -> None:
        # using A.Models; ... Outer.Inner x; — the qualified reference must
        # land a type_use (or stronger) edge on the declaring file.
        (tmp_path / "Outer.cs").write_text(
            "namespace A.Models;\npublic class Outer {\n"
            "    public class Inner {\n        public int Qty { get; set; }\n    }\n}\n"
        )
        (tmp_path / "Report.cs").write_text(
            "namespace A.App;\nusing A.Models;\npublic class Report {\n"
            "    public Outer.Inner First { get; set; }\n}\n"
        )
        graph = _build(tmp_path)
        edge = graph.get_edge_data("Report.cs", "Outer.cs")
        assert edge is not None
        assert edge["edge_type"] in ("imports", "type_use")

