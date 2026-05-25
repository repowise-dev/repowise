"""Tests for KG-informed scoring, selection, and tiering."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.core.generation.selection.scoring import score_file
from repowise.core.generation.page_generator.tiering import partition_file_tiers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed(
    path: str = "src/core.py",
    n_symbols: int = 10,
    is_entry_point: bool = False,
    is_test: bool = False,
    size_bytes: int = 10_000,
    language: str = "python",
) -> Any:
    symbols = [SimpleNamespace(name=f"sym{i}", visibility="public") for i in range(n_symbols)]
    fi = SimpleNamespace(
        path=path,
        language=language,
        is_api_contract=False,
        is_entry_point=is_entry_point,
        is_test=is_test,
        size_bytes=size_bytes,
    )
    return SimpleNamespace(file_info=fi, symbols=symbols)


# ---------------------------------------------------------------------------
# score_file with kg_bonus
# ---------------------------------------------------------------------------


class TestScoreFileKGBonus:
    def test_tour_file_scores_higher(self):
        parsed = _make_parsed()
        base = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
        )
        boosted = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
            kg_bonus=0.30,
        )
        assert boosted > base
        assert boosted - base == pytest.approx(0.30, abs=0.01)

    def test_edge_connector_scores_higher(self):
        parsed = _make_parsed()
        base = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
        )
        boosted = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
            kg_bonus=0.15,
        )
        assert boosted > base

    def test_combined_tour_and_edge_connector(self):
        parsed = _make_parsed()
        base = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
        )
        boosted = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
            kg_bonus=0.45,
        )
        assert boosted - base == pytest.approx(0.45, abs=0.01)

    def test_zero_kg_bonus_no_change(self):
        parsed = _make_parsed()
        score_a = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
        )
        score_b = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
            kg_bonus=0.0,
        )
        assert score_a == score_b

    def test_kg_bonus_before_test_penalty(self):
        parsed = _make_parsed(is_test=True)
        base = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
        )
        boosted = score_file(
            parsed, pagerank=0.5, betweenness=0.0,
            max_pagerank=1.0, max_betweenness=1.0, is_hotspot=False,
            kg_bonus=0.30,
        )
        assert boosted > base


# ---------------------------------------------------------------------------
# partition_file_tiers with kg_file_scores
# ---------------------------------------------------------------------------


class TestTieringWithKGScores:
    def test_tour_file_promoted_to_tier1(self):
        paths = {"tour.py", "regular.py"}
        pagerank = {"tour.py": 0.3, "regular.py": 0.5}
        kg_scores = {"tour.py": 0.30}
        tier1, tier2 = partition_file_tiers(paths, pagerank, tier1_top_n=1, kg_file_scores=kg_scores)
        assert "tour.py" in tier1
        assert "regular.py" in tier2

    def test_no_kg_scores_uses_pagerank_only(self):
        paths = {"a.py", "b.py"}
        pagerank = {"a.py": 0.3, "b.py": 0.5}
        tier1, tier2 = partition_file_tiers(paths, pagerank, tier1_top_n=1)
        assert "b.py" in tier1
        assert "a.py" in tier2

    def test_kg_scores_none(self):
        paths = {"a.py", "b.py"}
        pagerank = {"a.py": 0.3, "b.py": 0.5}
        tier1, tier2 = partition_file_tiers(paths, pagerank, tier1_top_n=1, kg_file_scores=None)
        assert "b.py" in tier1

    def test_all_tier1_ignores_kg_scores(self):
        paths = {"a.py", "b.py"}
        pagerank = {"a.py": 0.3, "b.py": 0.5}
        kg_scores = {"a.py": 1.0}
        tier1, tier2 = partition_file_tiers(paths, pagerank, tier1_top_n=None, kg_file_scores=kg_scores)
        assert tier1 == paths
        assert tier2 == set()

    def test_equal_pagerank_kg_breaks_tie(self):
        paths = {"x.py", "y.py"}
        pagerank = {"x.py": 0.5, "y.py": 0.5}
        kg_scores = {"x.py": 0.15}
        tier1, tier2 = partition_file_tiers(paths, pagerank, tier1_top_n=1, kg_file_scores=kg_scores)
        assert "x.py" in tier1


# ---------------------------------------------------------------------------
# _compute_kg_file_scores
# ---------------------------------------------------------------------------


class TestComputeKGFileScores:
    def test_tour_file_gets_bonus(self, tmp_path):
        from repowise.core.generation.kg_context import KnowledgeGraphContext
        from repowise.core.generation.page_generator.orchestrate import _compute_kg_file_scores

        kg = {
            "nodes": [
                {"id": "file:src/main.py", "filePath": "src/main.py"},
                {"id": "file:src/core.py", "filePath": "src/core.py"},
            ],
            "edges": [
                {"source": "file:src/main.py", "target": "file:src/core.py", "type": "imports"},
            ],
            "layers": [
                {"id": "layer:cli", "name": "CLI", "nodeIds": ["file:src/main.py"]},
                {"id": "layer:core", "name": "Core", "nodeIds": ["file:src/core.py"]},
            ],
            "tour": [
                {"order": 1, "title": "Entry", "description": "Start here",
                 "nodeIds": ["file:src/main.py"]},
            ],
        }
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        (tmp_path / "src" / "core.py").touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir()
        kg_path.write_text(json.dumps(kg))

        ctx = KnowledgeGraphContext(kg_path)
        scores = _compute_kg_file_scores(ctx)
        assert scores["src/main.py"] == pytest.approx(0.30, abs=0.01)

    def test_edge_connector_gets_bonus(self, tmp_path):
        from repowise.core.generation.kg_context import KnowledgeGraphContext
        from repowise.core.generation.page_generator.orchestrate import _compute_kg_file_scores

        kg = {
            "nodes": [
                {"id": "file:src/core.py", "filePath": "src/core.py"},
                {"id": "file:src/api.py", "filePath": "src/api.py"},
            ],
            "edges": [
                {"source": "file:src/api.py", "target": "file:src/core.py", "type": "imports"},
            ],
            "layers": [
                {"id": "layer:core", "name": "Core", "nodeIds": ["file:src/core.py"]},
                {"id": "layer:api", "name": "API", "nodeIds": ["file:src/api.py"]},
            ],
            "tour": [],
        }
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core.py").touch()
        (tmp_path / "src" / "api.py").touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir()
        kg_path.write_text(json.dumps(kg))

        ctx = KnowledgeGraphContext(kg_path)
        scores = _compute_kg_file_scores(ctx)
        assert scores.get("src/core.py", 0.0) == pytest.approx(0.15, abs=0.01)

    def test_unavailable_kg_returns_empty(self):
        from repowise.core.generation.kg_context import KnowledgeGraphContext
        from repowise.core.generation.page_generator.orchestrate import _compute_kg_file_scores

        ctx = KnowledgeGraphContext(None)
        scores = _compute_kg_file_scores(ctx)
        assert scores == {}

    def test_tour_plus_edge_connector(self, tmp_path):
        from repowise.core.generation.kg_context import KnowledgeGraphContext
        from repowise.core.generation.page_generator.orchestrate import _compute_kg_file_scores

        kg = {
            "nodes": [
                {"id": "file:src/core.py", "filePath": "src/core.py"},
                {"id": "file:src/api.py", "filePath": "src/api.py"},
            ],
            "edges": [
                {"source": "file:src/api.py", "target": "file:src/core.py", "type": "imports"},
            ],
            "layers": [
                {"id": "layer:core", "name": "Core", "nodeIds": ["file:src/core.py"]},
                {"id": "layer:api", "name": "API", "nodeIds": ["file:src/api.py"]},
            ],
            "tour": [
                {"order": 1, "title": "Core", "description": "Core logic",
                 "nodeIds": ["file:src/core.py"]},
            ],
        }
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core.py").touch()
        (tmp_path / "src" / "api.py").touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir()
        kg_path.write_text(json.dumps(kg))

        ctx = KnowledgeGraphContext(kg_path)
        scores = _compute_kg_file_scores(ctx)
        assert scores["src/core.py"] == pytest.approx(0.45, abs=0.01)
