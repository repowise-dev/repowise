"""Editor files written while init's progress display is live.

``init`` writes ``.claude/CLAUDE.md`` and ``AGENTS.md`` inside its progress
list. Their own spinner opened a second live display on the same console,
Rich raised, and the file was skipped.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from repowise.cli.editor_integrations import claude
from repowise.cli.ui.mascot import owl_status
from repowise.cli.ui.progress import progress_steps


@pytest.fixture
def console() -> Console:
    return Console(file=io.StringIO(), force_terminal=True, width=100)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def test_owl_status_inside_a_live_display_runs_its_body(console: Console) -> None:
    ran = False
    with progress_steps(console), owl_status(console, "working"):
        ran = True
    assert ran


def test_owl_status_alone_still_shows_a_spinner(console: Console) -> None:
    with owl_status(console, "working") as live:
        assert live is not None


def test_claude_md_is_written_under_the_init_progress_display(
    console: Console, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    target = repo / ".claude" / "CLAUDE.md"

    async def _write(repo_path: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# CLAUDE.md\n", encoding="utf-8")
        return target

    monkeypatch.setattr(claude, "_write_claude_md_async", _write)

    with progress_steps(console):
        written = claude.maybe_generate_claude_md(console, repo)

    assert written == target
    assert target.is_file()
    assert "skipped" not in console.file.getvalue()
