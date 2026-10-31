"""The review queue order: what most needs a person first."""

from __future__ import annotations

from repowise.core.analysis.decisions.review_queue import (
    HOT_FILE_CAP,
    ReviewSignals,
    review_queue_order,
)


def _sig(
    *,
    acceptable: bool = True,
    hot: int = 0,
    staleness: float = 0.0,
    confidence: float = 0.5,
    created_at: str | None = "2026-10-01T00:00:00",
) -> ReviewSignals:
    return ReviewSignals(
        acceptable=acceptable,
        hot_files=hot,
        staleness=staleness,
        confidence=confidence,
        created_at=created_at,
    )


def _order(rows: dict[str, ReviewSignals]) -> list[str]:
    return review_queue_order(rows, lambda name: rows[name])


def test_an_acceptable_record_leads_one_the_contract_would_refuse() -> None:
    assert _order({"blocked": _sig(acceptable=False, hot=3), "ready": _sig()}) == [
        "ready",
        "blocked",
    ]


def test_hotspot_files_lead_scope_changes() -> None:
    assert _order({"stale": _sig(staleness=0.9), "hot": _sig(hot=1)}) == ["hot", "stale"]


def test_hotspot_breadth_stops_counting_past_the_cap() -> None:
    """A record naming everything must not outrank a precise one on size alone."""
    rows = {
        "broad": _sig(hot=HOT_FILE_CAP + 10),
        "precise_and_stale": _sig(hot=HOT_FILE_CAP, staleness=0.5),
    }
    assert _order(rows) == ["precise_and_stale", "broad"]


def test_staleness_then_confidence_then_newest_break_ties() -> None:
    rows = {
        "old": _sig(created_at="2026-01-01T00:00:00"),
        "new": _sig(created_at="2026-10-30T00:00:00"),
        "sure": _sig(confidence=0.9, created_at="2025-01-01T00:00:00"),
        "stale": _sig(staleness=0.4, confidence=0.1),
        "undated": _sig(created_at=None),
    }
    assert _order(rows) == ["stale", "sure", "new", "old", "undated"]
