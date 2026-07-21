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
    assert any("update --full" in c for c in cmds)  # the upgrade-to-prose hint


def test_next_steps_index_only_recommends_serve_as_first_step():
    """`repowise serve` (the dashboard) is the headline next step after an
    index-only run — MCP is already auto-registered by init."""
    steps = build_contextual_next_steps(index_only=True, fast_mode=False)
    assert steps[0][0] == "repowise serve"


def test_next_steps_full_mode():
    steps = build_contextual_next_steps(index_only=False)
    cmds = [c for c, _ in steps]
    assert any("search" in c for c in cmds)


def test_next_steps_rendered_lines_have_space_before_description():
    """Regression: long commands like ``repowise init --provider gemini``
    (>28 chars) used to run straight into the description because the format
    spec only padded *up to* 28 columns. Every rendered line must have at
    least one space between the command and its description."""
    from repowise.cli.ui.result_panels import _render_what_next_lines

    steps = build_contextual_next_steps(index_only=True, fast_mode=False)
    lines = _render_what_next_lines(steps)
    for cmd, desc in steps:
        matching = [line for line in lines if cmd in line and desc in line]
        assert matching, f"expected a rendered line for ({cmd!r}, {desc!r})"
        line = matching[0]
        idx_cmd_end = line.index(cmd) + len(cmd)
        idx_desc = line.index(desc, idx_cmd_end)
        gap = line[idx_cmd_end:idx_desc]
        assert gap.strip() == "", f"unexpected non-space chars between cmd and desc: {gap!r}"
        assert len(gap) >= 1, f"no separator between {cmd!r} and {desc!r}: {line!r}"
