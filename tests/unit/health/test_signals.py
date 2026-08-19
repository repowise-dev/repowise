"""Tests for ``health/signals.py`` — the per-file signals join.

The assembler is a pure surfacing layer: it must never impute a value, must
normalize change entropy to 0-100, and must report "no signal" (``None``) only
when a source row is genuinely absent.
"""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.health.signals import FileSignals, file_signals


@dataclass
class _Git:
    """Stub mirroring the GitMetadata fields the signals view reads."""

    prior_defect_count: int | None = 0
    change_entropy_pct: float | None = 0.0
    lines_added_90d: int | None = 0
    lines_deleted_90d: int | None = 0
    commit_count_90d: int | None = 0
    age_days: int | None = 0
    primary_owner_name: str | None = None
    primary_owner_commit_pct: float | None = None
    recent_owner_name: str | None = None
    recent_owner_commit_pct: float | None = None


def test_no_git_and_no_degrees_is_all_none():
    s = file_signals(None, None)
    assert isinstance(s, FileSignals)
    assert all(v is None for v in s.__dict__.values())
    assert s.has_any is False


def test_present_git_surfaces_real_values_including_zero():
    git = _Git(
        prior_defect_count=3,
        lines_added_90d=120,
        lines_deleted_90d=40,
        commit_count_90d=7,
        age_days=410,
        primary_owner_name="Ada",
        primary_owner_commit_pct=0.62,
    )
    s = file_signals(git, None)
    assert s.prior_defect_count == 3
    assert s.lines_added_90d == 120
    assert s.lines_deleted_90d == 40
    assert s.commit_count_90d == 7
    assert s.age_days == 410
    assert s.primary_owner_name == "Ada"
    assert s.primary_owner_commit_pct == 0.62
    # No graph node → topology is silent, not zero.
    assert s.in_degree is None
    assert s.out_degree is None
    assert s.has_any is True


def test_zero_prior_defects_is_a_real_signal_not_none():
    # A git-tracked file with no bug-fixes reports 0 (reassuring), not None.
    s = file_signals(_Git(prior_defect_count=0), None)
    assert s.prior_defect_count == 0
    assert s.has_any is True


def test_change_entropy_normalized_to_0_100():
    s = file_signals(_Git(change_entropy_pct=0.734), None)
    assert s.change_entropy_pct == 73.4


def test_change_entropy_none_stays_none():
    s = file_signals(_Git(change_entropy_pct=None), None)
    assert s.change_entropy_pct is None


def test_recent_owner_can_differ_from_primary():
    git = _Git(
        primary_owner_name="Ada",
        primary_owner_commit_pct=0.7,
        recent_owner_name="Grace",
        recent_owner_commit_pct=0.5,
    )
    s = file_signals(git, None)
    assert s.primary_owner_name == "Ada"
    assert s.recent_owner_name == "Grace"


def test_degrees_surfaced_when_node_present():
    s = file_signals(None, {"in_degree": 17, "out_degree": 3})
    assert s.in_degree == 17
    assert s.out_degree == 3
    # No git history → process/people stay silent.
    assert s.prior_defect_count is None
    assert s.primary_owner_name is None
    assert s.has_any is True


def test_old_callers_see_unchanged_fields_when_defect_columns_are_absent():
    """A ``GitMetadata`` stub predating the fix-history columns still works.

    The Protocol is duck-typed and both the MCP and REST paths pass live ORM
    rows, but a pre-upgrade row (or an old stub like ``_Git`` above) simply has
    no ``bug_magnet``. That must read as "no signal", not raise, and must leave
    every field the caller already relied on untouched.
    """
    s = file_signals(_Git(prior_defect_count=4, commit_count_90d=9), None)
    assert s.prior_defect_count == 4
    assert s.commit_count_90d == 9
    assert s.bug_magnet is None
    assert s.last_fix_at is None
    assert s.fix_symbol_counts is None


def test_defect_history_surfaced_when_columns_are_present():
    from datetime import UTC, datetime

    @dataclass
    class _GitWithFixes(_Git):
        bug_magnet: bool = True
        last_fix_at: datetime | None = datetime(2026, 6, 2, tzinfo=UTC)
        fix_symbol_counts_json: str = '{"m.py::save": 4, "m.py::load": 1}'

    s = file_signals(_GitWithFixes(prior_defect_count=5), None)
    assert s.bug_magnet is True
    # Serialized as a string: this dataclass goes through asdict() into MCP
    # responses, which have no datetime encoder.
    assert s.last_fix_at == "2026-06-02T00:00:00+00:00"
    assert s.fix_symbol_counts == {"m.py::save": 4, "m.py::load": 1}


def test_fix_symbol_counts_are_capped_and_bad_json_is_silent():
    from repowise.core.analysis.health.signals import _TOP_FIX_SYMBOLS

    @dataclass
    class _GitJson(_Git):
        fix_symbol_counts_json: str = ""

    many = {f"m.py::s{i}": 20 - i for i in range(12)}
    import json as _json

    s = file_signals(_GitJson(fix_symbol_counts_json=_json.dumps(many)), None)
    assert len(s.fix_symbol_counts) == _TOP_FIX_SYMBOLS
    assert list(s.fix_symbol_counts) == [f"m.py::s{i}" for i in range(_TOP_FIX_SYMBOLS)]

    assert file_signals(_GitJson(fix_symbol_counts_json="not json"), None).fix_symbol_counts is None
    assert file_signals(_GitJson(fix_symbol_counts_json="{}"), None).fix_symbol_counts is None


def test_naive_timestamps_are_serialized_with_an_explicit_utc_offset():
    """The stored column is naive, and a naive ISO string is a real bug on the wire.

    SQLite's DATETIME bind processor drops tzinfo, so `last_fix_at` reads back
    naive even though the rollup wrote it aware-UTC. JS parses a date-time with
    no offset as LOCAL time, so west of UTC a fresh fix lands in the future,
    trips `formatRelativeTimeOrNull`'s future-guard, and the age disappears from
    copy that is contractually required to carry it. Worst for the newest fixes,
    which are the ones worth showing.
    """
    from datetime import datetime

    @dataclass
    class _GitNaive(_Git):
        last_fix_at: datetime | None = datetime(2026, 6, 2, 10, 30)

    s = file_signals(_GitNaive(prior_defect_count=1), None)
    assert s.last_fix_at == "2026-06-02T10:30:00+00:00"
