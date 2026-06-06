"""Canonical ordering of the exported knowledge-graph artifact.

File-traversal and graph-insertion order vary run to run (parallel parsing,
thread-completion order); ``KnowledgeGraphResult.to_dict`` must therefore
emit byte-stable output regardless of the in-memory list order it was
handed. Pinned after the 2026-06 discovery that identical re-runs produced
different knowledge-graph.json bytes on every validation repo.
"""

from __future__ import annotations

from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult


def _result(node_order: list[str], edge_order: list[tuple[str, str]]) -> KnowledgeGraphResult:
    return KnowledgeGraphResult(
        project={"name": "x"},
        nodes=[{"id": n, "type": "file"} for n in node_order],
        edges=[
            {"source": s, "target": t, "type": "imports", "direction": "forward", "weight": 1.0}
            for s, t in edge_order
        ],
        layers=[
            {
                "id": "layer:application",
                "name": "Application",
                "nodeIds": list(node_order),
                "display_order": 0,
                "subGroups": [
                    {"id": "layer:application:root", "name": "(root)", "nodeIds": list(node_order)}
                ],
            }
        ],
        tour=[],
    )


class TestCanonicalExport:
    def test_shuffled_inputs_export_identically(self) -> None:
        a = _result(["file:b", "file:a", "file:c"], [("file:b", "file:a"), ("file:a", "file:c")])
        b = _result(["file:c", "file:b", "file:a"], [("file:a", "file:c"), ("file:b", "file:a")])
        assert a.to_dict() == b.to_dict()

    def test_nodes_sorted_by_id(self) -> None:
        d = _result(["file:b", "file:a"], []).to_dict()
        assert [n["id"] for n in d["nodes"]] == ["file:a", "file:b"]

    def test_edges_sorted_by_source_target_type(self) -> None:
        d = _result(["file:a", "file:b"], [("file:b", "file:a"), ("file:a", "file:b")]).to_dict()
        assert [(e["source"], e["target"]) for e in d["edges"]] == [
            ("file:a", "file:b"),
            ("file:b", "file:a"),
        ]

    def test_layer_node_ids_and_subgroups_sorted(self) -> None:
        d = _result(["file:b", "file:a"], []).to_dict()
        layer = d["layers"][0]
        assert layer["nodeIds"] == ["file:a", "file:b"]
        assert layer["subGroups"][0]["nodeIds"] == ["file:a", "file:b"]

    def test_tour_order_untouched(self) -> None:
        r = _result(["file:a"], [])
        r.tour = [{"order": 1, "target_path": "z"}, {"order": 2, "target_path": "a"}]
        assert [s["target_path"] for s in r.to_dict()["tour"]] == ["z", "a"]
