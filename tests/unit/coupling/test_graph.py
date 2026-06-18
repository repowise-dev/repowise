"""Tests for ``analysis/coupling/graph.py`` -- the repo-wide co-change join.

The assembler is a pure surfacing layer: it deduplicates the symmetric partner
records into undirected edges, enriches nodes from the health metrics, sorts by
strength, caps at a limit while reporting the pre-cap total, and only emits
nodes that participate in a kept edge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from repowise.core.analysis.coupling.graph import (
    CouplingEdge,
    CouplingNode,
    coupling_graph,
)


@dataclass
class _Metric:
    """Stub mirroring the HealthFileMetric fields the graph reads."""

    file_path: str
    score: float = 8.0
    nloc: int = 100
    module: str | None = "core"


@dataclass
class _Git:
    """Stub mirroring the GitMetadata field the graph reads."""

    co_change_partners_json: str = "[]"


def _git(*partners: tuple[str, float, str | None]) -> _Git:
    return _Git(
        co_change_partners_json=json.dumps(
            [
                {"file_path": p, "co_change_count": c, "last_co_change": d}
                for p, c, d in partners
            ]
        )
    )


def test_dedupes_symmetric_partners_into_one_edge() -> None:
    metrics = [_Metric("a.py"), _Metric("b.py")]
    git = {
        "a.py": _git(("b.py", 3.0, "2026-06-01")),
        "b.py": _git(("a.py", 3.0, "2026-06-01")),
    }
    g = coupling_graph(metrics, git)
    assert len(g.edges) == 1
    assert g.edges[0] == CouplingEdge("a.py", "b.py", 3.0, "2026-06-01")
    assert g.total_edges == 1


def test_edges_sorted_by_strength_and_capped_with_total() -> None:
    metrics = [_Metric(p) for p in ("a.py", "b.py", "c.py")]
    git = {
        "a.py": _git(("b.py", 1.0, None), ("c.py", 5.0, None)),
        "b.py": _git(("a.py", 1.0, None), ("c.py", 2.0, None)),
        "c.py": _git(("a.py", 5.0, None), ("b.py", 2.0, None)),
    }
    g = coupling_graph(metrics, git, limit=2)
    assert [e.strength for e in g.edges] == [5.0, 2.0]
    assert g.total_edges == 3  # pre-cap count preserved for the "showing N of M" line


def test_nodes_only_for_files_in_kept_edges_and_enriched() -> None:
    metrics = [
        _Metric("a.py", score=3.0, nloc=200, module="api"),
        _Metric("b.py", score=9.0, nloc=50, module="ui"),
        _Metric("lonely.py", score=7.0),  # has a metric but no coupling
    ]
    git = {
        "a.py": _git(("b.py", 4.0, "2026-05-01")),
        "b.py": _git(("a.py", 4.0, "2026-05-01")),
    }
    g = coupling_graph(metrics, git)
    paths = {n.file_path for n in g.nodes}
    assert paths == {"a.py", "b.py"}  # lonely.py omitted
    by_path = {n.file_path: n for n in g.nodes}
    assert by_path["a.py"] == CouplingNode("a.py", "api", 3.0, 200)
    assert by_path["b.py"] == CouplingNode("b.py", "ui", 9.0, 50)


def test_missing_metric_yields_null_score_module() -> None:
    # A file with git co-change but no health metric (e.g. a config file).
    metrics = [_Metric("a.py")]
    git = {
        "a.py": _git(("config.yaml", 2.0, None)),
        "config.yaml": _git(("a.py", 2.0, None)),
    }
    g = coupling_graph(metrics, git)
    cfg = next(n for n in g.nodes if n.file_path == "config.yaml")
    assert cfg.score is None
    assert cfg.module is None
    assert cfg.nloc == 0


def test_keeps_strongest_and_most_recent_on_asymmetric_records() -> None:
    # Defensive: if the two stored directions disagree, keep max strength and
    # the most recent date.
    metrics = [_Metric("a.py"), _Metric("b.py")]
    git = {
        "a.py": _git(("b.py", 3.0, "2026-01-01")),
        "b.py": _git(("a.py", 5.0, "2026-06-01")),
    }
    g = coupling_graph(metrics, git)
    assert g.edges[0] == CouplingEdge("a.py", "b.py", 5.0, "2026-06-01")


def test_ignores_self_loops_zero_and_bad_json() -> None:
    metrics = [_Metric("a.py"), _Metric("b.py")]
    git = {
        "a.py": _git(("a.py", 9.0, None), ("b.py", 0.0, None)),  # self + zero dropped
        "b.py": _Git(co_change_partners_json="not json"),  # tolerated
    }
    g = coupling_graph(metrics, git)
    assert g.edges == []
    assert g.nodes == []
    assert g.total_edges == 0


def test_empty_inputs() -> None:
    g = coupling_graph([], {})
    assert g.nodes == []
    assert g.edges == []
    assert g.total_edges == 0
