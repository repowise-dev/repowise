"""Tests for the unified cross-detector ranking (rank.py).

One ranking is imposed over every refactoring type so the surfaces show the
most valuable suggestion first regardless of kind. The key blends recovered
impact, target centrality, and blast radius, weighted by confidence.
"""

from __future__ import annotations

from repowise.core.analysis.health.refactoring import rank_suggestions
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.rank import score


def _sugg(
    rtype: str,
    file_path: str,
    target: str,
    *,
    impact: float = 0.0,
    confidence: str = "medium",
    blast: dict | None = None,
) -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type=rtype,
        file_path=file_path,
        target_symbol=target,
        line_start=None,
        line_end=None,
        plan={},
        evidence={},
        impact_delta=impact,
        effort_bucket="M",
        blast_radius=blast or {},
        confidence=confidence,
    )


def test_higher_impact_ranks_first():
    low = _sugg("extract_class", "a.py", "A", impact=1.0)
    high = _sugg("extract_class", "b.py", "B", impact=5.0)
    ranked = rank_suggestions([low, high])
    assert [s.target_symbol for s in ranked] == ["B", "A"]


def test_centrality_breaks_an_impact_tie():
    leaf = _sugg("extract_class", "leaf.py", "Leaf", impact=2.0)
    hub = _sugg("extract_class", "hub.py", "Hub", impact=2.0)
    ranked = rank_suggestions([leaf, hub], centrality={"hub.py": 50, "leaf.py": 0})
    assert [s.target_symbol for s in ranked] == ["Hub", "Leaf"]


def test_graph_native_zero_impact_still_ranks_via_centrality():
    # A move_method on a very central file outranks a tiny-impact extract on a leaf.
    move = _sugg("move_method", "hub.py", "Hub.m", impact=0.0, blast={"callers": 20})
    tiny = _sugg("extract_class", "leaf.py", "Leaf", impact=0.05)
    ranked = rank_suggestions([tiny, move], centrality={"hub.py": 80, "leaf.py": 0})
    assert ranked[0].target_symbol == "Hub.m"


def test_blast_radius_enrichment_adds_callers():
    s = _sugg("extract_helper", "a.py", "a.py:1-10", blast={"files": ["b.py", "c.py"]})
    rank_suggestions([s], centrality={"b.py": 3, "c.py": 4})
    assert s.blast_radius["callers"] == 7  # 3 + 4 importers ride along


def test_confidence_weights_a_tie():
    med = _sugg("extract_class", "a.py", "A", impact=2.0, confidence="medium")
    high = _sugg("extract_class", "a.py", "B", impact=2.0, confidence="high")
    ranked = rank_suggestions([med, high], centrality={"a.py": 5})
    assert [s.target_symbol for s in ranked] == ["B", "A"]


def test_ordering_is_deterministic():
    items = [
        _sugg("extract_class", "a.py", "A", impact=1.0),
        _sugg("move_method", "a.py", "A.m", blast={"callers": 2}),
        _sugg("break_cycle", "a.py", "cycle", blast={"file_count": 3}),
    ]
    first = [s.target_symbol for s in rank_suggestions(items)]
    second = [s.target_symbol for s in rank_suggestions(list(reversed(items)))]
    assert first == second


def test_score_is_positive_even_with_all_zero_signals():
    s = _sugg("break_cycle", "a.py", "cycle")
    assert score(s, {}) > 0
