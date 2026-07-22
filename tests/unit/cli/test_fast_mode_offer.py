"""Tests for the large-repo fast-mode offer helpers in cli.ui."""

from __future__ import annotations

from repowise.cli.editor_setup import EditorSetupOutcome
from repowise.cli.ui import (
    LARGE_REPO_FILE_THRESHOLD,
    RepoScanInfo,
    build_contextual_next_steps,
    build_mcp_status_lines,
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


def test_next_steps_serve_is_the_headline_in_every_mode():
    """`repowise serve` (the dashboard) leads in fast, index-only and full — the
    one place the graph, hotspots, dead code, decisions and wiki all live."""
    for kwargs in (
        {"index_only": True, "fast_mode": True},
        {"index_only": True, "fast_mode": False},
        {"index_only": False},
    ):
        steps = build_contextual_next_steps(**kwargs)
        assert steps[0][0] == "repowise serve"


def test_next_steps_fast_mode_second_row_is_full_upgrade():
    steps = build_contextual_next_steps(index_only=True, fast_mode=True)
    cmds = [c for c, _ in steps]
    descs = " ".join(d for _, d in steps).lower()
    # After serve, fast mode points at the full result.
    assert cmds[1] == "repowise init"
    assert "full" in descs and "wiki" in descs


def test_next_steps_index_only_upgrades_via_generate_not_update_full():
    steps = build_contextual_next_steps(index_only=True, fast_mode=False)
    cmds = [c for c, _ in steps]
    assert any(c == "repowise generate" for c in cmds)  # the scoped upgrade path
    assert not any("update --full" in c for c in cmds)  # never the all-or-nothing path


def test_next_steps_full_mode():
    steps = build_contextual_next_steps(index_only=False)
    cmds = [c for c, _ in steps]
    assert any("search" in c for c in cmds)


def test_next_steps_headless_run_gets_manual_mcp_row():
    """A skipped-setup run (CI/headless) can't auto-wire a client, so the panel
    offers the manual connect command naming the real clients."""
    setup = EditorSetupOutcome(editor_setup_disabled=True, claude_code_connected=False)
    cmds = [c for c, _ in build_contextual_next_steps(index_only=True, setup=setup)]
    assert "repowise mcp ." in cmds
    # A skip-setup run opted out of all wiring, so it is never nagged to install
    # hooks even though it is non-interactive with none present.
    assert not any(c.startswith("repowise hook") for c in cmds)

    # A normally-wired run never shows the manual MCP command row.
    wired = EditorSetupOutcome(claude_code_connected=True, interactive=True)
    cmds_wired = [c for c, _ in build_contextual_next_steps(index_only=True, setup=wired)]
    assert "repowise mcp ." not in cmds_wired


def test_next_steps_non_interactive_surfaces_missing_hooks():
    """When the run couldn't prompt, the skipped hook offers surface as rows —
    but only for hooks that are actually missing."""
    setup = EditorSetupOutcome(
        interactive=False,
        autosync_hook_installed=False,
        rewrite_hook_installed=False,
        claude_code_connected=True,
    )
    cmds = [c for c, _ in build_contextual_next_steps(index_only=True, setup=setup)]
    assert "repowise hook install" in cmds
    assert "repowise hook rewrite install" in cmds

    # Already installed → not re-suggested.
    setup_installed = EditorSetupOutcome(
        interactive=False,
        autosync_hook_installed=True,
        rewrite_hook_installed=True,
        claude_code_connected=True,
    )
    cmds2 = [c for c, _ in build_contextual_next_steps(index_only=True, setup=setup_installed)]
    assert not any(c.startswith("repowise hook") for c in cmds2)


def test_next_steps_interactive_run_does_not_nag_about_hooks():
    """An interactive run was already asked about both hooks live, so the panel
    stays quiet about them even when missing."""
    setup = EditorSetupOutcome(
        interactive=True,
        autosync_hook_installed=False,
        rewrite_hook_installed=False,
        claude_code_connected=True,
    )
    cmds = [c for c, _ in build_contextual_next_steps(index_only=True, setup=setup)]
    assert not any(c.startswith("repowise hook") for c in cmds)


def test_mcp_status_lines_restart_note_only_on_first_index():
    first = EditorSetupOutcome(claude_code_connected=True, first_index=True)
    text = " ".join(build_mcp_status_lines(first)).lower()
    assert "restart" in text and "claude code" in text
    assert "cursor" in text and "codex" in text  # others are pointed the way too

    rerun = EditorSetupOutcome(claude_code_connected=True, first_index=False)
    rerun_text = " ".join(build_mcp_status_lines(rerun)).lower()
    assert "stays connected" in rerun_text


def test_mcp_status_lines_empty_when_headless_or_absent():
    assert build_mcp_status_lines(None) == []
    assert build_mcp_status_lines(EditorSetupOutcome(editor_setup_disabled=True)) == []


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
