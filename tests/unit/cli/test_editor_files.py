from __future__ import annotations

from pathlib import Path

from repowise.cli.editor_files import set_editor_file_enabled, should_generate_editor_file


def test_should_generate_editor_file_defaults_enabled(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()

    assert should_generate_editor_file(tmp_path, "agents_md")


def test_should_generate_editor_file_reads_config(tmp_path: Path) -> None:
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "config.yaml").write_text(
        "editor_files:\n  agents_md: false\n",
        encoding="utf-8",
    )

    assert not should_generate_editor_file(tmp_path, "agents_md")


def test_should_generate_editor_file_persists_override(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()

    assert not should_generate_editor_file(tmp_path, "agents_md", override=False)
    assert "agents_md: false" in (tmp_path / ".repowise" / "config.yaml").read_text(
        encoding="utf-8"
    )


def test_set_editor_file_enabled_preserves_existing_keys(tmp_path: Path) -> None:
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "config.yaml").write_text(
        "editor_files:\n  claude_md: false\n",
        encoding="utf-8",
    )

    set_editor_file_enabled(tmp_path, "agents_md", True)

    saved = (repowise_dir / "config.yaml").read_text(encoding="utf-8")
    assert "claude_md: false" in saved
    assert "agents_md: true" in saved
