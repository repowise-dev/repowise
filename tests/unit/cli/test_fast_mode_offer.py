"""Tests for the large-repo fast-mode offer helpers in cli.ui."""

from __future__ import annotations

from repowise.cli.editor_setup import EditorSetupOutcome
from repowise.cli.ui import (
    LARGE_REPO_FILE_THRESHOLD,
    RepoScanInfo,
    build_contextual_next_steps,
    keyless_upgrade_note,
    should_offer_fast_mode,
)
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.finish_panel import connected_lines


def build_status_notes(setup) -> list[str]:
    """The Connected lines as plain text."""
    from rich.console import Console

    console = Console(width=200, record=True, theme=THEME)
    for line in connected_lines(setup):
        console.print(line)
    return [line for line in console.export_text().splitlines() if line.strip()]


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
    # After serve, fast mode points at the one fast-to-full command, keyless.
    assert cmds[1] == "repowise update --full"
    assert "documentation" in descs and "no key" in descs


def test_next_steps_index_only_upgrades_via_generate_not_update_full():
    steps = build_contextual_next_steps(index_only=True, fast_mode=False)
    cmds = [c for c, _ in steps]
    assert any(c == "repowise generate" for c in cmds)  # the scoped upgrade path
    assert not any("update --full" in c for c in cmds)  # never the all-or-nothing path


def test_next_steps_full_mode():
    steps = build_contextual_next_steps(index_only=False)
    cmds = [c for c, _ in steps]
    assert any("search" in c for c in cmds)


def test_next_steps_are_serve_and_at_most_two_more():
    """The panel names the dashboard and at most two moves, never a pile."""
    for kwargs in (
        {"index_only": True, "fast_mode": True},
        {"index_only": True, "fast_mode": False},
        {"index_only": False},
        {"index_only": False, "dead_unreachable": 12, "dead_unused": 30},
        {"index_only": False, "hotspot_count": 4, "top_hotspot": "a.py"},
        {"index_only": False, "decision_count": 9},
    ):
        steps = build_contextual_next_steps(**kwargs)
        assert steps[0][0] == "repowise serve"
        assert 2 <= len(steps) <= 3


def test_next_steps_findings_rank_below_finishing_the_index():
    """A partial index is a worse problem than an unexplored finding."""
    steps = build_contextual_next_steps(
        index_only=True, fast_mode=False, dead_unreachable=40, decision_count=9
    )
    assert steps[1][0] == "repowise generate"

    # With the index complete, the largest finding gets the row.
    found = build_contextual_next_steps(
        index_only=False, dead_unreachable=12, dead_unused=30, decision_count=9
    )
    assert found[1][0] == "repowise dead-code"


def test_next_steps_fall_back_to_search_when_nothing_was_found():
    steps = build_contextual_next_steps(index_only=False)
    assert steps[1][0].startswith("repowise search")


def test_next_steps_headless_run_gets_manual_mcp_row():
    """A skipped-setup run (CI/headless) can't auto-wire a client, so the panel
    offers the manual connect command naming the real clients."""
    setup = EditorSetupOutcome(editor_setup_disabled=True, claude_code_connected=False)
    cmds = [c for c, _ in build_contextual_next_steps(index_only=True, setup=setup)]
    assert "repowise mcp ." in cmds
    # It outranks the index-only upgrade row: `--no-editor-setup --index-only`
    # is the CI shape, and the Connected lines carry no command for a disabled run,
    # so this row is the only place the connect instructions exist.
    assert cmds[1] == "repowise mcp ."

    # Fast mode too, for the same reason.
    fast = [
        c
        for c, _ in build_contextual_next_steps(index_only=True, fast_mode=True, setup=setup)
    ]
    assert fast[1] == "repowise mcp ."
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
    notes = " ".join(build_status_notes(setup))
    assert "repowise hook rewrite install" in notes
    # ...and they are notes, not command rows.
    cmds = [c for c, _ in build_contextual_next_steps(index_only=True, setup=setup)]
    assert not any(c.startswith("repowise hook") for c in cmds)

    # Already installed → not re-suggested.
    setup_installed = EditorSetupOutcome(
        interactive=False,
        autosync_hook_installed=True,
        rewrite_hook_installed=True,
        claude_code_connected=True,
    )
    assert not any("repowise hook" in n for n in build_status_notes(setup_installed))


def test_next_steps_interactive_run_does_not_nag_about_hooks():
    """An interactive run was already asked about both hooks live, so the panel
    stays quiet about them even when missing."""
    setup = EditorSetupOutcome(
        interactive=True,
        autosync_hook_installed=False,
        rewrite_hook_installed=False,
        claude_code_connected=True,
    )
    assert not any("repowise hook" in n for n in build_status_notes(setup))


def test_mcp_status_lines_restart_note_only_on_first_index():
    first = EditorSetupOutcome(claude_code_connected=True, first_index=True)
    text = " ".join(build_status_notes(first)).lower()
    assert "restart" in text and "claude code" in text
    assert "cursor" in text and "codex" in text  # others are pointed the way too

    rerun = EditorSetupOutcome(claude_code_connected=True, first_index=False)
    rerun_text = " ".join(build_status_notes(rerun)).lower()
    assert "stays connected" in rerun_text


def test_mcp_status_lines_empty_when_absent_and_say_skipped_when_headless():
    assert build_status_notes(None) == []
    headless = " ".join(build_status_notes(EditorSetupOutcome(editor_setup_disabled=True)))
    assert "editor setup was skipped" in headless
    assert "restart" not in headless.lower()


def test_next_steps_render_without_truncation_or_collision():
    """Regression, rehomed: a command longer than the gutter used to run into
    its own description because the format spec only padded *up to* 28 columns.
    Rich owns the column now, so the check is that both halves survive rendering
    intact rather than that a pad was wide enough."""
    from rich.console import Console

    from repowise.cli.ui import build_completion_panel

    steps = [("repowise hook rewrite install", "compress noisy command output")]
    console = Console(width=120, record=True, force_terminal=True, theme=THEME)
    console.print(build_completion_panel("done", [("Files", "1")], next_steps=steps))
    out = console.export_text()
    assert "repowise hook rewrite install" in out
    assert "compress noisy command output" in out


def test_keyless_upgrade_note_names_both_commands_by_default():
    note = keyless_upgrade_note(semantic_available=False)
    assert "repowise generate" in note
    assert "--provider ollama" in note
    assert "repowise reindex adds semantic search" in note
    assert "..." not in note  # a user must be able to copy-paste it


def test_keyless_upgrade_note_omits_search_half_when_already_available():
    note = keyless_upgrade_note(semantic_available=True)
    assert "repowise generate" in note
    assert "reindex" not in note
    assert "embedder" not in note


def test_keyless_upgrade_note_uses_the_scope_s_own_next_command():
    note = keyless_upgrade_note(
        semantic_available=False, reindex_command="repowise reindex --embedder ollama"
    )
    assert "repowise reindex --embedder ollama" in note


def test_keyless_upgrade_note_falls_back_without_a_next_command():
    note = keyless_upgrade_note(semantic_available=False, reindex_command=None)
    assert "repowise reindex adds semantic search" in note
    assert "..." not in note
