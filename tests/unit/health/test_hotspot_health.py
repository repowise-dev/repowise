"""The hotspot-health KPI has one definition (P10).

Four implementations of this number shipped at once and disagreed on all 42
local indexes. Nothing pinned the arithmetic: ``test_scoring.py`` exercised
``compute_kpis`` but never asserted ``kpis["hotspot_health"]``, and the two
surfaces carrying the wrong definition had no tests at all, so swapping them
would have been green.

These tests pin the number itself. ``test_no_hotspot_health_copies.py`` is the
other half — it fails when a *second* implementation appears.
"""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.health.scoring import (
    compute_kpis,
    hotspot_health,
    nloc_weighted_score,
)


@dataclass
class _Metric:
    """The three attributes the KPI reads off a per-file metric row."""

    file_path: str
    score: float
    nloc: int
    maintainability_score: float | None = None
    performance_score: float | None = None


def test_hotspot_health_weights_by_nloc_not_by_file() -> None:
    """A big bad file outweighs a small good one; a plain mean would not."""
    rows = [
        _Metric("big.py", 2.0, 900),
        _Metric("small.py", 9.0, 100),
    ]
    got = hotspot_health(rows, {"big.py", "small.py"})
    # NLOC-weighted: (2.0*900 + 9.0*100) / 1000 == 2.7. The unweighted mean is
    # 5.5, so this assertion fails if the weighting is ever dropped.
    assert got == 2.7


def test_hotspot_health_averages_only_the_hotspot_files() -> None:
    """Files outside the hotspot set contribute nothing, whatever their size."""
    rows = [
        _Metric("hot.py", 3.0, 100),
        _Metric("cold.py", 10.0, 10_000),
    ]
    assert hotspot_health(rows, {"hot.py"}) == 3.0


def test_hotspot_health_is_none_when_the_repo_has_no_hotspots() -> None:
    """No hotspots is a real answer, and it is not a perfect score.

    Averaging an empty set yields 10.0, which would tell a user their hotspots
    are in perfect health when they have none. 11 of 42 local indexes are in
    this state.
    """
    rows = [_Metric("a.py", 4.0, 100)]
    assert hotspot_health(rows, set()) is None


def test_hotspot_health_ignores_hotspot_paths_with_no_metric_row() -> None:
    """A git hotspot that health never scored cannot drag the average."""
    rows = [_Metric("scored.py", 6.0, 100)]
    assert hotspot_health(rows, {"scored.py", "never-scored.py"}) == 6.0


def test_nloc_floor_keeps_a_zero_nloc_file_from_vanishing() -> None:
    """``max(nloc, 1)`` — a 0-NLOC row still counts, at weight 1."""
    rows = [_Metric("empty.py", 0.0, 0), _Metric("real.py", 10.0, 1)]
    assert hotspot_health(rows, {"empty.py", "real.py"}) == 5.0


def test_compute_kpis_floors_the_persisted_kpi_to_ten() -> None:
    """The persisted contract is unchanged: a float, 10.0 when no hotspots.

    ``save_health_snapshot`` writes this into a non-nullable column and the
    trend alerts diff against it, so ``None`` must not reach it. The honest
    ``None`` is what ``hotspot_health`` returns to surfaces that can render it.
    """
    rows = [_Metric("a.py", 4.0, 100)]
    kpis = compute_kpis(rows, set())
    assert kpis["hotspot_health"] == 10.0
    assert hotspot_health(rows, set()) is None


def test_compute_kpis_hotspot_health_matches_the_shared_owner() -> None:
    """The persisted KPI and the surfaces cannot drift apart."""
    rows = [
        _Metric("hot.py", 3.0, 200),
        _Metric("cold.py", 9.0, 100),
    ]
    hotspots = {"hot.py"}
    assert compute_kpis(rows, hotspots)["hotspot_health"] == hotspot_health(rows, hotspots)


def test_hotspot_health_is_not_the_top_quartile_by_nloc() -> None:
    """The definition two surfaces shipped, named so it cannot come back.

    ``get_overview`` and ``repowise status`` both averaged the top 25% of files
    *by NLOC* under a comment claiming it matched the dashboard. That ranks size
    rather than churn, so it answers a different question and read *higher* than
    the real KPI on 31 of 42 local indexes.

    Here the biggest file is not a hotspot and the one hotspot is tiny, so the
    two definitions land far apart: quartile 2.0, owner 9.0.
    """
    rows = [
        _Metric("big-but-calm.py", 2.0, 1000),
        _Metric("small-but-churny.py", 9.0, 10),
    ]

    # What the retired implementation computed, spelled out rather than
    # imported, so deleting it cannot make this test vacuous.
    by_nloc = sorted(rows, key=lambda m: m.nloc, reverse=True)
    top_q = by_nloc[: max(1, len(by_nloc) // 4)]
    quartile = sum(m.score * max(m.nloc, 1) for m in top_q) / sum(
        max(m.nloc, 1) for m in top_q
    )
    assert quartile == 2.0

    assert hotspot_health(rows, {"small-but-churny.py"}) == 9.0


def test_nloc_weighted_score_is_the_one_weighting_helper() -> None:
    """``average_health`` and ``hotspot_health`` share their arithmetic."""
    rows = [_Metric("a.py", 2.0, 300), _Metric("b.py", 6.0, 100)]
    assert nloc_weighted_score(rows) == 3.0
    # The same helper, restricted to a subset, is what hotspot health is.
    assert hotspot_health(rows, {"a.py", "b.py"}) == round(nloc_weighted_score(rows), 2)
