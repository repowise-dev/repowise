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
    def test_section_present(self, rendered: str) -> None:
        assert "### Output Distillation" in rendered

    def test_teaches_distill_command(self, rendered: str) -> None:
        assert "repowise distill <cmd>" in rendered

    def test_teaches_marker_expansion(self, rendered: str) -> None:
        # Marker semantics: see a [repowise#<ref>: ...] marker → expand it.
        assert "[repowise#" in rendered
        assert "repowise expand <ref>" in rendered

    def test_discourages_rerunning(self, rendered: str) -> None:
        assert "Never re-run" in rendered
