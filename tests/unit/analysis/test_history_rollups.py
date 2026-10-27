"""Pure better/worse readings: one delta, a change's rollup, a period, rising files."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.history import (
    MIN_SHOWN_ANALYZER_VERSION,
    DeltaStamp,
    FileScoreShift,
    HealthDeltaSummary,
    delta_currency,
    delta_kind,
    matches_delta,
    parse_score_map,
    rising_files,
    rollup_deltas,
    summarize_delta,
    summarize_period,
)


def _row(**over) -> SimpleNamespace:
    """A stored delta row; every count 0 unless given."""
    base = {
        "status": "available",
        "introduced_count": 0,
        "worsened_count": 0,
        "resolved_count": 0,
        "improved_count": 0,
        "moved_count": 0,
    }
    return SimpleNamespace(**{**base, **over})


def _d(introduced=0, worsened=0, resolved=0, status="available", **over) -> HealthDeltaSummary:
    return HealthDeltaSummary(status, introduced, worsened, resolved, **over)


@pytest.mark.parametrize(
    ("better", "worse", "kind"),
    [(0, 0, "neutral"), (2, 0, "improved"), (0, 1, "worsened"), (3, 1, "mixed")],
)
def test_delta_kind_reads_both_sides(better: int, worse: int, kind: str) -> None:
    assert delta_kind(better, worse) == kind


@pytest.mark.parametrize(
    ("kind", "wanted", "passes"),
    [
        ("mixed", "improved", True),
        ("mixed", "worsened", True),
        ("mixed", "mixed", True),
        ("improved", "worsened", False),
        ("neutral", "neutral", True),
        ("mixed", "neutral", False),
        ("worsened", None, True),
    ],
)
def test_improved_and_worsened_filters_include_mixed(kind, wanted, passes) -> None:
    assert matches_delta(kind, wanted) is passes


def test_a_stored_row_reads_as_counts_and_kind() -> None:
    row = _row(status="partial", introduced_count=1, worsened_count=None, resolved_count=4)
    got = summarize_delta(row)
    assert (got.introduced, got.worsened, got.resolved, got.improved, got.moved) == (1, 0, 4, 0, 0)
    assert got.kind == "mixed" and got.files == ()


def test_a_current_row_counts_improved_and_moved_findings() -> None:
    got = summarize_delta(_row(improved_count=3, moved_count=2))
    assert (got.improved, got.moved, got.kind) == (3, 2, "improved")


def test_a_stale_row_reads_improved_and_moved_as_unknown() -> None:
    got = summarize_delta(_row(improved_count=0, moved_count=0, introduced_count=1), stale=True)
    assert (got.improved, got.moved, got.kind) == (None, None, "worsened")


def test_the_improved_count_makes_a_commit_better_once_stored() -> None:
    assert _d(improved=2).kind == "improved"
    assert _d(introduced=1, improved=0).kind == "worsened"


def test_rollup_sums_scanned_commits_and_marks_gaps_partial() -> None:
    rollup = rollup_deltas([_d(introduced=2), None, _d(resolved=3)], ["a.py", "a.py", "b.py"])
    assert rollup is not None and rollup.moved is None
    assert (rollup.introduced, rollup.resolved) == (2, 3)
    assert (rollup.scanned_commits, rollup.unscanned_commits) == (2, 1)
    assert rollup.status == "partial" and rollup.kind == "mixed"
    assert rollup.files_worsened == 2
    assert rollup.files_improved is None and rollup.improved is None


def test_rollup_is_available_only_when_every_commit_compared_cleanly() -> None:
    assert rollup_deltas([_d(), _d()]).status == "available"
    assert rollup_deltas([_d(), _d(status="partial")]).status == "partial"


def test_a_change_with_no_scanned_commit_has_no_rollup() -> None:
    assert rollup_deltas([None, None]) is None
    assert rollup_deltas([]) is None


def test_rollup_reads_per_file_scores_when_stored() -> None:
    first = _d(files=(FileScoreShift("a.py", 5.0, 6.0), FileScoreShift("b.py", 8.0, 7.5)))
    second = _d(files=(FileScoreShift("a.py", 6.0, 5.5), FileScoreShift("c.py", None, 9.0)))
    rollup = rollup_deltas([first, second], ["d.py"])
    # a.py nets +0.5, b.py falls; c.py has no before side and counts for neither.
    assert rollup.files_improved == 1
    assert rollup.files_worsened == 2


def test_period_counts_improvements_and_nets_findings() -> None:
    summary = summarize_period(
        [_d(resolved=2), _d(introduced=1, resolved=1), _d(worsened=3), _d(), None]
    )
    assert (summary.improved, summary.mixed, summary.worsened, summary.neutral) == (1, 1, 1, 1)
    assert (summary.total, summary.unscanned) == (5, 1)
    assert summary.findings_resolved == 3 and summary.findings_introduced == 1
    assert summary.net_findings == 3 - 1 - 3
    assert summary.findings_improved is None


def test_period_of_rollups_reads_the_same_way() -> None:
    summary = summarize_period([rollup_deltas([_d(resolved=1)]), None])
    assert (summary.improved, summary.unscanned, summary.net_findings) == (1, 1, 1)


def test_rising_files_counts_only_touched_files_that_rose() -> None:
    before = {"a.py": 5.0, "b.py": 7.0, "c.py": 4.0, "d.py": 6.0}
    after = {"a.py": 8.0, "b.py": 6.0, "c.py": 4.05, "d.py": 9.0, "e.py": 9.0}
    gains = rising_files(before, after, {"a.py", "b.py", "c.py", "e.py"})
    assert [(g.path, g.gain) for g in gains] == [("a.py", 3.0)]


def test_rising_files_orders_by_gain_then_path() -> None:
    gains = rising_files({"b": 1.0, "a": 1.0, "c": 2.0}, {"b": 3.0, "a": 3.0, "c": 9.0}, "abc")
    assert [g.path for g in gains] == ["c", "a", "b"]


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", '"x"'])
def test_an_unreadable_score_map_is_empty(raw) -> None:
    assert parse_score_map(raw) == {}


def test_a_score_map_skips_values_that_are_not_numbers() -> None:
    assert parse_score_map('{"a.py": 7.5, "b.py": "x", "c.py": 3}') == {"a.py": 7.5, "c.py": 3.0}


def test_a_delta_is_current_only_under_the_current_stamp() -> None:
    current = DeltaStamp(MIN_SHOWN_ANALYZER_VERSION + 1, "rules", 3)

    def row(version: int, rules: str = "rules", perf: int = 3) -> SimpleNamespace:
        return SimpleNamespace(
            analyzer_version=version, rules_fingerprint=rules, performance_model_version=perf
        )

    assert delta_currency(row(current.analyzer_version), current) == "current"
    assert delta_currency(row(current.analyzer_version, rules="edited"), current) == "stale"
    assert delta_currency(row(current.analyzer_version, perf=2), current) == "stale"
    assert delta_currency(row(MIN_SHOWN_ANALYZER_VERSION), current) == "stale"
    assert delta_currency(row(MIN_SHOWN_ANALYZER_VERSION - 1), current) == "absent"


def test_a_stale_reading_keeps_its_direction() -> None:
    got = summarize_delta(_row(introduced_count=2), stale=True)
    assert got.stale and got.kind == "worsened"
