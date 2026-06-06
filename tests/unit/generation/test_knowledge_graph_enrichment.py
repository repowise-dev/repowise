"""Tests for LLM-enriched knowledge graph layers and tour generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repowise.core.generation.knowledge_graph import (
    _backfill_summaries,
    _parse_json_response,
    build_deterministic_tour,
    enrich_knowledge_graph,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeKGResult:
    project: dict = field(default_factory=dict)
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    layers: list[dict] = field(default_factory=list)
    tour: list[dict] = field(default_factory=list)
    fingerprint: str = ""


def _make_llm_client(response_content: str = "{}"):
    client = AsyncMock()
    client.generate.return_value = SimpleNamespace(
        content=response_content,
        input_tokens=100,
        output_tokens=50,
    )
    return client


def _make_graph_builder(pagerank: dict[str, float] | None = None):
    builder = MagicMock()
    builder.pagerank.return_value = pagerank or {}
    builder.betweenness_centrality.return_value = {}
    return builder


def _make_repo_structure(entry_points: list[str] | None = None):
    return SimpleNamespace(
        is_monorepo=False,
        entry_points=entry_points or ["src/main.py"],
        total_files=10,
        total_loc=1000,
    )


def _make_kg_skeleton(layers: list[dict] | None = None, nodes: list[dict] | None = None):
    if layers is None:
        layers = [
            {"id": "layer:core", "name": "src/core", "description": "", "nodeIds": ["file:src/core.py", "file:src/utils.py"]},
            {"id": "layer:cli", "name": "cli", "description": "", "nodeIds": ["file:src/main.py"]},
        ]
    if nodes is None:
        nodes = [
            {"id": "file:src/core.py", "type": "file", "filePath": "src/core.py", "summary": ""},
            {"id": "file:src/utils.py", "type": "file", "filePath": "src/utils.py", "summary": ""},
            {"id": "file:src/main.py", "type": "file", "filePath": "src/main.py", "summary": ""},
        ]
    return FakeKGResult(
        layers=layers,
        nodes=nodes,
    )


# ---------------------------------------------------------------------------
# Layer enrichment tests
# ---------------------------------------------------------------------------


class TestEnrichLayers:
    @pytest.mark.asyncio
    async def test_enriches_layer_names(self):
        llm = _make_llm_client(
            '{"layers": [{"id": "layer:core", "name": "Core Pipeline", "description": "Handles data flow"}, '
            '{"id": "layer:cli", "name": "CLI Interface", "description": "Command line entry"}]}'
        )
        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder({"src/core.py": 0.5, "src/main.py": 0.3})
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert result.layers[0]["name"] == "Core Pipeline"
        assert result.layers[0]["description"] == "Handles data flow"
        assert result.layers[1]["name"] == "CLI Interface"

    @pytest.mark.asyncio
    async def test_positional_index_responses_are_ignored(self):
        # Regression: a model answering with list positions instead of layer
        # ids must not rename anything (positional joins once shuffled every
        # layer name when later batches restarted their indices at 0).
        llm = _make_llm_client(
            '{"layers": [{"index": 0, "name": "Wrong Name", "description": "wrong"}, '
            '{"index": 1, "name": "Also Wrong", "description": "wrong"}]}'
        )
        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder()
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert result.layers[0]["name"] == "src/core"  # heuristic preserved
        assert result.layers[1]["name"] == "cli"

    @pytest.mark.asyncio
    async def test_cross_batch_ids_do_not_clobber_other_layers(self):
        # Eight layers → two batches of five and three. Batch 2's response
        # echoes batch 1's ids; those answers must be dropped, not applied.
        layers = [
            {"id": f"layer:l{i}", "name": f"heuristic-{i}", "description": "",
             "nodeIds": [f"file:src/f{i}.py"]}
            for i in range(8)
        ]
        nodes = [
            {"id": f"file:src/f{i}.py", "type": "file", "filePath": f"src/f{i}.py", "summary": ""}
            for i in range(8)
        ]
        responses = [
            # Batch 1 (l0..l4): correct ids.
            '{"layers": [{"id": "layer:l0", "name": "Named Zero", "description": "d0"}]}',
            # Batch 2 (l5..l7): answers with batch-1 ids — all must be skipped.
            '{"layers": [{"id": "layer:l0", "name": "Clobbered", "description": "x"}, '
            '{"id": "layer:l1", "name": "Clobbered", "description": "x"}]}',
            # Tour call.
            '{"tour": []}',
        ]
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            content = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return SimpleNamespace(content=content, input_tokens=10, output_tokens=10)

        llm = AsyncMock()
        llm.generate.side_effect = _side_effect

        skeleton = _make_kg_skeleton(layers=layers, nodes=nodes)
        builder = _make_graph_builder()
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert result.layers[0]["name"] == "Named Zero"
        assert all(layer["name"] != "Clobbered" for layer in result.layers)
        assert result.layers[5]["name"] == "heuristic-5"  # untouched by bad batch

    @pytest.mark.asyncio
    async def test_unknown_id_is_skipped(self):
        llm = _make_llm_client(
            '{"layers": [{"id": "layer:nonexistent", "name": "Ghost", "description": "x"}]}'
        )
        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder()
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert all(layer["name"] != "Ghost" for layer in result.layers)

    @pytest.mark.asyncio
    async def test_falls_back_on_llm_failure(self):
        llm = AsyncMock()
        llm.generate.side_effect = RuntimeError("LLM is down")
        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder()
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert result.layers[0]["name"] == "src/core"  # heuristic label preserved

    @pytest.mark.asyncio
    async def test_falls_back_on_invalid_json(self):
        llm = _make_llm_client("not json at all {{{ broken")
        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder()
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert result.layers[0]["name"] == "src/core"  # heuristic preserved

    @pytest.mark.asyncio
    async def test_empty_layers_no_crash(self):
        llm = _make_llm_client('{"tour": []}')
        skeleton = _make_kg_skeleton(layers=[])
        builder = _make_graph_builder()
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert result.layers == []


# ---------------------------------------------------------------------------
# Tour generation tests
# ---------------------------------------------------------------------------


class TestTourGeneration:
    @pytest.mark.asyncio
    async def test_tour_generated_from_llm(self):
        tour_response = (
            '{"tour": [{"order": 1, "title": "Start Here", '
            '"description": "Begin with the CLI entry point.", '
            '"files": ["src/main.py"]}]}'
        )
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SimpleNamespace(content='{"layers": []}', input_tokens=10, output_tokens=10)
            return SimpleNamespace(content=tour_response, input_tokens=100, output_tokens=50)

        llm = AsyncMock()
        llm.generate.side_effect = _side_effect

        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder({"src/main.py": 0.5})
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert len(result.tour) == 1
        assert result.tour[0]["order"] == 1
        assert result.tour[0]["title"] == "Start Here"
        assert "file:src/main.py" in result.tour[0]["nodeIds"]

    @pytest.mark.asyncio
    async def test_tour_fallback_on_failure(self):
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SimpleNamespace(content='{"layers": []}', input_tokens=10, output_tokens=10)
            raise RuntimeError("Tour generation failed")

        llm = AsyncMock()
        llm.generate.side_effect = _side_effect

        skeleton = _make_kg_skeleton()
        builder = _make_graph_builder({"src/main.py": 0.5, "src/core.py": 0.3})
        repo = _make_repo_structure()

        result = await enrich_knowledge_graph(skeleton, llm, builder, repo, [])
        assert len(result.tour) >= 1  # deterministic fallback


# ---------------------------------------------------------------------------
# Deterministic tour fallback tests
# ---------------------------------------------------------------------------


class TestDeterministicTour:
    def test_basic_tour(self):
        pagerank = {"src/main.py": 0.5, "src/core.py": 0.3, "src/utils.py": 0.1}
        entry_points = ["src/main.py"]
        layers = [
            {"name": "Core", "nodeIds": ["file:src/core.py", "file:src/utils.py"]},
            {"name": "CLI", "nodeIds": ["file:src/main.py"]},
        ]
        tour = build_deterministic_tour(pagerank, entry_points, layers)
        assert len(tour) >= 2
        assert tour[0]["order"] == 1
        assert tour[0]["title"] == "Entry Point"
        assert "file:src/main.py" in tour[0]["nodeIds"]

    def test_no_entry_points(self):
        pagerank = {"src/core.py": 0.5}
        layers = [{"name": "Core", "nodeIds": ["file:src/core.py"]}]
        tour = build_deterministic_tour(pagerank, [], layers)
        assert len(tour) >= 1
        assert tour[0]["title"] == "Core"

    def test_empty_layers(self):
        tour = build_deterministic_tour({}, ["main.py"], [])
        assert len(tour) == 1
        assert tour[0]["title"] == "Entry Point"


# ---------------------------------------------------------------------------
# Summary backfill tests
# ---------------------------------------------------------------------------


class TestSummaryBackfill:
    def test_populates_summaries(self):
        kg = _make_kg_skeleton()
        pages = [
            SimpleNamespace(target_path="src/core.py", summary="Core business logic module"),
        ]
        _backfill_summaries(kg, pages)
        core_node = next(n for n in kg.nodes if n["filePath"] == "src/core.py")
        assert core_node["summary"] == "Core business logic module"

    def test_does_not_overwrite_existing(self):
        kg = _make_kg_skeleton(nodes=[
            {"id": "file:src/core.py", "type": "file", "filePath": "src/core.py", "summary": "Existing summary"},
        ])
        pages = [
            SimpleNamespace(target_path="src/core.py", summary="New summary"),
        ]
        _backfill_summaries(kg, pages)
        assert kg.nodes[0]["summary"] == "Existing summary"

    def test_handles_no_pages(self):
        kg = _make_kg_skeleton()
        _backfill_summaries(kg, [])
        assert all(n["summary"] == "" for n in kg.nodes)


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    def test_plain_json(self):
        result = _parse_json_response('{"layers": []}')
        assert result == {"layers": []}

    def test_markdown_fenced(self):
        result = _parse_json_response('```json\n{"layers": []}\n```')
        assert result == {"layers": []}

    def test_json_with_preamble(self):
        result = _parse_json_response('Here is the result:\n{"layers": []}')
        assert result == {"layers": []}

    def test_invalid_returns_none(self):
        result = _parse_json_response("not json at all")
        assert result is None

    def test_empty_string(self):
        result = _parse_json_response("")
        assert result is None
