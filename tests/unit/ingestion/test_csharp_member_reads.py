"""Unit tests for C# member-access read resolution."""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.languages.csharp_member_reads import (
    resolve_csharp_member_reads,
)


def _seed_graph(*paths: str) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in paths:
        g.add_node(p)
    return g


def test_var_new_local_binds_member_read() -> None:
    """``var x = new Order(); ... x.Total`` resolves to Order.cs."""
    graph = _seed_graph("Caller.cs", "Order.cs")
    cs_texts = {
        "Caller.cs": (
            "namespace Acme;\npublic class Caller {\n"
            "  decimal Sum() { var order = new Order(); return order.Total; }\n}"
        ),
        "Order.cs": "namespace Acme;\npublic class Order { public decimal Total { get; } }",
    }
    type_to_file = {"Order": "Order.cs", "Caller": "Caller.cs"}

    added = resolve_csharp_member_reads(graph, cs_texts, type_to_file)

    assert added == 1
    assert graph.has_edge("Caller.cs", "Order.cs")
    edge = graph["Caller.cs"]["Order.cs"]
    assert edge["edge_type"] == "reads"


def test_typed_local_binds_member_read() -> None:
    """``Order o = new(...);`` (target-typed new) also binds."""
    graph = _seed_graph("Caller.cs", "Order.cs")
    cs_texts = {
        "Caller.cs": (
            "namespace Acme;\npublic class Caller {\n"
            "  decimal Sum() { Order o = new(); return o.Total; }\n}"
        ),
        "Order.cs": "namespace Acme;\npublic class Order { public decimal Total { get; } }",
    }
    type_to_file = {"Order": "Order.cs", "Caller": "Caller.cs"}

    resolve_csharp_member_reads(graph, cs_texts, type_to_file)

    assert graph.has_edge("Caller.cs", "Order.cs")


def test_unknown_receiver_no_edge() -> None:
    """An untyped receiver must not produce an edge — would be a guess."""
    graph = _seed_graph("Caller.cs", "Order.cs")
    cs_texts = {
        "Caller.cs": (
            "namespace Acme;\npublic class Caller {\n"
            "  void F(object x) { var y = x; var n = y.SomeProp; }\n}"
        ),
        "Order.cs": "namespace Acme;\npublic class Order { public int SomeProp { get; } }",
    }
    type_to_file = {"Order": "Order.cs"}

    resolve_csharp_member_reads(graph, cs_texts, type_to_file)

    assert not graph.has_edge("Caller.cs", "Order.cs")


def test_existing_edge_preserved() -> None:
    """A pre-existing `imports` edge wins over `reads` (no overwrite)."""
    graph = _seed_graph("Caller.cs", "Order.cs")
    graph.add_edge("Caller.cs", "Order.cs", edge_type="imports", imported_names=["Order"])
    cs_texts = {
        "Caller.cs": (
            "namespace Acme;\npublic class Caller {\n"
            "  decimal Sum() { var o = new Order(); return o.Total; }\n}"
        ),
        "Order.cs": "namespace Acme;\npublic class Order { public decimal Total { get; } }",
    }
    resolve_csharp_member_reads(graph, cs_texts, {"Order": "Order.cs"})

    assert graph["Caller.cs"]["Order.cs"]["edge_type"] == "imports"


def test_self_reads_skipped() -> None:
    """``this.Prop`` on a same-file class doesn't add a self-loop."""
    graph = _seed_graph("Order.cs")
    cs_texts = {
        "Order.cs": (
            "namespace Acme;\npublic class Order {\n"
            "  public decimal Total { get; }\n"
            "  decimal Half() { return this.Total / 2; }\n}"
        ),
    }
    resolve_csharp_member_reads(graph, cs_texts, {"Order": "Order.cs"})

    assert not graph.has_edge("Order.cs", "Order.cs")
