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

    def test_generic_arity_keeps_types_apart(self, tmp_path: Path) -> None:
        # Policy and Policy<TResult> are two types, each split across files.
        files = {
            "p.cs": "namespace A;\npublic partial class Policy {}\n",
            "p2.cs": "namespace A;\npublic partial class Policy {}\n",
            "t.cs": "namespace A;\npublic partial class Policy<TResult> {}\n",
            "t2.cs": "namespace A;\npublic partial class Policy<TResult>\n{\n}\n",
            "kv.cs": "namespace A;\npublic partial class Policy<TKey, TValue> {}\n",
        }
        for name, text in files.items():
            (tmp_path / name).write_text(text)
        _ns, _types, partials = build_namespace_map([tmp_path / n for n in files])
        names = {k: sorted(p.name for p in v) for k, v in partials.items()}
        assert names == {
            "A.Policy": ["p.cs", "p2.cs"],
            "A.Policy`1": ["t.cs", "t2.cs"],
            "A.Policy`2": ["kv.cs"],
        }


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


def _calls(graph, caller: str) -> dict[str, str]:
    """``{callee id: resolution origin}`` for every call edge out of *caller*."""
    return {
        callee: data.get("resolution_origin")
        for _, callee, data in graph.out_edges(caller, data=True)
        if data.get("edge_type") == "calls"
    }


# Polly's ResiliencePipelineRegistry shape: a private nested type and private
# members declared in one fragment, used only from the other.
_REGISTRY = (
    "namespace Acme;\ninternal sealed partial class Registry\n{\n"
    "    private object _generic;\n"
    "    public object Get<TResult>()\n    {\n"
    "        var created = new GenericRegistry<TResult>(this);\n"
    "        Helper();\n"
    "        this.Other();\n"
    "        return (GenericRegistry<TResult>)_generic;\n"
    "    }\n}\n"
)
_REGISTRY_TRESULT = (
    "namespace Acme;\ninternal sealed partial class Registry\n{\n"
    "    private void Helper() { }\n"
    "    private void Other() { }\n"
    "    private sealed class Spare { }\n"
    "    private sealed class GenericRegistry<TResult>\n    {\n"
    "        public GenericRegistry(Registry owner) { }\n"
    "    }\n}\n"
)


class TestPartialFragmentScope:
    """Every fragment of a partial type is one class scope for call resolution."""

    def test_members_of_a_sibling_fragment_resolve(self, tmp_path: Path) -> None:
        (tmp_path / "Registry.cs").write_text(_REGISTRY)
        (tmp_path / "Registry.TResult.cs").write_text(_REGISTRY_TRESULT)
        calls = _calls(_build(tmp_path), "Registry.cs::Registry::Get")
        assert calls == {
            # ``new GenericRegistry<TResult>(..)`` reaches the constructor.
            "Registry.TResult.cs::GenericRegistry::GenericRegistry": "enclosing_class",
            "Registry.TResult.cs::Registry::Helper": "enclosing_class",
            "Registry.TResult.cs::Registry::Other": "self_scope",
        }

    def test_same_named_member_of_an_unrelated_class_is_not_linked(
        self, tmp_path: Path
    ) -> None:
        # ``Plain`` shares a file with a Registry fragment but is not part of
        # the partial type, so Registry's private Helper is not in its scope.
        # A second Helper elsewhere keeps the repo-wide unique tier out of it.
        (tmp_path / "Registry.cs").write_text(
            "namespace Acme;\ninternal partial class Registry { }\n"
            "internal class Plain\n{\n    public void Run() { Helper(); }\n}\n"
        )
        (tmp_path / "Registry.Helpers.cs").write_text(
            "namespace Acme;\ninternal partial class Registry\n{\n"
            "    private void Helper() { }\n}\n"
        )
        (tmp_path / "Other.cs").write_text(
            "namespace Acme;\ninternal class Other\n{\n    private void Helper() { }\n}\n"
        )
        graph = _build(tmp_path)
        assert _calls(graph, "Registry.cs::Plain::Run") == {}

    def test_own_overload_is_not_rebound_to_a_sibling_fragment(self, tmp_path: Path) -> None:
        # Polly's Policy.Bulkhead shape: an overload calls its own overload set,
        # and another fragment declares a generic variant of the same name.
        (tmp_path / "Policy.cs").write_text(
            "namespace Acme;\npublic partial class Policy\n{\n"
            "    public static object Bulkhead(int n) => Bulkhead(n, 0);\n"
            "    public static object Bulkhead(int n, int q) => null;\n}\n"
        )
        (tmp_path / "Policy.TResult.cs").write_text(
            "namespace Acme;\npublic partial class Policy\n{\n"
            "    public static object Bulkhead<TResult>(int n) => null;\n}\n"
        )
        graph = _build(tmp_path)
        assert "Policy.TResult.cs::Policy::Bulkhead" not in _calls(
            graph, "Policy.cs::Policy::Bulkhead"
        )

    def test_generic_twin_is_not_a_fragment(self, tmp_path: Path) -> None:
        # Polly's Policy / Policy<TResult>: the non-generic type's call must not
        # land on the generic type's same-named member.
        (tmp_path / "Policy.Execute.cs").write_text(
            "namespace Acme;\npublic abstract partial class Policy\n{\n"
            "    public void Execute() { Implementation(); }\n}\n"
        )
        (tmp_path / "Policy.Impl.cs").write_text(
            "namespace Acme;\npublic abstract partial class Policy\n{\n"
            "    protected abstract void Implementation();\n}\n"
        )
        (tmp_path / "Policy.TResult.Impl.cs").write_text(
            "namespace Acme;\npublic abstract partial class Policy<TResult>\n{\n"
            "    protected abstract TResult Implementation();\n}\n"
        )
        (tmp_path / "Policy.TResult.Execute.cs").write_text(
            "namespace Acme;\npublic abstract partial class Policy<TResult>\n{\n"
            "    public TResult Execute() => Implementation();\n}\n"
        )
        graph = _build(tmp_path)
        assert _calls(graph, "Policy.Execute.cs::Policy::Execute") == {
            "Policy.Impl.cs::Policy::Implementation": "enclosing_class"
        }
        assert _calls(graph, "Policy.TResult.Execute.cs::Policy::Execute") == {
            "Policy.TResult.Impl.cs::Policy::Implementation": "enclosing_class"
        }

    def test_construction_of_a_type_is_not_bound_to_a_same_named_method(
        self, tmp_path: Path
    ) -> None:
        # Polly's docs snippets: ``new FaultGenerator()`` builds the library
        # type while a sibling fragment declares a snippet method of that name.
        (tmp_path / "Chaos.Index.cs").write_text(
            "namespace Snippets;\ninternal static partial class Chaos\n{\n"
            "    public static void Use() { var g = new FaultGenerator(); }\n}\n"
        )
        (tmp_path / "Chaos.Fault.cs").write_text(
            "namespace Snippets;\ninternal static partial class Chaos\n{\n"
            "    public static void FaultGenerator() { }\n}\n"
        )
        (tmp_path / "FaultGenerator.cs").write_text(
            "namespace Lib;\npublic sealed class FaultGenerator { }\n"
        )
        graph = _build(tmp_path)
        assert "Chaos.Fault.cs::Chaos::FaultGenerator" not in _calls(
            graph, "Chaos.Index.cs::Chaos::Use"
        )

    def test_sibling_fragment_use_is_not_unused_internal(self, tmp_path: Path) -> None:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind

        (tmp_path / "Registry.cs").write_text(_REGISTRY)
        (tmp_path / "Registry.TResult.cs").write_text(_REGISTRY_TRESULT)
        report = DeadCodeAnalyzer(_build(tmp_path), git_meta_map={}).analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_exports": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        flagged = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL
        }
        # C# methods are not judged by this pass; nested types are.
        assert flagged == {"Spare"}


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

