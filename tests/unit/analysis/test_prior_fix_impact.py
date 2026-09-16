"""The fix record must not let a failed read look like a clean history.

The summary itself is arithmetic, but the states around it are the point: a
change whose fix record could not be read has not been cleared of one, and the
two must never render the same way. These assertions are about that difference
as much as about the counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from repowise.core.analysis.prior_fix_impact import (
    CONCENTRATION_SHARE,
    FixRecord,
    PriorFixImpact,
    parse_old_ranges,
    summarize_prior_fixes,
    unavailable_prior_fixes,
    unsupported_prior_fixes,
)

NOW = datetime(2026, 9, 16, tzinfo=UTC)


def _by_path(impact) -> dict:
    return {f.file_path: f for f in impact.files}


def _fix(sha: str, path: str, ranges=(), *, days_ago: int | None = None) -> FixRecord:
    return FixRecord(
        fix_sha=sha,
        file_path=path,
        old_ranges=tuple(ranges),
        committed_at=None if days_ago is None else NOW - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def test_no_events_is_a_real_answer_not_a_missing_one():
    impact = summarize_prior_fixes([], {"a.py": {1, 2}}, now=NOW)

    assert impact.status == "available"
    assert impact.is_empty
    assert impact.reason is None


def test_an_unread_record_is_not_a_clean_bill():
    impact = unavailable_prior_fixes("the fix record could not be read: disk I/O error")

    assert impact.status == "unavailable"
    assert impact.reason
    # Identical population to the known-empty case, which is exactly why the
    # status has to carry the difference.
    assert impact.is_empty
    assert impact.total_fixes == 0


def test_nothing_to_read_is_distinct_from_a_failed_read():
    assert unsupported_prior_fixes("this index predates the bug-fix record").status == "unsupported"
    assert unavailable_prior_fixes("query failed").status == "unavailable"


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_one_commit_fixing_three_files_is_one_past_fix():
    """Summing per-file rows would report a single commit three times."""
    impact = summarize_prior_fixes(
        [_fix("abc", "a.py"), _fix("abc", "b.py"), _fix("abc", "c.py")],
        {"a.py": {1}, "b.py": {1}, "c.py": {1}},
        now=NOW,
    )

    assert impact.total_fixes == 1
    assert impact.files_with_fixes == 3
    assert [f.fix_count for f in impact.files] == [1, 1, 1]


def test_per_file_counts_stay_per_file():
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py"), _fix("bbb", "a.py"), _fix("ccc", "b.py")],
        {"a.py": {1}, "b.py": {1}},
        now=NOW,
    )

    by_path = _by_path(impact)
    assert by_path["a.py"].fix_count == 2
    assert by_path["b.py"].fix_count == 1
    assert impact.total_fixes == 3


def test_overlap_counts_changed_lines_inside_a_past_range():
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py", [(10, 20)])],
        {"a.py": {9, 10, 15, 20, 21}},
        now=NOW,
    )

    assert impact.files[0].overlapping_lines == 3  # 10, 15, 20


def test_an_event_outside_the_counted_universe_is_ignored():
    """The fix record must not widen what the change is said to touch."""
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py"), _fix("bbb", "excluded.py")],
        {"a.py": {1}},
        now=NOW,
    )

    assert [f.file_path for f in impact.files] == ["a.py"]
    # The commit that only touched an uncounted file is not a past fix here.
    assert impact.total_fixes == 1


def test_share_of_change_is_a_share_of_the_counted_lines():
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py"), _fix("bbb", "b.py")],
        {"a.py": {1, 2, 3}, "b.py": {4}},
        now=NOW,
    )

    by_path = _by_path(impact)
    assert by_path["a.py"].share_of_change == 0.75
    assert by_path["b.py"].share_of_change == 0.25


def test_the_newest_fix_wins_the_age():
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py", days_ago=400), _fix("bbb", "a.py", days_ago=12)],
        {"a.py": {1}},
        now=NOW,
    )

    assert impact.files[0].last_fix_days_ago == 12


def test_a_dateless_event_leaves_the_age_unknown():
    impact = summarize_prior_fixes([_fix("aaa", "a.py")], {"a.py": {1}}, now=NOW)

    assert impact.files[0].last_fix_days_ago is None


def test_a_naive_timestamp_is_read_as_utc_rather_than_dropped():
    record = FixRecord(fix_sha="aaa", file_path="a.py", committed_at=datetime(2026, 9, 6))

    impact = summarize_prior_fixes([record], {"a.py": {1}}, now=NOW)

    assert impact.files[0].last_fix_days_ago == 10


def test_a_future_timestamp_does_not_go_negative():
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py", days_ago=-5)], {"a.py": {1}}, now=NOW
    )

    assert impact.files[0].last_fix_days_ago == 0


# ---------------------------------------------------------------------------
# Ordering and concentration
# ---------------------------------------------------------------------------


def test_files_order_by_overlap_then_count_then_path():
    impact = summarize_prior_fixes(
        [
            _fix("aaa", "b.py"),
            _fix("bbb", "a.py"),
            _fix("ccc", "a.py"),
            _fix("ddd", "c.py", [(1, 5)]),
        ],
        {"a.py": {1}, "b.py": {1}, "c.py": {1}},
        now=NOW,
    )

    assert [f.file_path for f in impact.files] == ["c.py", "a.py", "b.py"]


def test_a_file_carrying_most_of_the_change_is_named():
    impact = summarize_prior_fixes(
        [_fix("aaa", "big.py"), _fix("bbb", "small.py")],
        {"big.py": set(range(1, 10)), "small.py": {1}},
        now=NOW,
    )

    assert impact.dominant_file is not None
    assert impact.dominant_file.file_path == "big.py"


def test_a_spread_out_change_names_no_file():
    impact = summarize_prior_fixes(
        [_fix("aaa", "a.py"), _fix("bbb", "b.py"), _fix("ccc", "c.py")],
        {"a.py": {1}, "b.py": {1}, "c.py": {1}},
        now=NOW,
    )

    # A third each is below the threshold; naming one would overclaim.
    assert max(f.share_of_change for f in impact.files) < CONCENTRATION_SHARE
    assert impact.dominant_file is None


def test_no_files_names_no_dominant_file():
    assert PriorFixImpact().dominant_file is None


# ---------------------------------------------------------------------------
# Stored range parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[[1, 4], [10, 12]]", ((1, 4), (10, 12))),
        ("[]", ()),
        ("", ()),
        (None, ()),
        ("not json", ()),
        ('{"a": 1}', ()),  # not a list
        ("[[1]]", ()),  # wrong arity
        ('[["x", "y"]]', ()),  # not integers
        ("[[4, 1]]", ()),  # a reversed span is corrupt, not backwards
    ],
)
def test_stored_ranges_parse_tolerantly(raw, expected):
    """One malformed row costs that row, never the whole analysis."""
    assert parse_old_ranges(raw) == expected


def test_a_malformed_span_does_not_discard_its_good_neighbours():
    assert parse_old_ranges('[[1, 2], "junk", [5, 6]]') == ((1, 2), (5, 6))
