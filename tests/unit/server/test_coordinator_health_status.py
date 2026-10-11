"""Unit tests for the coordinator-health status/detail derivation (issue #374).

These pin the pure helpers that turn per-population drift into an
ok/warning/critical status and a human-readable explanation, so the dashboard
reports page-vector and decision-vector problems separately instead of as one
opaque "drift across stores" number.
"""

from __future__ import annotations

from repowise.server.routers.health import _build_detail, _classify_drift, _worst_status


def test_classify_drift_thresholds():
    assert _classify_drift(None) == "ok"
    assert _classify_drift(0.0) == "ok"
    assert _classify_drift(1.0) == "ok"
    assert _classify_drift(3.0) == "warning"
    assert _classify_drift(5.0) == "warning"
    assert _classify_drift(42.0) == "critical"


def test_worst_status_picks_most_severe():
    assert _worst_status("ok", "ok") == "ok"
    assert _worst_status("ok", "warning") == "warning"
    assert _worst_status("warning", "critical") == "critical"
    assert _worst_status("critical", "ok") == "critical"


def test_detail_calls_out_missing_page_vectors():
    detail = _build_detail(
        page_drift_pct=100.0,
        decision_drift_pct=0.0,
        sql_pages=12,
        vector_page_count=0,
        sql_decisions=3,
        vector_decision_count=3,
    )
    assert detail is not None
    assert "No page vectors for 12 wiki pages" in detail
    assert "decision" not in detail.lower()  # decisions are healthy -> not mentioned


def test_detail_reports_missing_decision_vectors_separately():
    detail = _build_detail(
        page_drift_pct=0.0,
        decision_drift_pct=100.0,
        sql_pages=12,
        vector_page_count=12,
        sql_decisions=3,
        vector_decision_count=0,
    )
    assert detail is not None
    assert "No decision vectors for 3 decision records" in detail
    assert "page" not in detail.lower()


def test_detail_in_sync_when_no_drift():
    detail = _build_detail(
        page_drift_pct=0.0,
        decision_drift_pct=0.0,
        sql_pages=12,
        vector_page_count=12,
        sql_decisions=3,
        vector_decision_count=3,
    )
    assert detail == "All populations in sync."


def test_detail_none_when_populations_not_comparable():
    detail = _build_detail(
        page_drift_pct=None,
        decision_drift_pct=None,
        sql_pages=None,
        vector_page_count=None,
        sql_decisions=None,
        vector_decision_count=None,
    )
    assert detail is None
