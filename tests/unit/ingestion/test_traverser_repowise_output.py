"""The traverser skips files repowise wrote whole, and keeps shared files a user wrote in."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
from repowise.core.generation.editor_files.base import BaseEditorFileGenerator as Gen
from repowise.core.generation.editor_files.claude_md import ClaudeMdGenerator
from repowise.core.ingestion.traverser import FileTraverser


def _block(generator: type[Gen]) -> str:
    start = Gen.MARKER_START_FMT.format(tag=generator.marker_tag)
    end = Gen.MARKER_END_FMT.format(tag=generator.marker_tag)
    return f"{start}\n## Repowise\nindexed facts\n{end}"


def _new_file(generator: type[Gen]) -> str:
    """What the generator writes when the file does not exist yet."""
    return f"{generator.user_placeholder}\n{_block(generator)}\n"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _indexed(root: Path) -> tuple[set[str], FileTraverser]:
    traverser = FileTraverser(root)
    return {fi.path for fi in traverser.traverse()}, traverser


def test_files_repowise_wrote_whole_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".claude/CLAUDE.md", _new_file(ClaudeMdGenerator))
    _write(tmp_path, "AGENTS.md", _new_file(AgentsMdGenerator))
    _write(
        tmp_path,
        ".mcp.json",
        json.dumps({"mcpServers": {"repowise": {"command": "repowise", "args": ["mcp"]}}}),
    )
    _write(tmp_path, ".kiro/steering/repowise.md", "# Repowise\n")

    paths, traverser = _indexed(tmp_path)

    assert paths == {"app.py"}
    assert traverser.stats.skipped_repowise_output == 4


def test_a_users_own_content_keeps_the_file_indexed(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "x = 1\n")
    _write(
        tmp_path,
        ".claude/CLAUDE.md",
        f"# My rules\n\nRun pytest.\n\n{_block(ClaudeMdGenerator)}\n",
    )
    _write(
        tmp_path,
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "repowise": {"command": "repowise", "args": ["mcp"]},
                    "github": {"command": "gh-mcp"},
                }
            }
        ),
    )

    paths, traverser = _indexed(tmp_path)

    assert paths == {"app.py", ".claude/CLAUDE.md", ".mcp.json"}
    assert traverser.stats.skipped_repowise_output == 0


def test_only_the_root_path_is_repowise_output(tmp_path: Path) -> None:
    """A nested ``AGENTS.md`` is not one repowise writes, whatever it holds."""
    _write(tmp_path, "pkg/AGENTS.md", _new_file(AgentsMdGenerator))

    paths, _ = _indexed(tmp_path)

    assert paths == {"pkg/AGENTS.md"}
