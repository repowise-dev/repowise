"""Resume should continue the prior run's git tier (issue #341).

A fast (ESSENTIAL-tier) index resumed without re-passing ``--mode fast``
must not silently fall back to STANDARD and redo the expensive FULL git
indexing the first run deliberately skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.init_cmd import (
    _effective_run_mode_for_resume,
    _git_tier_for_run_mode,
)
from repowise.cli.helpers import save_state


def test_git_tier_for_run_mode():
    assert _git_tier_for_run_mode("fast") == "essential"
    assert _git_tier_for_run_mode("standard") == "full"


def test_resume_restores_fast_run_mode(tmp_path: Path):
    save_state(tmp_path, {"run_mode": "fast", "git_tier": "essential"})
    # Default CLI run_mode is "standard"; resume should restore "fast".
    assert _effective_run_mode_for_resume(tmp_path, "standard", resume=True) == "fast"


def test_resume_keeps_standard_when_prior_was_standard(tmp_path: Path):
    save_state(tmp_path, {"run_mode": "standard", "git_tier": "full"})
    assert _effective_run_mode_for_resume(tmp_path, "standard", resume=True) == "standard"


def test_no_resume_is_a_noop(tmp_path: Path):
    save_state(tmp_path, {"run_mode": "fast"})
    # Without --resume we honour the invocation's mode, not persisted state.
    assert _effective_run_mode_for_resume(tmp_path, "standard", resume=False) == "standard"


def test_explicit_fast_on_resume_still_wins(tmp_path: Path):
    save_state(tmp_path, {"run_mode": "standard"})
    assert _effective_run_mode_for_resume(tmp_path, "fast", resume=True) == "fast"


def test_resume_without_prior_state_falls_back_to_invocation(tmp_path: Path):
    # No state.json at all → keep whatever the invocation requested.
    assert _effective_run_mode_for_resume(tmp_path, "standard", resume=True) == "standard"


@pytest.mark.parametrize("bad", [None, "", "weird"])
def test_resume_ignores_unknown_persisted_mode(tmp_path: Path, bad):
    save_state(tmp_path, {"run_mode": bad} if bad is not None else {})
    assert _effective_run_mode_for_resume(tmp_path, "standard", resume=True) == "standard"
