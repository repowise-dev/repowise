"""The System Map's lean view of the stored system graph."""

from __future__ import annotations

from repowise.core.workspace.system_map_view import lean_system_graph


def _graph() -> dict:
    return {
        "version": 2,
        "generated_at": "t",
        "repo_provenance": {"backend": {"commit": "abc"}},
        "nodes": [
            {"id": "backend", "repo": "backend", "service_path": None, "name": "backend", "group": "core"},
            # Written before the orphan / isolated flags existed.
            {"id": "frontend", "repo": "frontend", "name": "frontend"},
        ],
        "edges": [
            {
                "id": "frontend->backend:http",
                "source": "frontend",
                "target": "backend",
                "kind": "http",
                "match_type": "exact",
                "confidence": 0.9,
                "weight": 3,
                "structural": True,
                # One contract used from two files repeats its id.
                "contract_refs": ["http::GET::/a", "http::GET::/a", "http::GET::/b"],
                "strategy": "exact_path",
            },
            {
                "id": "backend->frontend:co_change",
                "source": "backend",
                "target": "frontend",
                "kind": "co_change",
                "match_type": "inferred",
                "confidence": 0.4,
                "weight": 2,
                "structural": False,
                "contract_refs": ["a.py~b.ts", "c.py~d.ts"],
                "strategy": None,
            },
        ],
        "diagnostics": {"total_links": 1, "orphan_providers": [{"repo": "backend"}]},
    }


def test_drops_diagnostics_and_provenance() -> None:
    lean = lean_system_graph(_graph())
    assert "diagnostics" not in lean
    assert "repo_provenance" not in lean
    assert (lean["version"], lean["generated_at"]) == (2, "t")


def test_structural_edge_carries_a_count_not_its_refs() -> None:
    http = lean_system_graph(_graph())["edges"][0]
    assert "contract_refs" not in http
    assert http["ref_count"] == 2  # distinct ids, as the drawer lists them
    assert (http["weight"], http["strategy"], http["structural"]) == (3, "exact_path", True)


def test_co_change_edge_keeps_its_file_pairs() -> None:
    co = lean_system_graph(_graph())["edges"][1]
    assert co["contract_refs"] == ["a.py~b.ts", "c.py~d.ts"]
    assert co["ref_count"] == 2


def test_nodes_keep_unknown_keys_and_gain_defaults() -> None:
    backend, frontend = lean_system_graph(_graph())["nodes"]
    assert backend["group"] == "core"
    assert frontend["is_isolated"] is False
    assert frontend["service_path"] is None
    assert "group" not in frontend


def test_input_is_not_mutated() -> None:
    graph = _graph()
    lean_system_graph(graph)
    assert graph == _graph()


def test_old_artifact_edges_read_with_defaults() -> None:
    graph = {
        "nodes": [],
        "edges": [
            {"id": "a->b:package", "source": "a", "target": "b", "kind": "package", "contract_refs": None},
        ],
    }
    (edge,) = lean_system_graph(graph)["edges"]
    assert edge["ref_count"] == 0
    assert edge["strategy"] == "manifest"  # as the stored-graph reader backfills it
    assert edge["structural"] is True


def test_empty_graph() -> None:
    assert lean_system_graph({}) == {"version": 1, "generated_at": "", "nodes": [], "edges": []}
