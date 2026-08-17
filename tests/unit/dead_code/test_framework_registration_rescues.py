"""Rescues for symbols a framework or a container reaches, not a caller.

Each case here is a symbol the repository really uses and whose name a search
would correctly report as absent, because the name is the wrong thing to look
for: a template invokes a tag by its registered string, and a C# call site
writes an extension method's name and never its holder class's.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _unused_export_names(graph: nx.DiGraph) -> list[str]:
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False}
    )
    return [f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]


def _django_tag_graph(decorator: str) -> nx.DiGraph:
    return _build_graph(
        nodes={
            "app/templatetags/admin_list.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "result_list_tag",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [decorator],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
    )


def test_django_template_tag_is_not_flagged():
    """``{% result_list cl %}`` is the only caller and it is not Python."""
    for decorator in (
        '@register.tag(name="result_list")',
        "@register.simple_tag",
        "@register.inclusion_tag('admin/list.html')",
        "@register.filter",
    ):
        assert "result_list_tag" not in _unused_export_names(_django_tag_graph(decorator))


def test_an_undecorated_sibling_is_still_flagged():
    """The rescue is the decorator, not the directory."""
    graph = _django_tag_graph("@staticmethod")
    assert "result_list_tag" in _unused_export_names(graph)


def test_suppresswarnings_unused_is_read_as_an_author_statement():
    """``@SuppressWarnings("unused")`` says outright that nothing calls this."""
    graph = _build_graph(
        nodes={
            "src/Provider.java": {
                "language": "java",
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "activate",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ['@SuppressWarnings("unused")'],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "java",
                    },
                ],
            },
        },
    )
    assert "activate" not in _unused_export_names(graph)


def test_a_bare_suppresswarnings_still_flags():
    """The argument is the signal; the annotation alone is worn by everything."""
    graph = _build_graph(
        nodes={
            "src/Provider.java": {
                "language": "java",
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "activate",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ['@SuppressWarnings("rawtypes")'],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "java",
                    },
                ],
            },
        },
    )
    assert "activate" in _unused_export_names(graph)


def _holder_graph(language: str, wire_call: bool) -> nx.DiGraph:
    graph = _build_graph(
        nodes={
            "src/GuardExtensions.cs": {
                "language": language,
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "BasketGuards",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 20,
                        "complexity_estimate": 2,
                        "language": language,
                    },
                ],
            },
            "src/Checkout.cs": {
                "language": language,
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "Checkout",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 9,
                        "complexity_estimate": 1,
                        "language": language,
                    },
                ],
            },
        },
    )
    holder = "src/GuardExtensions.cs::BasketGuards"
    member = "src/GuardExtensions.cs::BasketGuards::EmptyBasketOnCheckout"
    graph.add_node(member, node_type="symbol", file_path="src/GuardExtensions.cs", language=language)
    graph.add_edge(holder, member, edge_type="has_method")
    if wire_call:
        graph.add_edge("src/Checkout.cs::Checkout", member, edge_type="calls")
    return graph


def test_csharp_holder_class_is_rescued_by_its_used_member():
    """A call site writes the extension method's name, never the holder's."""
    assert "BasketGuards" not in _unused_export_names(_holder_graph("csharp", wire_call=True))


def test_a_holder_whose_member_is_also_unused_is_still_flagged():
    """The rescue is a used member, not the presence of members."""
    assert "BasketGuards" in _unused_export_names(_holder_graph("csharp", wire_call=False))


def test_the_container_rescue_is_language_gated():
    """A static holder class is C#'s idiom; ungating it is its own change."""
    assert "BasketGuards" in _unused_export_names(_holder_graph("java", wire_call=True))


def test_a_container_cannot_rescue_itself_from_the_inside():
    """One of its own methods calling another is not a use of the container.

    Without this, any class holding two methods where one calls the other is
    unreportable, because a class's own methods are predecessors of each other.
    """
    graph = _holder_graph("csharp", wire_call=False)
    holder = "src/GuardExtensions.cs::BasketGuards"
    sibling = "src/GuardExtensions.cs::BasketGuards::Round"
    member = "src/GuardExtensions.cs::BasketGuards::EmptyBasketOnCheckout"
    graph.add_node(sibling, node_type="symbol", file_path="src/GuardExtensions.cs", language="csharp")
    graph.add_edge(holder, sibling, edge_type="has_method")
    graph.add_edge(sibling, member, edge_type="calls")

    assert "BasketGuards" in _unused_export_names(graph)


def test_an_annotation_beside_suppresswarnings_does_not_leak_its_argument():
    """``@Named("unused-legacy-bean")`` is not the author declaring anything."""
    graph = _build_graph(
        nodes={
            "src/Provider.java": {
                "language": "java",
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "activate",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ['@SuppressWarnings("rawtypes")\n@Named("unused-legacy-bean")'],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "java",
                    },
                ],
            },
        },
    )
    assert "activate" in _unused_export_names(graph)
