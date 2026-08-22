"""Coverage decay: how much of a measurement still describes the file."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from repowise.core.analysis.health.coverage import (
    STALE_DRIFT_PCT,
    STALE_MIN_MEASURED,
    decay_for_file,
    decay_since,
    measurement_ref,
)

# --- the pure split --------------------------------------------------------


def test_nothing_changed_confirms_everything() -> None:
    d = decay_for_file({1, 2, 3}, set())
    assert (d.measured, d.confirmed, d.invalidated) == (3, 3, 0)
    assert d.drift_pct == 0.0


def test_every_covered_line_changed_invalidates_everything() -> None:
    d = decay_for_file({1, 2, 3}, {1, 2, 3})
    assert (d.measured, d.confirmed, d.invalidated) == (3, 0, 3)
    assert d.drift_pct == 100.0


def test_only_covered_lines_count_as_drift() -> None:
    """A change to a line the report never saw covered is not drift.

    It may well be new uncovered code, but this module reports on the
    *measurement*, and a line outside the covered set was never part of it.
    """
    d = decay_for_file({1, 2}, {2, 50, 51, 52})
    assert (d.confirmed, d.invalidated) == (1, 1)
    assert d.drift_pct == 50.0


def test_no_covered_lines_does_not_divide_by_zero() -> None:
    d = decay_for_file(set(), {1, 2, 3})
    assert (d.measured, d.confirmed, d.invalidated) == (0, 0, 0)
    assert d.drift_pct == 0.0
    assert d.is_stale is False


def test_confirmed_and_invalidated_always_sum_to_measured() -> None:
    d = decay_for_file({1, 4, 9, 16, 25}, {4, 16, 99})
    assert d.confirmed + d.invalidated == d.measured


# --- the staleness convention ----------------------------------------------


def test_small_file_is_never_stale_on_ratio_alone() -> None:
    """A one-line __init__ at 100% drift is noise, not a stale measurement.

    Without the floor this shape led the drift ranking on this repo's own
    index: a version bump is the only covered line, so one edit reads as total
    decay while saying nothing about the file.
    """
    d = decay_for_file({1}, {1})
    assert d.drift_pct == 100.0
    assert d.is_stale is False


def test_stale_once_the_file_is_big_enough_and_drift_clears_the_bar() -> None:
    covered = set(range(1, STALE_MIN_MEASURED + 1))
    changed = set(list(covered)[:3])  # 30% of 10
    d = decay_for_file(covered, changed)
    assert d.measured >= STALE_MIN_MEASURED
    assert d.drift_pct > STALE_DRIFT_PCT
    assert d.is_stale is True


def test_big_file_under_the_bar_is_not_stale() -> None:
    covered = set(range(1, 101))
    d = decay_for_file(covered, {1, 2})  # 2%
    assert d.is_stale is False


# --- end-to-end against a real git repo ------------------------------------


def _git(cwd, *args: str, when: str | None = None) -> str:
    """Run git in *cwd*. *when* pins both dates, since ``rev-list --before``
    filters on the COMMITTER date and ``git commit --date`` only sets the
    author one."""
    env = None
    if when is not None:
        env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    ).stdout


@pytest.fixture
def git_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    (tmp_path / "quiet.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_decay_since_splits_an_edited_file_and_leaves_an_untouched_one(git_repo) -> None:
    base = _git(git_repo, "rev-parse", "HEAD").strip()
    (git_repo / "mod.py").write_text("a = 1\nb = 22\nc = 3\nd = 4\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "edit line 2")

    out = decay_since(str(git_repo), base, {"mod.py": {1, 2, 3}, "quiet.py": {1, 2}})

    # Line 2 was covered and has changed; 1 and 3 still stand.
    assert (out["mod.py"].confirmed, out["mod.py"].invalidated) == (2, 1)
    # A file nothing touched keeps its whole measurement, and is reported
    # rather than omitted, so "nothing moved" is distinguishable from
    # "not checked".
    assert (out["quiet.py"].confirmed, out["quiet.py"].invalidated) == (2, 0)


def test_decay_since_returns_empty_on_an_unknown_ref(git_repo) -> None:
    """An unresolvable ref must not read as zero drift.

    Zero drift is a freshness claim. When the range cannot be diffed the
    honest answer is no answer, so the caller renders the stored figure
    without a decay line rather than one asserting nothing has moved.
    """
    assert decay_since(str(git_repo), "deadbeef" * 5, {"mod.py": {1}}) == {}


def test_decay_since_short_circuits_on_no_input(git_repo) -> None:
    assert decay_since(str(git_repo), "HEAD", {}) == {}


# --- placing the measurement in history ------------------------------------


def test_measurement_ref_prefers_a_recorded_sha(git_repo) -> None:
    head = _git(git_repo, "rev-parse", "HEAD").strip()
    assert measurement_ref(str(git_repo), head, None) == head


def test_measurement_ref_falls_back_to_the_timestamp(git_repo) -> None:
    """The sha is absent on rows written before the repo carried a head commit.

    The second commit is dated an hour ahead so "now" sits strictly between the
    two, which is the shape the fallback exists for: pick the commit that was
    HEAD when the report was taken, not the one that is HEAD today.
    """
    first = _git(git_repo, "rev-parse", "HEAD").strip()
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    (git_repo / "mod.py").write_text("a = 9\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "later", when=later)

    assert measurement_ref(str(git_repo), None, datetime.now(UTC)) == first


def test_measurement_ref_is_none_when_the_report_predates_all_history(git_repo) -> None:
    """No commit existed when the measurement was taken, so it cannot be placed.

    ``None`` here is load-bearing: ``decay_since`` refuses to run without a
    ref, so the caller renders the stored figure with no drift claim instead of
    one asserting nothing has moved.
    """
    ancient = datetime(2000, 1, 1, tzinfo=UTC)
    assert measurement_ref(str(git_repo), None, ancient) is None


def test_measurement_ref_ignores_an_unknown_sha_and_falls_back(git_repo) -> None:
    resolved = measurement_ref(str(git_repo), "deadbeef" * 5, datetime.now(UTC))
    assert resolved == _git(git_repo, "rev-parse", "HEAD").strip()


def test_measurement_ref_is_none_when_the_measurement_cannot_be_placed(git_repo) -> None:
    assert measurement_ref(str(git_repo), None, None) is None


def test_naive_timestamp_is_read_as_utc_not_local(git_repo) -> None:
    """A SQLite store hands back a naive datetime; git would read it as local.

    Both spellings of the same instant must resolve to the same commit, which
    they do not if the naive one is left for git to localise.
    """
    now = datetime.now(UTC)
    aware = measurement_ref(str(git_repo), None, now)
    naive = measurement_ref(str(git_repo), None, now.replace(tzinfo=None))
    assert aware == naive
