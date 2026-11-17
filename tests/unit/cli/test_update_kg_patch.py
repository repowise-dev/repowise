"""A docs update over an unchanged graph shape applies page summaries without a model call."""

from __future__ import annotations

import json
from types import SimpleNamespace

import repowise.core.generation.knowledge_graph as gen_kg
from repowise.cli.commands.update_cmd import command as upd_cmd
from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult


def test_patched_graph_gets_page_summaries_without_layer_naming(monkeypatch):
    async def _no_model(*_a, **_k):
        raise AssertionError("layer naming must not run on a patched graph")

    monkeypatch.setattr(gen_kg, "enrich_knowledge_graph", _no_model)
    kg = KnowledgeGraphResult(
        nodes=[{"id": "file:src/a.py", "filePath": "src/a.py", "summary": "Floor."}],
        layers=[{"id": "layer:core", "name": "Core", "description": "Named."}],
    )
    run = SimpleNamespace(
        knowledge_graph_result=kg, knowledge_graph_shape_unchanged=True, degraded=[], timings=None
    )
    page = SimpleNamespace(target_path="src/a.py", summary="Page summary.", page_type="file_page")

    upd_cmd._enrich_knowledge_graph(run, SimpleNamespace(generated_pages=[page]))

    assert run.knowledge_graph_result.nodes[0]["summary"] == "Page summary."
    assert run.knowledge_graph_result.layers[0]["name"] == "Core"
    assert run.degraded == []


def test_unchanged_shape_with_no_moved_node_still_takes_this_runs_page(monkeypatch, tmp_path):
    """The refresh moved no node, but the changed file's page was rewritten."""

    async def _no_model(*_a, **_k):
        raise AssertionError("layer naming must not run on an unchanged shape")

    monkeypatch.setattr(gen_kg, "enrich_knowledge_graph", _no_model)
    stored = KnowledgeGraphResult(
        nodes=[{"id": "file:src/a.py", "filePath": "src/a.py", "summary": "Floor."}],
    )
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "knowledge-graph.json").write_text(
        json.dumps(stored.to_dict()), encoding="utf-8"
    )
    run = SimpleNamespace(
        knowledge_graph_result=None,
        knowledge_graph_shape_unchanged=True,
        repo_path=tmp_path,
        state={"knowledge_graph": {"fingerprint": "abc"}},
        degraded=[],
        timings=None,
    )
    page = SimpleNamespace(target_path="src/a.py", summary="Page summary.", page_type="file_page")

    upd_cmd._enrich_knowledge_graph(run, SimpleNamespace(generated_pages=[]))
    assert run.knowledge_graph_result is None

    upd_cmd._enrich_knowledge_graph(run, SimpleNamespace(generated_pages=[page]))
    assert run.knowledge_graph_result.nodes[0]["summary"] == "Page summary."
    assert run.knowledge_graph_result.fingerprint == "abc"
