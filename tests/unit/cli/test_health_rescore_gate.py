"""The gate that decides when `repowise update` re-scores health in full.

Two independent triggers: the analyzer version moving, and the periodic #728
time cadence. These pin the interaction between them, because the version
trigger deliberately sits *ahead* of the time gate's ``head_ts`` guard.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd.persistence import (
    full_rescore_due,
    health_analyzer_changed,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION


class TestHealthAnalyzerChanged:
    def test_absent_stamp_is_not_a_change(self):
        """A legacy state file must not read as drift.

        Treating absence as a change would re-score every existing install
        once on upgrade, for no stated defect in its rows.
        """
        assert health_analyzer_changed({}) is False

    def test_matching_stamp_is_not_a_change(self):
        assert (
            health_analyzer_changed({"health_analyzer_version": HEALTH_ANALYZER_VERSION}) is False
        )

    @pytest.mark.parametrize("stored", [HEALTH_ANALYZER_VERSION - 1, HEALTH_ANALYZER_VERSION + 1])
    def test_any_different_stamp_is_a_change(self, stored):
        """Not `<`: a downgrade also leaves rows this analyzer did not write."""
        assert health_analyzer_changed({"health_analyzer_version": stored}) is True


class TestFullRescoreDue:
    def test_analyzer_change_fires_without_git(self):
        """The version trigger must clear the ``head_ts`` guard.

        An analyzer change invalidates the stored rows whether or not git is
        readable, so `head_ts=None` — which switches the *time* gate off — must
        not switch this one off with it.
        """
        state = {"health_analyzer_version": HEALTH_ANALYZER_VERSION - 1}
        assert full_rescore_due(state, None) is True

    def test_analyzer_change_fires_inside_the_time_window(self):
        """A fresh time stamp would say "not due"; the version still forces it."""
        head_ts = 1_000_000.0
        state = {
            "last_full_rescore_at": head_ts,  # re-scored this very instant
            "health_analyzer_version": HEALTH_ANALYZER_VERSION - 1,
        }
        assert full_rescore_due(state, head_ts) is True
        # Same state, current analyzer: the time gate governs and says no.
        current = {**state, "health_analyzer_version": HEALTH_ANALYZER_VERSION}
        assert full_rescore_due(current, head_ts) is False

    def test_time_gate_still_governs_when_the_analyzer_is_current(self):
        head_ts = 1_000_000.0
        state = {
            "last_full_rescore_at": head_ts - (8 * 86400.0),
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
        }
        assert full_rescore_due(state, head_ts) is True

    def test_no_git_and_current_analyzer_is_not_due(self):
        """Guards the ordering: the version check returning False must fall
        through to the ``head_ts`` guard rather than short-circuiting True."""
        state = {"health_analyzer_version": HEALTH_ANALYZER_VERSION}
        assert full_rescore_due(state, None) is False
