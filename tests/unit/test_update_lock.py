"""Tests for the update-lock single-flight guard."""
from __future__ import annotations

import os
import time

from repowise.core import update_lock as ul
from repowise.core.procutils import process_create_token


def _live_payload(age_seconds: float) -> dict:
    return {
        "pid": os.getpid(),
        "pid_create_token": process_create_token(os.getpid()),
        "target_commit": "abc123",
        "started_at": time.time() - age_seconds,
    }


def test_try_acquire_returns_none_when_acquired(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = ul.try_acquire_update_lock(repo, "deadbeef")
    assert result is None
    assert ul.update_lock_path(repo).exists()
    ul.release_update_lock(repo)
    assert not ul.update_lock_path(repo).exists()


def test_try_acquire_reports_live_owner(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert ul.try_acquire_update_lock(repo, "deadbeef") is None
    # A second attempt on the same repo sees the live owner's payload.
    owner = ul.try_acquire_update_lock(repo, "deadbeef")
    assert owner is not None
    assert owner["pid"] == os.getpid()
    ul.release_update_lock(repo)


def test_format_lock_age_covers_all_bands():
    assert ul.format_lock_age(None) == "for an unknown time"
    assert ul.format_lock_age(0) == "for 0s"
    assert ul.format_lock_age(5) == "for 5s"
    assert ul.format_lock_age(89) == "for 89s"
    assert ul.format_lock_age(90) == "for 1m"
    assert ul.format_lock_age(89 * 60) == "for 89m"
    assert ul.format_lock_age(90 * 60) == "for 1.5h"


def test_lock_age_seconds_handles_missing_and_future():
    assert ul.lock_age_seconds(None) is None
    assert ul.lock_age_seconds({}) is None
    assert ul.lock_age_seconds({"started_at": "nope"}) is None
    # A future timestamp still yields a non-negative age.
    assert ul.lock_age_seconds({"started_at": time.time() + 1000.0}) == 0.0


def test_lock_is_suspect_false_for_missing_payload():
    assert ul.lock_is_suspect(None) is False
    assert ul.lock_is_suspect({}) is False
    assert ul.lock_is_suspect({"pid": "not-an-int"}) is False
    assert ul.lock_is_suspect({"pid": -1}) is False


def test_lock_is_suspect_false_when_recently_acquired():
    assert ul.lock_is_suspect(_live_payload(60)) is False


def test_lock_is_suspect_true_when_held_past_window():
    # Held for an hour, owner still provably alive -> suspect.
    assert ul.lock_is_suspect(_live_payload(60 * 60)) is True


def test_lock_is_suspect_false_for_dead_owner():
    # A PID that cannot exist is provably dead -> broken, not suspect.
    assert ul.lock_is_suspect(
        {
            "pid": 9_999_999,
            "pid_create_token": "",
            "started_at": time.time() - 60 * 60,
        }
    ) is False


def test_lock_is_suspect_false_for_recycled_pid():
    # Wrong create token means the PID was recycled by an unrelated process.
    assert ul.lock_is_suspect(
        {
            "pid": os.getpid(),
            "pid_create_token": "definitely-not-our-token",
            "started_at": time.time() - 60 * 60,
        }
    ) is False
