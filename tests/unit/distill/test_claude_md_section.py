"""The generated CLAUDE.md must teach agents the distill marker semantics."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from repowise.core.generation.editor_files.data import EditorFileData

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "src"
    / "repowise"
    / "core"
    / "generation"
    / "templates"
)


@pytest.fixture
def rendered() -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
    data = EditorFileData(
        repo_name="test",
        indexed_at="2026-06-05",
        indexed_commit="abc123",
        architecture_summary="",
    )
    return env.get_template("claude_md.j2").render(data=data)


class TestDistillSection:
    def test_teaches_distill_command(self, rendered: str) -> None:
        assert "repowise distill <cmd>" in rendered

    def test_the_marker_itself_names_its_restore_command(self) -> None:
        # The block names the command once; each marker carries how to restore
        # what it omitted, so the block does not repeat it on every turn.
        from repowise.core.distill.markers import _MARKER_TEMPLATE

        assert "restore: repowise expand {ref}" in _MARKER_TEMPLATE

    def test_hosts_without_a_hook_get_the_full_marker_rules(self) -> None:
        from repowise.cli.agent_targets.instructions import DISTILL_SECTION

        assert "[repowise#" in DISTILL_SECTION
        assert "repowise expand <ref>" in DISTILL_SECTION
        assert "Never re-run a command" in DISTILL_SECTION
