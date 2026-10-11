"""The incremental update path must not wipe persisted entry-point scores.

``persist_graph_nodes(..., ep_scores=None)`` (how the update path calls it)
should *derive* scores from the graph builder rather than writing empty
community-meta, which previously left get_execution_flows / the dashboard
panel permanently empty after the first ``update``.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.execution_flows import ExecutionFlowReport
from repowise.core.pipeline.persist import _derive_entry_point_scores


def _builder_with_report(report) -> SimpleNamespace:
    return SimpleNamespace(execution_flows=lambda: report)


def test_derive_uses_all_candidate_scores():
    report = ExecutionFlowReport(
        total_entry_points_scored=2,
        total_flows=1,
        flows=[],
        entry_point_scores={"a.py::main": 0.9, "a.py::handle": 0.5},
    )
    scores = _derive_entry_point_scores(_builder_with_report(report))
    assert scores == {"a.py::main": 0.9, "a.py::handle": 0.5}


def test_derive_falls_back_to_flow_scores_for_legacy_reports():
    flow = SimpleNamespace(entry_point_id="a.py::main", entry_point_score=0.8)
    report = SimpleNamespace(flows=[flow], entry_point_scores={})
    scores = _derive_entry_point_scores(_builder_with_report(report))
    assert scores == {"a.py::main": 0.8}


def test_derive_is_safe_when_report_missing_or_raises():
    assert _derive_entry_point_scores(_builder_with_report(None)) == {}

    def _boom():
        raise RuntimeError("flow tracing failed")

    assert _derive_entry_point_scores(SimpleNamespace(execution_flows=_boom)) == {}
