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
