"""The gate that decides when `repowise update` re-scores health in full.

Two independent triggers: the analyzer version moving, and the periodic #728
time cadence. These pin the interaction between them, because the version
trigger deliberately sits *ahead* of the time gate's ``head_ts`` guard.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd.persistence import (
    _full_rescore_interval_days,
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

    @pytest.mark.parametrize("stamp", [None, "", "not-a-number"])
    def test_a_missing_or_unusable_stamp_is_due(self, stamp):
        """"Never re-scored" has to establish the baseline rather than skip it.

        This is why ``init`` stamps: the branch is correct, and a fresh index
        that left it unset walked straight into it (see
        ``test_a_fresh_index_is_not_due_for_a_re_score``).
        """
        state = {"health_analyzer_version": HEALTH_ANALYZER_VERSION}
        if stamp is not None:
            state["last_full_rescore_at"] = stamp
        assert full_rescore_due(state, 1_000_000.0) is True


class TestInitStampsTheCadence:
    """A fresh index must not be re-scored by the update that follows it.

    ``init`` scores every file, then the first ``update`` used to find no
    ``last_full_rescore_at``, read it as "never re-scored", and score every file
    again — about 30s on a 2k-file repo, on every fresh install.
    """

    def test_a_fresh_index_is_not_due_for_a_re_score(self):
        head_ts = 1_000_000.0
        # Exactly what init now writes.
        state = {
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
            "last_full_rescore_at": head_ts,
        }
        assert full_rescore_due(state, head_ts) is False

    def test_the_cadence_still_comes_due_later(self):
        """Stamping starts the clock, it does not stop it."""
        head_ts = 1_000_000.0
        state = {
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
            "last_full_rescore_at": head_ts,
        }
        assert full_rescore_due(state, head_ts + (8 * 86400.0)) is True

    def test_an_analyzer_bump_still_forces_it(self):
        """The stamp must not be able to suppress the version trigger."""
        head_ts = 1_000_000.0
        state = {
            "health_analyzer_version": HEALTH_ANALYZER_VERSION - 1,
            "last_full_rescore_at": head_ts,
        }
        assert full_rescore_due(state, head_ts) is True


@pytest.mark.parametrize(
    "module_name",
    [
        "repowise.cli.commands.init_cmd.command",
        "repowise.cli.commands.init_cmd.persistence",
    ],
)
def test_init_never_stamps_the_cadence_for_a_run_that_scored_nothing(module_name):
    """The stamp says "these rows are current", so it must not outrun the rows.

    The health phase swallows its own failures and returns ``None``
    (``pipeline/phases/analysis.py``), and a ``None`` report persists nothing
    (``pipeline/persist.py``, ``if getattr(result, "health_report", None)``). An
    unconditional stamp would then suppress the first update's re-score, which
    is the only thing that would have written the missing rows, for a repo-week
    and indefinitely on an idle repo, since the cadence is anchored to HEAD's
    committer time rather than wall clock. Both update-side writers already
    stamp only on a re-score that returned True; init has to match.

    Source-level for the same reason ``test_page_tree_wiring`` is: the failure
    is "somebody added a stamp and did not know the rule", the rows only exist
    after a real pipeline run, and an unconditional stamp raises nothing and
    reads as working right up until a health failure makes it permanent.
    """
    import inspect

    lines = inspect.getsource(__import__(module_name, fromlist=["*"])).splitlines()
    assert any("last_full_rescore_at" in line for line in lines), (
        f"{module_name} no longer stamps the cadence at all, so a fresh index is "
        "back to being re-scored by the update right after it"
    )
    sources = [line for line in lines if "= head_commit_ts(" in line]
    assert sources, f"{module_name} no longer resolves a stamp value"
    for line in sources:
        assert "health_report" in line, (
            f"{module_name} resolves the cadence stamp without checking that this "
            f"run produced a health report: {line.strip()}"
        )


def _capture_warnings(monkeypatch) -> list[str]:
    """Collect what ``_full_rescore_interval_days`` warns, via the shared err_console."""
    from repowise.cli import helpers

    printed: list[str] = []
    monkeypatch.setattr(
        helpers.err_console, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )
    return printed


class TestFullRescoreIntervalWarningOnce:
    """#1371: a malformed REPOWISE_FULL_RESCORE_INTERVAL_DAYS must warn once per invocation.

    ``_full_rescore_interval_days`` is consulted once per repo in a workspace
    fan-out, ``repowise watch`` and post-commit hook updates. A naive warn-in-
    except spams one line per repo; the module-level guard collapses it to a
    single warning.
    """

    def test_warns_once_across_many_repos(self, monkeypatch):
        monkeypatch.setenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "not-a-number")
        monkeypatch.setattr(
            "repowise.cli.commands.update_cmd.persistence._FULL_RESCORE_WARNING_SENT", False
        )
        printed = _capture_warnings(monkeypatch)

        # Simulate the per-repo fan-out: many independent reads in one invocation.
        for _ in range(5):
            assert _full_rescore_interval_days() == 7.0

        (line,) = printed
        assert "REPOWISE_FULL_RESCORE_INTERVAL_DAYS" in line
        assert "not-a-number" in line
        assert len(printed) == 1  # not once per repo

    def test_valid_value_stays_silent(self, monkeypatch):
        monkeypatch.setenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "7")
        monkeypatch.setattr(
            "repowise.cli.commands.update_cmd.persistence._FULL_RESCORE_WARNING_SENT", False
        )
        printed = _capture_warnings(monkeypatch)

        assert _full_rescore_interval_days() == 7.0
        assert printed == []

    def test_default_is_unchanged(self, monkeypatch):
        monkeypatch.delenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", raising=False)
        monkeypatch.setattr(
            "repowise.cli.commands.update_cmd.persistence._FULL_RESCORE_WARNING_SENT", False
        )
        assert _full_rescore_interval_days() == 7.0

    def test_warning_survives_valid_reads_after_invalid(self, monkeypatch):
        """A valid read after the invalid one must not re-arm the guard."""
        monkeypatch.setenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "oops")
        monkeypatch.setattr(
            "repowise.cli.commands.update_cmd.persistence._FULL_RESCORE_WARNING_SENT", False
        )
        printed = _capture_warnings(monkeypatch)

        assert _full_rescore_interval_days() == 7.0
        # Valid now: still no re-warning.
        monkeypatch.setenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "7")
        assert _full_rescore_interval_days() == 7.0
        assert len(printed) == 1
