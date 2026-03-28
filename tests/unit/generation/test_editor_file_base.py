"""Unit tests for BaseEditorFileGenerator marker-merge logic."""

from __future__ import annotations

import pytest

from repowise.core.generation.editor_files.base import BaseEditorFileGenerator
from repowise.core.generation.editor_files.data import EditorFileData

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class _TestGenerator(BaseEditorFileGenerator):
    filename = "TEST.md"
    marker_tag = "REPOWISE"
    template_name = "claude_md.j2"
    user_placeholder = "# TEST.md\n\n<!-- user content here -->\n"


def _minimal_data() -> EditorFileData:
    return EditorFileData(
        repo_name="test-repo",
        indexed_at="2026-03-28",
        architecture_summary="A test repo.",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def gen():
    return _TestGenerator()


def test_render_returns_non_empty_string(gen):
    data = _minimal_data()
    result = gen.render(data)
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_contains_repo_name(gen):
    data = _minimal_data()
    result = gen.render(data)
    assert "test-repo" in result


def test_write_creates_new_file(gen, tmp_path):
    data = _minimal_data()
    written = gen.write(tmp_path, data)
    assert written.exists()
    content = written.read_text(encoding="utf-8")
    assert "<!-- REPOWISE:START" in content
    assert "<!-- REPOWISE:END -->" in content
    assert "<!-- user content here -->" in content  # placeholder present


def test_write_new_file_has_correct_structure(gen, tmp_path):
    data = _minimal_data()
    gen.write(tmp_path, data)
    content = (tmp_path / "TEST.md").read_text(encoding="utf-8")
    start_idx = content.index("<!-- REPOWISE:START")
    end_idx = content.index("<!-- REPOWISE:END -->")
    assert start_idx < end_idx
    # User placeholder is BEFORE the markers
    placeholder_idx = content.index("<!-- user content here -->")
    assert placeholder_idx < start_idx


def test_write_preserves_user_content_when_no_markers(gen, tmp_path):
    target = tmp_path / "TEST.md"
    user_content = "# My Project\n\nDo not touch this content!\n"
    target.write_text(user_content, encoding="utf-8")

    data = _minimal_data()
    gen.write(tmp_path, data)

    content = target.read_text(encoding="utf-8")
    assert "Do not touch this content!" in content
    assert "<!-- REPOWISE:START" in content


def test_write_appends_when_no_markers(gen, tmp_path):
    target = tmp_path / "TEST.md"
    target.write_text("# Existing\n\nsome content", encoding="utf-8")

    gen.write(tmp_path, _minimal_data())
    content = target.read_text(encoding="utf-8")

    # Existing content is BEFORE the markers
    existing_idx = content.index("# Existing")
    marker_idx = content.index("<!-- REPOWISE:START")
    assert existing_idx < marker_idx


def test_write_replaces_between_markers(gen, tmp_path):
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    target = tmp_path / "TEST.md"
    old_content = (
        "# My notes\n\nUser section.\n\n"
        f"{marker_start}\n## Old Content\nOld managed content.\n{marker_end}\n"
    )
    target.write_text(old_content, encoding="utf-8")

    gen.write(tmp_path, _minimal_data())
    new_content = target.read_text(encoding="utf-8")

    assert "User section." in new_content
    assert "Old managed content." not in new_content
    assert "test-repo" in new_content  # new managed content present


def test_write_replaces_only_managed_section(gen, tmp_path):
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    target = tmp_path / "TEST.md"
    target.write_text(
        f"# User notes\n\nKeep this.\n\n{marker_start}\nold\n{marker_end}\n",
        encoding="utf-8",
    )

    gen.write(tmp_path, _minimal_data())
    content = target.read_text(encoding="utf-8")

    assert "Keep this." in content
    assert content.count("<!-- REPOWISE:START") == 1
    assert content.count("<!-- REPOWISE:END -->") == 1


def test_write_is_idempotent(gen, tmp_path):
    data = _minimal_data()
    gen.write(tmp_path, data)
    first = (tmp_path / "TEST.md").read_text(encoding="utf-8")

    gen.write(tmp_path, data)
    second = (tmp_path / "TEST.md").read_text(encoding="utf-8")

    assert first == second


def test_write_returns_path(gen, tmp_path):
    result = gen.write(tmp_path, _minimal_data())
    assert result == tmp_path / "TEST.md"


def test_render_full_matches_write_output(gen, tmp_path):
    data = _minimal_data()
    preview = gen.render_full(tmp_path, data)
    gen.write(tmp_path, data)
    written = (tmp_path / "TEST.md").read_text(encoding="utf-8")
    assert preview == written
