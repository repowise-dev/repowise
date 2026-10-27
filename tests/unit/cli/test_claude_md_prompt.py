"""Regression tests for issue #81.

The CLAUDE.md opt-out prompt used to live inside ``interactive_advanced_config``
and was therefore skipped entirely in full mode, and the answer was not always
threaded back to the writer in every code path. These tests pin the property
that fixed it, which survives the prompt itself moving twice: whatever the user
answers reaches the writer, and the writer persists the opt-out.

The answer now comes from the one agent checklist rather than from a yes/no the
Claude integration owned. The checklist's own behaviour is tested in
``test_editor_setup``; what is tested here is the second half — that unticking
Claude Code still ends in no CLAUDE.md and a persisted opt-out.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from repowise.cli.editor_integrations import claude as claude_integration


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def test_unticking_claude_code_reaches_the_writer(monkeypatch, tmp_path: Path) -> None:
    """The whole path: checklist answer -> options -> the writer's opt-out."""
    from repowise.cli.editor_setup import select_agents_interactively
    from repowise.cli.ui import agent_selection

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(agent_selection, "interactive_agent_select", lambda *_a: set())

    options = select_agents_interactively(_silent_console(), tmp_path, _empty_options())
    claude_integration.ClaudeCodeSetup().write_project_files(
        _silent_console(), tmp_path, options
    )

    assert not (tmp_path / ".claude").exists()
    cfg = (tmp_path / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    assert "claude_md: false" in cfg


def _empty_options():
    from repowise.cli.editor_setup import EditorSetupOptions

    return EditorSetupOptions()


def test_maybe_generate_skips_write_when_user_opted_out(tmp_path: Path) -> None:
    """When ``no_claude_md=True`` the gating function must not touch the
    .claude directory and must persist the opt-out to config.yaml so future
    ``repowise update`` invocations stay opted out as well."""

    (tmp_path / ".repowise").mkdir()
    claude_dir = tmp_path / ".claude"

    claude_integration.maybe_generate_claude_md(
        _silent_console(), tmp_path, no_claude_md=True
    )

    # No .claude directory and no CLAUDE.md should have been created.
    assert not claude_dir.exists()
    assert not (claude_dir / "CLAUDE.md").exists()

    # Opt-out must be persisted to .repowise/config.yaml so that subsequent
    # commands (e.g. `repowise update`) also skip CLAUDE.md generation.
    cfg_path = tmp_path / ".repowise" / "config.yaml"
    assert cfg_path.exists()
    contents = cfg_path.read_text(encoding="utf-8")
    assert "claude_md: false" in contents


def test_maybe_generate_skips_write_when_config_disabled(tmp_path: Path) -> None:
    """If the persisted opt-out is already in config.yaml from a previous run,
    the writer must respect it even when ``no_claude_md`` is False."""

    (tmp_path / ".repowise").mkdir()
    cfg_path = tmp_path / ".repowise" / "config.yaml"
    cfg_path.write_text("editor_files:\n  claude_md: false\n", encoding="utf-8")

    # Patch the writer to detect any unexpected call.
    with patch(
        "repowise.cli.editor_integrations.claude._write_claude_md_async"
    ) as fake_write:
        claude_integration.maybe_generate_claude_md(
            _silent_console(), tmp_path, no_claude_md=False
        )

    fake_write.assert_not_called()
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
