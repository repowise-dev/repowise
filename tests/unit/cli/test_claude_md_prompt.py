"""Regression tests for issue #81.

The CLAUDE.md opt-out prompt used to live inside ``interactive_advanced_config``
and was therefore skipped entirely in full mode, and the answer was not always
threaded back to the writer in every code path. These tests pin the new
behaviour: the dedicated ``interactive_claude_md_prompt`` helper returns
``True`` (i.e. ``no_claude_md=True``) when the user answers ``n`` and ``False``
when they answer ``y`` or accept the default, and the writer's gating function
honours that answer by early-returning before any file is written.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands.init_cmd import _maybe_generate_claude_md
from repowise.cli.ui import interactive_claude_md_prompt


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def test_prompt_returns_true_when_user_says_no() -> None:
    runner = CliRunner()
    with runner.isolation(input="n\n"):
        assert interactive_claude_md_prompt(_silent_console()) is True


def test_prompt_returns_false_when_user_says_yes() -> None:
    runner = CliRunner()
    with runner.isolation(input="y\n"):
        assert interactive_claude_md_prompt(_silent_console()) is False


def test_prompt_returns_false_on_default_enter() -> None:
    runner = CliRunner()
    with runner.isolation(input="\n"):
        # default is "Yes", so empty input should produce no_claude_md=False
        assert interactive_claude_md_prompt(_silent_console()) is False


def test_maybe_generate_skips_write_when_user_opted_out(tmp_path: Path) -> None:
    """When ``no_claude_md=True`` the gating function must not touch the
    .claude directory and must persist the opt-out to config.yaml so future
    ``repowise update`` invocations stay opted out as well."""

    (tmp_path / ".repowise").mkdir()
    claude_dir = tmp_path / ".claude"

    _maybe_generate_claude_md(_silent_console(), tmp_path, no_claude_md=True)

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
        "repowise.cli.commands.init_cmd._write_claude_md_async"
    ) as fake_write:
        _maybe_generate_claude_md(_silent_console(), tmp_path, no_claude_md=False)

    fake_write.assert_not_called()
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
