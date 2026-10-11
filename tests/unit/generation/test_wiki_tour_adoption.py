"""The wiki guided tour adopts the curated KG tour when one exists.

One tour, every surface: when the indexed knowledge graph went through
curation (marked by ``project.graph_mode``), the page orchestrator must use
its tour verbatim instead of re-deriving a second, divergent ordering from
the raw graph. Without a curated KG it falls back to computing the tour.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.core.generation.kg_context import KnowledgeGraphContext
from repowise.core.generation.page_generator.orchestrate import _GenerationRun


def _write_kg(tmp_path, kg: dict):
    kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
    kg_path.parent.mkdir(parents=True, exist_ok=True)
    kg_path.write_text(json.dumps(kg))
    return kg_path


def _run_stub(kg_ctx) -> SimpleNamespace:
    """The minimal duck-typed surface _compute_ia touches."""
    stub = SimpleNamespace(
        kg_ctx=kg_ctx,
        tour_stops=[],
        layer_order=[],
        parsed_files=[],
        pagerank={},
        sel_file_paths=[],
        sel_infra_paths=[],
        repo_name="test",
    )
    stub._file_import_edges = lambda: []
    return stub


CURATED_TOUR = [
    {"order": 1, "target_path": "README.md", "page_type": "repo_overview",
     "title": "README.md", "depth": 0, "kind": "overview",
     "reason": "Start here for the end-to-end picture before diving into the code.",
     "layer_id": "layer:app"},
    {"order": 2, "target_path": "src/main.py", "page_type": "file_page",
     "title": "main.py", "depth": 1, "kind": "code",
     "reason": "An entry point — execution and imports fan out from here.",
     "layer_id": "layer:app"},
]


def test_curated_tour_adopted_verbatim(tmp_path):
    kg_path = _write_kg(
        tmp_path,
        {
            "project": {"name": "test", "graph_mode": "flow"},
            "nodes": [], "edges": [], "layers": [],
            "tour": CURATED_TOUR,
        },
    )
    run = _run_stub(KnowledgeGraphContext(kg_path))
    _GenerationRun._compute_ia(run)
    assert [s["target_path"] for s in run.tour_stops] == ["README.md", "src/main.py"]
    assert run.tour_stops[1]["reason"].startswith("An entry point")


def test_uncurated_kg_falls_back_to_computed_tour(tmp_path):
    # No graph_mode marker: the KG (and its tour, if any) predate curation —
    # the orchestrator must compute its own tour, not adopt a stale one.
    kg_path = _write_kg(
        tmp_path,
        {
            "project": {"name": "test"},
            "nodes": [], "edges": [], "layers": [],
            "tour": [{"order": 1, "title": "old-style", "description": "x",
                      "nodeIds": []}],
        },
    )
    run = _run_stub(KnowledgeGraphContext(kg_path))
    _GenerationRun._compute_ia(run)
    assert all(s.get("title") != "old-style" for s in run.tour_stops)


def test_no_kg_computes_tour(tmp_path):
    run = _run_stub(KnowledgeGraphContext(None))
    _GenerationRun._compute_ia(run)
    # The fallback build_tour ran: with nothing selected it yields just the
    # overview stop, never an adopted (layer_id-carrying) curated step.
    assert all("layer_id" not in s for s in run.tour_stops)
    assert [s["kind"] for s in run.tour_stops] == ["overview"]
