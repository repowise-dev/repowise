"""Tests for the repair marker that keeps a degraded update recoverable.

``last_sync_commit`` advances whether or not the persist steps succeeded, so a
failed step used to take its commit range with it: nothing revisited those
commits and their data was skipped permanently. The marker holds the old
pointer so the next update re-covers the range.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd.persistence import (
    _REPAIR_MAX_COMMITS,
    record_repair_marker,
    resolve_repair_base,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def linear_repo(tmp_path: Path) -> tuple[Path, list[str]]:
    """A repo with three linear commits; returns (repo, [sha0, sha1, sha2])."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Seed")
    _git(repo, "config", "user.email", "seed@e.com")
    shas = []
    for i in range(3):
        (repo / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    return repo, shas


# --- record_repair_marker -------------------------------------------------


def test_failure_records_the_pointer_the_run_started_from():
    new_state: dict = {"last_sync_commit": "head-sha"}
    record_repair_marker(new_state, {"last_sync_commit": "base-sha"}, ["Git persist"])
    assert new_state["pending_repair"] == {"from_commit": "base-sha", "steps": ["Git persist"]}


def test_consecutive_failures_keep_the_oldest_unrepaired_commit():
    """Three failed runs in a row must still re-cover all three ranges.

    And the marker names one range, so its step list has to name everything
    still unrepaired over that range, not just the latest run's casualties.
    """
    prior = {
        "last_sync_commit": "c1",
        "pending_repair": {"from_commit": "c0", "steps": ["Git persist"]},
    }
    new_state: dict = {"last_sync_commit": "c2"}
    record_repair_marker(new_state, prior, ["Health persist"])
    assert new_state["pending_repair"]["from_commit"] == "c0"
    assert new_state["pending_repair"]["steps"] == ["Git persist", "Health persist"]


def test_a_junk_marker_does_not_crash_the_update():
    """A hand-edited or truncated state file must not take the run down with it."""
    new_state: dict = {"last_sync_commit": "c2"}
    record_repair_marker(new_state, {"last_sync_commit": "c1", "pending_repair": "garbage"}, ["X"])
    assert new_state["pending_repair"] == {"from_commit": "c1", "steps": ["X"]}


def test_clean_run_clears_the_marker():
    new_state: dict = {"pending_repair": {"from_commit": "c0", "steps": ["Git persist"]}}
    record_repair_marker(new_state, {"last_sync_commit": "c1"}, [])
    assert "pending_repair" not in new_state


def test_no_marker_without_a_pointer_to_diff_back_to():
    """A first index has no old pointer, so there is no range to name."""
    new_state: dict = {}
    record_repair_marker(new_state, {}, ["Git persist"])
    assert "pending_repair" not in new_state


# --- resolve_repair_base --------------------------------------------------


def test_marker_widens_the_base_back_to_the_unrepaired_range(linear_repo):
    repo, shas = linear_repo
    state = {"pending_repair": {"from_commit": shas[0], "steps": ["Git persist"]}}
    base, give_up = resolve_repair_base(repo, state, shas[1], shas[2])
    assert (base, give_up) == (shas[0], None)


def test_no_marker_leaves_the_base_alone(linear_repo):
    repo, shas = linear_repo
    base, give_up = resolve_repair_base(repo, {}, shas[1], shas[2])
    assert (base, give_up) == (shas[1], None)


def test_a_junk_marker_is_ignored_rather_than_raising(linear_repo):
    repo, shas = linear_repo
    base, give_up = resolve_repair_base(repo, {"pending_repair": "garbage"}, shas[1], shas[2])
    assert (base, give_up) == (shas[1], None)


def test_marker_that_caught_up_is_a_no_op(linear_repo):
    repo, shas = linear_repo
    state = {"pending_repair": {"from_commit": shas[1], "steps": ["Git persist"]}}
    base, give_up = resolve_repair_base(repo, state, shas[1], shas[2])
    assert (base, give_up) == (shas[1], None)


def test_commit_that_left_the_history_is_given_up_on(linear_repo):
    """A rebased or gc'd marker must never move the base forward, so it is dropped."""
    repo, shas = linear_repo
    state = {"pending_repair": {"from_commit": "0" * 40, "steps": ["Git persist"]}}
    base, give_up = resolve_repair_base(repo, state, shas[1], shas[2])
    assert base == shas[1]
    assert give_up is not None and "not present in this clone" in give_up


def test_non_ancestor_marker_is_given_up_on(linear_repo):
    """A base older than the marker (docs mode, a branch switch) must win."""
    repo, shas = linear_repo
    state = {"pending_repair": {"from_commit": shas[2], "steps": ["Git persist"]}}
    base, give_up = resolve_repair_base(repo, state, shas[0], shas[2])
    assert base == shas[0]
    assert give_up is not None


def test_window_past_the_cap_is_given_up_on(linear_repo, monkeypatch):
    """The bound is what stops a step that fails every run from pinning the window open."""
    repo, shas = linear_repo
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence._REPAIR_MAX_COMMITS", 1
    )
    state = {"pending_repair": {"from_commit": shas[0], "steps": ["Git persist"]}}
    base, give_up = resolve_repair_base(repo, state, shas[1], shas[2])
    assert base == shas[1]
    assert give_up is not None and "re-index rather than a repair" in give_up


def test_cap_is_a_real_bound():
    assert _REPAIR_MAX_COMMITS > 0
