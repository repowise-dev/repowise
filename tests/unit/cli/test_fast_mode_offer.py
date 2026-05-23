"""Tests for the large-repo fast-mode offer helpers in cli.ui."""

from __future__ import annotations

from repowise.cli.ui import (
    LARGE_REPO_FILE_THRESHOLD,
    RepoScanInfo,
    build_contextual_next_steps,
    should_offer_fast_mode,
)


def test_should_offer_fast_mode_none():
    assert should_offer_fast_mode(None) is False


def test_should_offer_fast_mode_small_repo():
    assert should_offer_fast_mode(RepoScanInfo(total_files=100)) is False


def test_should_offer_fast_mode_at_threshold():
    # Threshold is strict ">" — exactly at the boundary does not offer.
    assert should_offer_fast_mode(RepoScanInfo(total_files=LARGE_REPO_FILE_THRESHOLD)) is False


def test_should_offer_fast_mode_large_repo():
    assert should_offer_fast_mode(RepoScanInfo(total_files=LARGE_REPO_FILE_THRESHOLD + 1)) is True


def test_next_steps_fast_mode_leads_with_full_upgrade():
    steps = build_contextual_next_steps(index_only=True, fast_mode=True)
    cmds = [c for c, _ in steps]
    descs = " ".join(d for _, d in steps).lower()
    # Fast mode tells the user how to get the full result.
    assert cmds[0] == "repowise init"
    assert "full" in descs and "docs" in descs


def test_next_steps_index_only_without_fast():
    steps = build_contextual_next_steps(index_only=True, fast_mode=False)
    cmds = [c for c, _ in steps]
    assert any("--provider" in c for c in cmds)  # the existing generate hint


def test_next_steps_full_mode():
    steps = build_contextual_next_steps(index_only=False)
    cmds = [c for c, _ in steps]
    assert any("search" in c for c in cmds)
